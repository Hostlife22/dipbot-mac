from __future__ import annotations

import time
from typing import TYPE_CHECKING

from requests.exceptions import ConnectionError as RPCConnectionError
from requests.exceptions import HTTPError
from requests.exceptions import Timeout as RPCTimeout
from web3.exceptions import Web3RPCError

from dipbot.application.errors import safe_error
from dipbot.application.messages import EventKind
from dipbot.domain.entry_guard import EntryRejected
from dipbot.domain.strategy import D, raw_amount
from dipbot.execution.accounting import marked_value
from dipbot.execution.errors import UncertainTransaction
from dipbot.market.chain import StaleBlock
from dipbot.market.exit_reads import transient
from dipbot.observability.cycle_trace import observation_mark, observation_timing, signal_cycle
from dipbot.observability.telemetry import timed

if TYPE_CHECKING:
    from dipbot.application.contexts import ObservationRuntime


@timed("worker.read_price")
def read_price(runtime: ObservationRuntime, force_chain: bool = False) -> D:
    if runtime.session.mode == "DEMO" and not force_chain:
        # Deterministic local market; no network, funds or signing.
        runtime.market.tick += 1
        # Include a sudden dip: smooth declines reanchor every two moves.
        cycle = ("1", "1.01", "1.02", "0.97", "0.98", "1.00", "1.01", "1")
        price = D(cycle[(runtime.market.tick - 1) % len(cycle)])
    else:
        runtime.require_chain()
        if not runtime.market.pool:
            raise ValueError("Пул не выбран")
        price = runtime.market_price()
    runtime.market.current_price = price
    runtime.market.price_time = time.monotonic()
    demo = runtime.session.mode == "DEMO" and not force_chain
    source_chain = (
        runtime.connections.backup_chain
        if runtime.connections.market_source != "BSC"
        else runtime.connections.chain
    )
    header = getattr(source_chain, "price_block", None) if not demo else None
    runtime.emit_event(
        EventKind.PRICE_CONTEXT,
        {
            "source": "DEMO" if demo else getattr(runtime.connections.chain, "price_source", "BSC"),
            "rpc_source": runtime.connections.market_source,
            "same_block_cache": bool(getattr(source_chain, "price_cache_hit", False)) if not demo else False,
            "block": header["number"] if header else None,
            "block_timestamp": header.get("timestamp") if header else None,
            "quote": "" if demo else runtime.market.selected.quote,
        },
    )
    runtime.record_market(
        "price",
        price=str(price),
        block=header["number"] if header else None,
        block_hash=bytes(header["hash"]).hex() if header else None,
        block_timestamp=header.get("timestamp") if header else None,
        source="DEMO" if demo else "BSC",
        pool_state=getattr(source_chain, "price_state", None) if not demo else None,
    )
    runtime.emit_event(EventKind.PRICE, str(price))
    return price


def adaptive_market_price(runtime: ObservationRuntime) -> D:
    source_id = runtime.connections.rpc_health.choose(time.monotonic())
    for attempt in range(2):
        source = runtime.connections.backup_chain if source_id else runtime.connections.chain
        started = time.monotonic()
        try:
            price = (
                runtime.backup_price()
                if source_id
                else runtime.connections.reader.price(runtime.market.selected)
            )
            header = getattr(source, "price_block", None)
            previous = runtime.connections.last_market_header
            if (
                header
                and previous
                and (
                    header["number"] < previous["number"]
                    or (header["number"] == previous["number"] and header["hash"] != previous["hash"])
                )
            ):
                raise TimeoutError("RPC вернул более старый блок или другую ветвь")
        except (RPCConnectionError, RPCTimeout, TimeoutError, HTTPError, Web3RPCError) as exc:
            if not transient(exc):
                raise
            runtime.connections.rpc_health.failure(source_id, time.monotonic())
            other = 1 - source_id
            if attempt or time.monotonic() < runtime.connections.rpc_health.blocked_until[other]:
                raise
            source_id = other
            continue
        runtime.connections.rpc_health.success(source_id, time.monotonic() - started, time.monotonic())
        runtime.connections.last_market_header = dict(header) if header else previous
        runtime.connections.market_source = "BSC · резервный RPC" if source_id else "BSC"
        return price

    raise RuntimeError("RPC attempts exhausted")


