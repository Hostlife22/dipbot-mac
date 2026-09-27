from __future__ import annotations

import queue
import threading
import time
from collections.abc import Callable
from dataclasses import asdict
from typing import Any, TypeVar, cast

from PySide6.QtCore import QThread, Signal

from dipbot.application import commands, market_observation, positions
from dipbot.application.errors import safe_error
from dipbot.application.messages import Command, CommandKind, Event, EventKind, StatusPayload
from dipbot.application.ports import StateStore
from dipbot.application.state import Connections, MarketState, RecordingState, SessionState, StateAccess
from dipbot.application.sweep import SweepService
from dipbot.application.trade_view import entry_view
from dipbot.domain.entry_guard import EntryRejected
from dipbot.domain.records import PositionRecord, TradeDetail
from dipbot.domain.strategy import D
from dipbot.execution.accounting import (
    RateBook,
    closed_summary,
)
from dipbot.market.chain import Chain, Pool
from dipbot.market.exit_reads import ExitReadCancelled
from dipbot.observability.cycle_trace import head_context, mark


class Worker(QThread, StateAccess):
    log = Signal(str)
    event = Signal(str, object)  # type: ignore[assignment]  # Existing Qt signal API shadows QObject.event.

    def __init__(self, store: StateStore) -> None:
        super().__init__()
        self.connections = Connections()
        self.market = MarketState()
        self.session = SessionState()
        self.recording = RecordingState()
        self.position_watch_error = ""
        self.exit_return: str | None = None
        self.store = store
        self.commands: queue.Queue[Command] = queue.Queue()
        self.quit_event = threading.Event()
        self.stop_event = threading.Event()
        self.rates = RateBook()

    def discovery_current(self, generation: int | None) -> bool:
        return (generation is None or generation == self.discovery_generation) and not (
            self.stop_event.is_set() or self.quit_event.is_set()
        )

    def discovery_emit(self, generation: int | None, name: str | EventKind, value: Any) -> None:
        if generation is None:
            self.emit_event(name, value)
        else:
            self.emit_event(EventKind.DISCOVERY_EVENT, (generation, name, value))

    def emit_event(self, name: str | EventKind, payload: Any) -> None:
        message = Event.from_wire(name, payload)
        self.event.emit(message.kind.value, message.payload)

    def submit(self, name: str, **data: Any) -> None:
        self.commands.put(Command.from_wire(name, data))

    def run(self) -> None:
        try:
            self.run_loop()
        finally:
            if self.gap_recovery is not None:
                self.gap_recovery.stop()
                self.gap_recovery = None
            if self.head_feed is not None:
                self.head_feed.stop()
            if self.recorder is not None:
                self.recorder.close()

    def run_loop(self) -> None:
        next_tick = time.monotonic()
        next_watch = 0.0
        while not self.quit_event.is_set():
            try:
                timeout = (
                    min(0.05, max(0, next_tick - time.monotonic()))
                    if self.running and self.head_feed is None
                    else 0.05
                )
                message = self.commands.get(timeout=timeout)
                name, data = message.kind, message.data()
            except queue.Empty:
                name = None
            if name:
                self.emit_event(EventKind.BUSY, True)
                try:
                    self.command(name, dict(data))
                except ExitReadCancelled:
                    pass  # STOP below owns cancellation; do not report it as an RPC failure.
                except Exception as exc:
                    if name in (
                        "discover",
                        "verify",
                        "select",
                        "compare_routes",
                    ) and not self.discovery_current(data.get("generation")):
                        continue
                    if name in ("reconcile", "compare_positions"):
                        self.emit_event(
                            EventKind.POSITION_COMPARISON_ERROR
                            if name == CommandKind.COMPARE_POSITIONS
                            else "receipt_review",
                            safe_error(exc),
                        )
                    self.running = False
                    self.halt_reason = safe_error(exc)
                    stopped_entry = (
                        name in ("buy", "start")
                        and isinstance(exc, EntryRejected)
                        and self.stop_event.is_set()
                        and not self.store.data.get("operation")
                        and not self.paper.position
                        and not self.position()
                    )
                    self.log.emit(("STOP: " if stopped_entry else "ОШИБКА: ") + safe_error(exc))
                    if stopped_entry:
                        pass  # Normal cancellation; STOP handling below clears queued commands.
                    elif name == CommandKind.COMPARE_ROUTES:
                        self.discovery_emit(data.get("generation"), "route_comparison_error", safe_error(exc))
                    elif name in ("discover", "verify", "select"):
                        self.pool = None
                        self.pool_generation = None
                        self.discovery_emit(data.get("generation"), "pools", [])
                        self.discovery_emit(data.get("generation"), "error", safe_error(exc))
                    else:
                        self.emit_event(EventKind.ERROR, safe_error(exc))
                finally:
                    self.emit_event(EventKind.BUSY, False)
                    self.status()
            if self.stop_event.is_set():
                self.stop_event.clear()
                # Discard commands queued before STOP was processed. In
                # particular, a queued BUY must not restart trading afterwards.
                while True:
                    try:
                        self.commands.get_nowait()
                    except queue.Empty:
                        break
                try:
                    if self.running or self.paper.position or self.position():
                        self.close_position("STOP")
                    self.running = False
                    self.strategy.stopped = True
                    self.halt_reason = ""
                    self.entry_notice = ""
                    self.log.emit("BOT остановлен")
                except Exception as exc:
                    self.running = False
                    self.halt_reason = safe_error(exc)
                    self.emit_event(EventKind.ERROR, safe_error(exc))
                    self.log.emit("STOP: " + safe_error(exc))
                self.status()
            if self.gap_recovery is not None and self.gap_recovery.result is not None:
                recovery = self.gap_recovery
                self.gap_recovery = None
                if self.pool == recovery.pool:
                    assert recovery.result is not None
                    self.record_market("backfill", **recovery.result)
                    result = recovery.result
                    assert result is not None
                    detail = (
                        result["error_type"]
                        if result["error_type"]
                        else f"{result['count']} Swap; ограниченный диапазон"
                        if result["truncated"]
                        else f"{result['count']} Swap"
                    )
                    self.log.emit(
                        f"Дозагрузка событий {result['from_block']}–{result['to_block']}: {detail}. Исторические события не торгуются"
                    )
            if not self.running and self.watchable_position() and time.monotonic() >= next_watch:
                started = time.monotonic()
                self.watch_position()
                delay = (
                    min(5, 0.5 * 2 ** min(self.quote_failures, 4))
                    if self.quote_unavailable
                    else self.interval
                )
                next_watch = max(started + delay, time.monotonic())
                self.status()
            head = (
                self.head_feed.snapshot()
                if self.head_feed is not None and self.mode != "DEMO" and not self.quote_unavailable
                else None
            )
            due = (
                self.head_schedule.due(head, time.monotonic(), next_tick)
                if self.head_feed is not None and self.mode != "DEMO"
                else time.monotonic() >= next_tick
            )
            if self.running and due:
                poll_started = time.monotonic()
                previous_head = self.head_schedule.last_head
                if self.head_schedule.consume(head, poll_started):
                    assert head is not None
                    if self.strategy.entry is None:
                        self.strategy.reset_anchor()
                    self.log.emit("Пропуск или смена ветви WebSocket: читается актуальное состояние HTTP")
                    self.record_market(
                        "stream_gap",
                        previous_block=previous_head.number if previous_head else None,
                        new_block=head.number,
                        discontinuity=head.discontinuity,
                    )
                    if self.gap_recovery is None and isinstance(self.chain, Chain) and self.pool is not None:
                        from dipbot.market.gap_recovery import GapRecovery

                        start = (
                            previous_head.number + 1
                            if previous_head and previous_head.number < head.number
                            else max(0, head.number - 31)
                        )
                        source = self.backup_chain or self.chain
                        self.gap_recovery = GapRecovery(
                            str(getattr(source.w3.provider, "endpoint_uri")), self.pool, start, head.number
                        ).start()
                    elif self.gap_recovery is not None:
                        self.log.emit(
                            "Дозагрузка предыдущего разрыва ещё выполняется; новый диапазон отмечен как неполный"
                        )
                        self.record_market(
                            "backfill",
                            pool=self.pool.address if self.pool else None,
                            from_block=previous_head.number + 1 if previous_head else None,
                            to_block=head.number,
                            truncated=True,
                            count=None,
                            events=[],
                            error_type="Busy",
                        )
                try:
                    with head_context(
                        head.number if head else None,
                        head.hash if head else None,
                        head.received_ns if head else None,
                    ):
                        self.observe()
                except ExitReadCancelled:
                    pass  # STOP is processed at the next loop boundary; no execution retry.
                except Exception as exc:
                    # Unhandled execution failures must still halt, including uncertain LIVE results.
                    self.running = False
                    self.halt_reason = safe_error(exc)
                    self.log.emit("BOT приостановлен: " + safe_error(exc))
                    self.emit_event(EventKind.ERROR, safe_error(exc))
                # One observation at a time; slow RPC skips missed slots instead
                # of queuing catch-up requests or adding another full delay.
                delay = (
                    min(5, 0.5 * 2 ** min(self.quote_failures, 4))
                    if self.quote_unavailable
                    else self.interval
                )
                next_tick = max(poll_started + delay, time.monotonic())
                self.status()

    def record_market(self, kind: str, **data: Any) -> None:
        if self.recorder is not None:
            self.recorder.record(kind, **data)
            if not self.recorder_notice and (self.recorder.dropped or self.recorder.error_type):
                self.recorder_notice = True
                self.log.emit(
                    "Архив рынка неполный: ошибка записи или достигнут лимит; торговый журнал не затронут"
                )

    def record_quote(
        self,
        source: Chain | None,
        purpose: str,
        side: str,
        amount: int,
        output: int,
        reverse: int | None = None,
    ) -> None:
        mark(self, "quote")
        context = getattr(source, "quote_context", None) or {}
        self.record_market(
            "quote",
            purpose=purpose,
            side=side,
            amount_in_raw=amount,
            amount_out_raw=output,
            reverse_out_raw=reverse,
            block=context.get("block"),
            block_hash=context.get("block_hash"),
            pool=self.pool.address if self.pool else None,
        )

    def paper_operation_cost(self) -> D:
        return self.paper_policy.operation_cost(self.gas_gwei, self.market.selected.quote, self.rates)

    def current_trade_detail(self) -> TradeDetail | None:
        if self.mode == "LIVE" and self.position():
            pos = self.position()
            return entry_view(
                D(pos["amount"]) / D(10) ** self.market.selected.token_decimals,
                pos.get("cost_quote"),
                pos.get("entry_fees", {}).get("usd"),
                pos.get("entry_rate"),
                total_usd=pos.get("entry_cost_usd"),
            )
        return self.trade_detail

    def status(self) -> None:
        settings = self.strategy.settings
        wait_reason, signal_notice = self.strategy.entry_wait(time.monotonic())
        realized = str(self.paper.realized) if self.mode != "LIVE" else "—"
        if self.mode == "LIVE" and self.live and self.pool:
            key = self.live.owner.lower() + ":" + self.market.selected.quote.lower()
            realized = self.store.data.get("realized_quote", {}).get(key, "—")
        base, entry = self.strategy.base or D(0), self.strategy.entry or D(0)
        levels = (
            {
                "ENTRY": str(entry),
                "TP": str(entry * (1 + settings.take_profit / 100)),
                "SL": str(entry * (1 - settings.stop_loss / 100)),
            }
            if entry
            else {"DIP": str(base * (1 - self.strategy.effective_dip / 100))}
        )
        if entry and self.strategy.exit_policy.tp_sl_basis == "quote":
            levels.pop("TP", None)
            levels.pop("SL", None)
        if entry and self.strategy.exit_policy.trailing_pct and self.strategy.peak_price:
            levels["TRAIL"] = str(
                self.strategy.peak_price * (1 - self.strategy.exit_policy.trailing_pct / 100)
            )
        payload: StatusPayload = {
            "running": self.running,
            "mode": self.mode,
            "levels": levels,
            "exit_retry": self.exit_retry,
            "trade_detail": self.current_trade_detail(),
            "open_estimate": self.open_estimate,
            "position_watch_error": getattr(self, "position_watch_error", ""),
            "position": str(self.paper.position)
            if self.mode != "LIVE"
            else str(
                D(self.position().get("amount", 0))
                / D(10) ** (self.market.selected.token_decimals if self.pool else 18)
            ),
            "base": str(self.strategy.base or 0),
            "rpc_health": self.rpc_health.report() if self.adaptive_rpc else [],
            "exit_basis": self.strategy.exit_policy.tp_sl_basis,
            "exit_return": getattr(self, "exit_return", None),
            "signal_mode": self.strategy.policy.mode,
            "signal_notice": signal_notice,
            "wait_reason": wait_reason,
            "effective_dip": str(self.strategy.effective_dip),
            "base_reason": self.strategy.base_reason,
            "base_age": max(0, time.monotonic() - self.strategy.base_time)
            if self.strategy.base_time is not None
            else None,
            "entry": str(self.strategy.entry or 0),
            "realized": realized,
            "paper_cost_model": self.paper_policy.export(),
            "historical_usd": (
                closed_summary(self.store, self.live.owner)
                if self.mode == "LIVE" and self.live
                else {
                    "value": str(self.paper_usd["value"])
                    if self.paper_usd["closed"] and not self.paper_usd["missing"]
                    else None,
                    "closed": self.paper_usd["closed"],
                    "missing": self.paper_usd["missing"],
                    "includes_gas": False,
                }
            ),
            "pnl_quote": (
                self.paper_context[2]
                if self.mode == "PAPER" and self.paper_context and len(self.paper_context) == 3
                else self.market.selected.quote
                if self.mode != "DEMO" and self.pool
                else ""
            ),
            "quote_unavailable": self.quote_unavailable,
            "entry_notice": self.entry_notice
            or (
                f"Пауза после выхода: {max(0, self.strategy.cooldown_until - time.monotonic()):.1f} с"
                if self.strategy.cooldown_until and time.monotonic() < self.strategy.cooldown_until
                else ""
            ),
            "halt_reason": self.halt_reason,
            "locked": bool(self.store.data.get("operation")),
        }
        self.emit_event(EventKind.STATUS, payload)

    def position_key(self) -> str:
        return f"{self.live.owner.lower()}:{self.pool.address.lower()}" if self.live and self.pool else ""

    def position(self) -> PositionRecord:
        return cast(PositionRecord, self.store.data.get("positions", {}).get(self.position_key(), {}))

    def set_position(self, amount: int, entry: D | int) -> None:
        positions = self.store.data.setdefault("positions", {})
        if amount:
            positions[self.position_key()] = {
                **positions.get(self.position_key(), {}),
                "amount": amount,
                "entry": str(entry),
                "pool": asdict(self.market.selected),
                "opened_at": positions.get(self.position_key(), {}).get("opened_at", time.time()),
                "peak_price": positions.get(self.position_key(), {}).get("peak_price", str(entry)),
            }
        else:
            positions.pop(self.position_key(), None)
        self.store.save()

    def require_chain(self) -> None:
        if self.chain is None:
            raise ValueError("Сначала подключите HTTP RPC")

    def require_live(self) -> None:
        if self.mode != "LIVE" or self.live is None:
            raise ValueError("Нужен LIVE-режим и кошелёк в Keychain")

    def configure(self, data: dict[str, Any]) -> None:
        return commands.configure(self, data)

    def command(self, name: str, data: dict[str, Any]) -> None:
        message = Command.from_wire(name, data)
        return commands.command(self, message.kind, dict(message.data()))

    def select_pool(self, pool: Pool, *, generation: int | None = None) -> None:
        return commands.select_pool(self, pool, generation=generation)

    def read_price(self, force_chain: bool = False) -> D:
        return market_observation.read_price(self, force_chain)

    def adaptive_market_price(self) -> D:
        return market_observation.adaptive_market_price(self)

    def market_price(self) -> D:
        return market_observation.market_price(self)

    def backup_price(self) -> D:
        return market_observation.backup_price(self)

    def watchable_position(self) -> bool:
        return market_observation.watchable_position(self)

    def watch_position(self) -> None:
        return market_observation.watch_position(self)

    def observe(self, read_only: bool = False) -> None:
        return market_observation.observe(self, read_only)

    def open_position(self) -> None:
        return positions.open_position(self)

    def exit_read(self, read: Callable[[Chain | None], T], *, stopping: bool = False) -> T:
        return positions.exit_read(self, read, stopping=stopping)

    def close_position(self, reason: str) -> None:
        return positions.close_position(self, reason)

    def sweep_service(self) -> SweepService:
        """Bind the current connection and wallet when starting this use case."""
        return SweepService(
            store=self.store,
            chain=self.connections.reader,
            live=self.session.executor,
            strategy=self.strategy,
            rates=self.rates,
            stop_event=self.stop_event,
            log=self.log.emit,
            emit=self.emit_event,
            position=self.position,
        )

    def sweep(self) -> None:
        self.sweep_service().run()


T = TypeVar("T")