def market_price(runtime: ObservationRuntime) -> D:
    if runtime.connections.adaptive_rpc and runtime.connections.backup_chain is not None:
        return runtime.adaptive_market_price()
    # Execution always keeps runtime.connections.chain / LiveTrader.chain on the primary.
    if runtime.connections.backup_chain is not None and time.monotonic() < runtime.connections.backup_until:
        return runtime.backup_price()
    try:
        price = runtime.connections.reader.price(runtime.market.selected)
    except (RPCConnectionError, RPCTimeout, TimeoutError, HTTPError, Web3RPCError) as exc:
        if not transient(exc):
            raise
        if runtime.connections.backup_chain is None:
            raise
        runtime.connections.backup_until = time.monotonic() + 30
        runtime.log.emit(
            "Основной RPC недоступен: котировки через резервный. Отправка сделок остаётся на основном RPC"
        )
        return runtime.backup_price()
    if runtime.connections.market_source != "BSC":
        runtime.log.emit("Котировки снова поступают с основного RPC")
    runtime.connections.market_source = "BSC"
    return price


def backup_price(runtime: ObservationRuntime) -> D:
    if runtime.connections.backup_verified_pool != runtime.market.pool:
        runtime.connections.backup.check()
        verified = runtime.connections.backup.verify_pool(
            runtime.market.selected.address, runtime.market.selected.token
        )
        if verified != runtime.market.pool:
            raise ValueError("Резервный RPC вернул другой пул/маршрут; торговля приостановлена")
        runtime.connections.backup_verified_pool = verified
    price = runtime.connections.backup.price(runtime.market.selected)
    primary = getattr(runtime.connections.chain, "price_block", None)
    backup = getattr(runtime.connections.backup_chain, "price_block", None)
    if (
        primary
        and backup
        and (
            backup["number"] < primary["number"]
            or (backup["number"] == primary["number"] and backup["hash"] != primary["hash"])
        )
    ):
        raise StaleBlock("Резервный RPC отстаёт или вернул другую ветвь цепочки")
    runtime.connections.market_source = "BSC · резервный RPC"
    return price


def watchable_position(runtime: ObservationRuntime) -> bool:
    return (
        runtime.session.mode in ("PAPER", "LIVE")
        and runtime.market.pool is not None
        and bool(runtime.position() if runtime.session.mode == "LIVE" else runtime.session.paper.position)
    )


def watch_position(runtime: ObservationRuntime) -> None:
    # Serial read-only observation: no strategy signals, persistence or execution.
    runtime.position_watch_error = ""
    runtime.session.open_estimate = None
    if runtime.store.data.get("operation"):
        runtime.position_watch_error = "Результат транзакции неизвестен; требуется сверка"
        return
    try:
        runtime.observe(read_only=True)
        if runtime.market.quote_unavailable:
            runtime.position_watch_error = "Ошибка RPC · повтор чтения с паузой до 5 с"
    except Exception as exc:
        # Monitoring must not raise modal errors or change the trading halt reason.
        runtime.session.open_estimate = None
        runtime.market.quote_unavailable = True
        runtime.market.quote_failures += 1
        runtime.position_watch_error = safe_error(exc)


@timed("worker.observe")
@observation_timing
def observe(runtime: ObservationRuntime, read_only: bool = False) -> None:
    # Retry only a failed read, never an execution or post-receipt failure.
    if runtime.store.data.get("operation"):
        raise UncertainTransaction("Незавершённая операция: автоматические сделки заблокированы")
    runtime.session.open_estimate = None
    try:
        observation_mark("http_started")
        price = runtime.read_price()
        observation_mark("price_ready")
        exit_return = None
        if read_only or (
            runtime.session.strategy.entry is not None
            and runtime.session.strategy.exit_policy.tp_sl_basis == "quote"
        ):
            if runtime.session.mode == "LIVE":
                position = runtime.position()
                amount, cost = position["amount"], D(position.get("cost_quote") or 0)
            else:
                cost = runtime.session.paper.cost
                amount = (
                    raw_amount(runtime.session.paper.position, runtime.market.selected.token_decimals)
                    if runtime.market.pool
                    else 0
                )
            if cost <= 0 and not read_only:
                raise ValueError("Неизвестна себестоимость позиции: TP/SL по выходу недоступен")
            if runtime.session.mode == "DEMO":
                proceeds = runtime.session.paper.position * price
            else:
                source = (
                    runtime.connections.backup_chain
                    if runtime.connections.market_source != "BSC"
                    else runtime.connections.chain
                )
                assert source is not None
                exit_raw = source.exit_quote(runtime.market.selected, amount)
                runtime.record_quote(
                    source, "position_mark" if read_only else "exit_signal", "SELL", amount, exit_raw
                )
                proceeds = D(exit_raw) / D(10) ** runtime.market.selected.quote_decimals
            if time.monotonic() - runtime.market.price_time > runtime.session.strategy.settings.max_gap:
                raise TimeoutError("Снимок цены устарел во время котировки выхода")
            if runtime.session.mode == "PAPER":
                proceeds -= runtime.paper_operation_cost()
            exit_return = (proceeds / cost - 1) * 100 if cost > 0 else None
            rate = runtime.rates.snapshot(runtime.market.selected.quote) if runtime.market.pool else None
            entry_usd = (
                runtime.position().get("entry_cost_usd")
                if runtime.session.mode == "LIVE"
                else runtime.session.paper_usd["entry"]
            )
            value = marked_value(proceeds, rate)
            runtime.session.open_estimate = {
                "at": time.monotonic(),
                "value_usd": value,
                "pnl_usd": str(D(value) - D(entry_usd))
                if value is not None and entry_usd is not None
                else None,
                "excludes_exit_gas": runtime.session.mode == "LIVE",
            }
        runtime.exit_return = str(exit_return) if exit_return is not None else None
    except (RPCConnectionError, RPCTimeout, TimeoutError, HTTPError, Web3RPCError) as exc:
        if not transient(exc):
            raise
        runtime.record_market("read_error", type=type(exc).__name__)
        runtime.market.quote_failures += 1
        if not runtime.market.quote_unavailable:
            runtime.log.emit(
                "Котировки недоступны: входы запрещены, повтор чтения с паузой до 5 с. Открытая позиция сохраняется"
            )
        runtime.market.quote_unavailable = True
        runtime.exit_return = None
        return
    if runtime.market.quote_unavailable:
        runtime.log.emit("Чтение котировок восстановлено; проверка позиции возобновлена")
    runtime.market.quote_unavailable = False
    runtime.market.quote_failures = 0
    if read_only:
        return
    now = time.monotonic()
    if runtime.session.entry_notice and runtime.session.strategy.entry is None:
        if now < runtime.session.entry_retry_at:
            return
        # Require a new signal from a fresh baseline, not the rejected signal.
        runtime.session.strategy.reset_anchor()
        runtime.session.entry_notice = ""
    if (
        runtime.session.strategy.entry is None
        and runtime.session.strategy.last_time is not None
        and now - runtime.session.strategy.last_time > runtime.session.strategy.settings.max_gap
    ):
        runtime.log.emit("Разрыв котировок > 0.55 с: база DIP сброшена")
    source = (
        runtime.connections.backup_chain
        if runtime.connections.market_source != "BSC"
        else runtime.connections.chain
    )
    header = getattr(source, "price_block", None) if runtime.session.mode != "DEMO" else None
    observation_id = (header["number"], bytes(header["hash"]), price) if header else None
    usd_mark = (
        runtime.rates.snapshot(runtime.market.selected.quote)
        if runtime.market.pool and runtime.session.mode != "DEMO"
        else None
    )
    runtime.record_market(
        "observation",
        price=str(price),
        block=header["number"] if header else None,
        block_hash=bytes(header["hash"]).hex() if header else None,
        quote_usd=usd_mark["usd"] if usd_mark else None,
        quote_usd_observed_at=usd_mark["observed_at"] if usd_mark else None,
    )
    action = runtime.session.strategy.observe(
        price, now, observation_id=observation_id, exit_return=exit_return
    )
    observation_mark("strategy_completed")
    if (
        runtime.session.mode == "LIVE"
        and runtime.session.strategy.entry is not None
        and runtime.session.strategy.peak_price is not None
    ):
        position = runtime.position()
        if position and runtime.session.strategy.peak_price > D(
            position.get("peak_price", position["entry"])
        ):
            position["peak_price"] = str(runtime.session.strategy.peak_price)
            runtime.store.save()
    if runtime.stop_event.is_set():
        return
    if action:
        runtime.record_market(
            "signal",
            action=action,
            price=str(price),
            base=str(runtime.session.strategy.base),
            entry=str(runtime.session.strategy.entry),
        )
    if action == "BUY":
        try:
            with signal_cycle(runtime, action, header):
                runtime.open_position()
        except EntryRejected as exc:
            if (
                runtime.session.mode not in ("PAPER", "LIVE")
                or runtime.store.data.get("operation")
                or runtime.session.paper.position
                or runtime.position()
            ):
                raise
            runtime.session.entry_retry_at = time.monotonic() + 5.0
            runtime.session.entry_notice = str(exc) + "; пауза 5 с, затем новый сигнал DIP"
            runtime.log.emit("Вход пропущен: " + runtime.session.entry_notice)
    elif action:
        with signal_cycle(runtime, action, header):
            runtime.close_position(action)
        if runtime.session.strategy.stopped:
            runtime.session.running = False
