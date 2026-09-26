from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import replace
from typing import TYPE_CHECKING, TypeVar, cast

from requests.exceptions import ConnectionError as RPCConnectionError
from requests.exceptions import HTTPError
from requests.exceptions import Timeout as RPCTimeout
from web3.exceptions import Web3RPCError

from dipbot.application.errors import safe_error
from dipbot.application.messages import EventKind
from dipbot.application.trade_view import entry_view, exit_view
from dipbot.domain.entry_guard import EntryRejected
from dipbot.domain.records import ExitRetry
from dipbot.domain.strategy import D, raw_amount, snapshot_minimum
from dipbot.execution.accounting import marked_value, operation_fees, record_close
from dipbot.execution.errors import UncertainTransaction
from dipbot.market.chain import Chain
from dipbot.market.exit_reads import retry_read, transient
from dipbot.market.market_monitor import monitor_execution
from dipbot.observability.cycle_trace import mark
from dipbot.observability.telemetry import timed

if TYPE_CHECKING:
    from dipbot.application.contexts import PositionRuntime


@monitor_execution
@timed("worker.open_position")
def open_position(runtime: PositionRuntime) -> None:
    mark(runtime, "preflight_started")
    if runtime.stop_event.is_set():
        raise EntryRejected("STOP: вход отменён до проверки и исполнения")
    if runtime.session.strategy.entry is not None:
        raise ValueError("Позиция уже открыта")
    settings = runtime.session.strategy.settings
    if runtime.session.sizing.unit == "usd":
        if runtime.session.requested_amount is None:
            raise ValueError("AMOUNT в USD не задан")
        settings = replace(
            settings,
            amount=runtime.session.sizing.amount_quote(
                runtime.session.requested_amount, runtime.market.selected.quote, runtime.rates
            ),
        )
    if runtime.session.mode in ("LIVE", "PAPER") and settings.min_swaps:
        from dipbot.market.activity import swap_count

        try:
            activity = swap_count(runtime.connections.reader, runtime.market.selected)
        except (Web3RPCError, RPCConnectionError, RPCTimeout, TimeoutError, HTTPError) as exc:
            if runtime.connections.backup_chain is None:
                raise EntryRejected("Не удалось прочитать активность пула; вход запрещён") from exc
            if (
                runtime.connections.backup.verify_pool(
                    runtime.market.selected.address, runtime.market.selected.token
                )
                != runtime.market.pool
            ):
                raise ValueError("Резервный RPC вернул другой пул")
            try:
                activity = swap_count(runtime.connections.backup_chain, runtime.market.pool)
            except (Web3RPCError, RPCConnectionError, RPCTimeout, TimeoutError, HTTPError) as second:
                raise EntryRejected("Активность недоступна на обоих RPC; вход запрещён") from second
            runtime.log.emit("Активность проверена через резервный RPC")
        runtime.record_market(
            "activity",
            count=activity["count"],
            from_block=activity["from_block"],
            to_block=activity["to_block"],
            pool=runtime.market.selected.address,
        )
        runtime.log.emit(
            f"Активность пула: {activity['count']} Swap за блоки {activity['from_block']}–{activity['to_block']}"
        )
        if activity["count"] < settings.min_swaps:
            raise EntryRejected(
                f"Недостаточная активность: {activity['count']} Swap, нужно минимум {settings.min_swaps:g}"
            )
        mark(runtime, "activity_checked")
    else:
        mark(runtime, "activity_skipped")
    if runtime.session.mode in ("LIVE", "PAPER") and callable(
        getattr(runtime.connections.chain, "entry_quote", None)
    ):
        if runtime.session.mode == "LIVE":
            runtime.require_live()
        raw = raw_amount(settings.amount, runtime.market.selected.quote_decimals)
        try:
            check = runtime.connections.reader.entry_quote(
                runtime.market.selected, raw, settings.max_roundtrip_loss
            )
        except (RPCConnectionError, RPCTimeout, TimeoutError, HTTPError, Web3RPCError) as exc:
            if not transient(exc):
                raise
            # This is strictly before live.begin/swap. Discard the old signal;
            # observe() applies a pause and requires a new DIP after recovery.
            raise EntryRejected("Проверка входа недоступна: " + safe_error(exc)) from None
        runtime.record_quote(
            runtime.connections.chain,
            "entry_screen",
            "BUY",
            check.amount_in,
            check.target_out,
            check.reverse_out,
        )
        estimated_cost = runtime.session.cost_policy.assess(
            check, runtime.market.selected, runtime.session.gas_gwei, runtime.rates
        )
        if estimated_cost is not None:
            runtime.log.emit(f"Расчётные расходы цикла: {estimated_cost:.2f}% (модель газа, без token tax)")
        runtime.emit_event(
            EventKind.ENTRY_CHECK,
            {
                "estimated_cost_pct": str(estimated_cost) if estimated_cost is not None else None,
                "block": check.block,
                "roundtrip_loss_pct": str(check.roundtrip_loss_pct),
            },
        )
        if runtime.stop_event.is_set():
            raise ValueError("STOP запрошен во время проверки входа")
    mark(runtime, "entry_screened")
    if runtime.session.mode == "LIVE":
        runtime.require_live()
        if runtime.stop_event.is_set():
            raise ValueError("STOP запрошен во время подготовки")
        amount = raw_amount(settings.amount, runtime.market.selected.quote_decimals)
        bound = snapshot_minimum(
            amount,
            runtime.market.price,
            runtime.market.selected.quote_decimals,
            runtime.market.selected.token_decimals,
            settings.buy_tolerance,
        )
        runtime.session.executor.begin("BUY " + runtime.market.selected.token)
        received = runtime.session.executor.swap(
            runtime.market.selected, amount, True, settings.buy_tolerance, signal_minimum=bound
        )
        execution = (D(amount) / D(10) ** runtime.market.selected.quote_decimals) / (
            D(received) / D(10) ** runtime.market.selected.token_decimals
        )
        # Persist actual holdings even if the post-receipt price read fails.
        runtime.set_position(received, execution)
        runtime.position()["cost_quote"] = str(D(amount) / D(10) ** runtime.market.selected.quote_decimals)
        runtime.position()["execution_price"] = str(execution)
        rate = runtime.rates.snapshot(runtime.market.selected.quote)
        fees = operation_fees(getattr(runtime.session.live, "operation", None))
        cost_usd = marked_value(D(amount) / D(10) ** runtime.market.selected.quote_decimals, rate)
        runtime.position()["entry_rate"] = rate
        runtime.position()["entry_fees"] = fees
        operation = getattr(runtime.session.live, "operation", None)
        runtime.position()["entry_gas_hashes"] = (
            [row["hash"] for row in operation["transactions"]] if operation else None
        )
        runtime.position()["entry_cost_usd"] = (
            str(D(cost_usd) + D(fees["usd"])) if cost_usd is not None and fees["usd"] is not None else None
        )
        runtime.store.save()
        entry = runtime.read_price()
        runtime.position()["peak_price"] = str(entry)
        runtime.set_position(received, entry)
        runtime.session.executor.finish()
    else:
        try:
            paper_fee = (
                runtime.paper_operation_cost()
                if runtime.session.mode == "PAPER" and runtime.market.pool
                else runtime.session.paper_policy.fee_quote
            )
        except TimeoutError as exc:
            raise EntryRejected(str(exc)) from None
        signal_price = runtime.market.price
        entry = runtime.market.price
        if runtime.session.mode == "PAPER" and runtime.stop_event.wait(
            runtime.session.paper_policy.latency_seconds
        ):
            raise EntryRejected("STOP во время ожидания PAPER; виртуальный вход отменён")
        mark(runtime, "paper_delay_finished")
        if runtime.session.mode == "PAPER" and callable(getattr(runtime.connections.chain, "quote", None)):
            raw = raw_amount(settings.amount, runtime.market.selected.quote_decimals)
            if callable(getattr(runtime.connections.chain, "price", None)):
                entry = runtime.read_price()
            mark(runtime, "fill_price_read")
            quote = getattr(runtime.connections.chain, "paper_quote", runtime.connections.reader.quote)
            quoted = quote(runtime.market.selected, raw, True)
            mark(runtime, "fill_quote_received")
            runtime.record_quote(runtime.connections.chain, "paper_fill", "BUY", raw, quoted)
            if runtime.stop_event.is_set():
                raise EntryRejected("STOP во время котировки PAPER; виртуальный вход отменён")
            bound = snapshot_minimum(
                raw,
                signal_price,
                runtime.market.selected.quote_decimals,
                runtime.market.selected.token_decimals,
                settings.buy_tolerance,
            )
            if quoted < bound:
                raise EntryRejected("PAPER BUY: котировка ниже minOut снимка; покупка не исполнена")
            try:
                paper_fee = runtime.paper_operation_cost()
            except TimeoutError as exc:
                raise EntryRejected(str(exc)) from None
            paper_gross = D(raw) / D(10) ** runtime.market.selected.quote_decimals
            execution = runtime.session.paper.buy_quoted(
                paper_gross + paper_fee, D(quoted) / D(10) ** runtime.market.selected.token_decimals
            )
            runtime.log.emit(
                f"PAPER: router quote после задержки {runtime.session.paper_policy.latency_seconds:g} с; "
                f"стоимость операции {paper_fee} в базе добавлена по модели; token tax не учтён"
            )
        else:
            execution = runtime.session.paper.buy(settings.amount, runtime.market.price)
            paper_gross = runtime.session.paper.cost - paper_fee
        runtime.session.paper_usd["entry"] = (
            marked_value(runtime.session.paper.cost, runtime.rates.snapshot(runtime.market.selected.quote))
            if runtime.session.mode == "PAPER" and runtime.market.pool
            else None
        )
    if runtime.session.mode == "PAPER" and runtime.market.pool:
        rate = runtime.rates.snapshot(runtime.market.selected.quote)
        runtime.session.trade_detail = entry_view(
            runtime.session.paper.position,
            paper_gross,
            marked_value(paper_fee, rate),
            rate,
            total_usd=runtime.session.paper_usd["entry"],
        )
    elif runtime.session.mode == "LIVE":
        pos = runtime.position()
        runtime.session.trade_detail = entry_view(
            D(pos["amount"]) / D(10) ** runtime.market.selected.token_decimals,
            pos["cost_quote"],
            pos["entry_fees"]["usd"],
            pos["entry_rate"],
            total_usd=pos.get("entry_cost_usd"),
        )
    else:
        runtime.session.trade_detail = None
    runtime.session.open_estimate = None
    runtime.session.strategy.bought(entry, now=time.monotonic())
    mark(runtime, "execution_applied")
    runtime.record_market("execution", side="BUY", price=str(entry))
    runtime.exit_return = None
    # Publish settled holdings before the chart marker. Monitor shutdown and
    # later RPC reads must not leave a filled trade paired with old UI state.
    runtime.status()
    runtime.emit_event(
        EventKind.TRADE_MARKER, {"mode": runtime.session.mode, "side": "BUY", "price": str(entry)}
    )
    runtime.log.emit(f"{runtime.session.mode} BUY: исполнение {execution:.10g}; база TP/SL {entry:.10g}")


def exit_read(runtime: PositionRuntime, read: Callable[[Chain | None], T], *, stopping: bool = False) -> T:
    """Retry only the supplied price/quote read; never wrap swap or send."""

    def notify(value: ExitRetry | None) -> None:
        runtime.session.exit_retry = value
        if value:
            runtime.record_market("exit_read_retry", **value)
        # Do not turn an active, known LIVE operation into a UI recovery latch.
        runtime.emit_event(EventKind.EXIT_RETRY, value)

    def attempt(index: int) -> T:
        operation = runtime.store.data.get("operation")
        if operation and any(t.get("status") != "confirmed" for t in operation.get("transactions", [])):
            raise UncertainTransaction("Результат транзакции неизвестен; повтор выхода заблокирован")
        source = runtime.connections.chain
        if index and runtime.connections.backup_chain is not None:
            # Includes network, canonical pool, freshness and fork checks.
            runtime.backup_price()
            source = runtime.connections.backup_chain
        return read(source)

    return cast(
        T,
        retry_read(
            attempt,
            cancelled=lambda: runtime.quit_event.is_set() or (runtime.stop_event.is_set() and not stopping),
            wait=lambda delay: runtime.quit_event.wait(delay) if stopping else runtime.stop_event.wait(delay),
            notify=notify,
        ),
    )


@monitor_execution
@timed("worker.close_position")
def close_position(runtime: PositionRuntime, reason: str) -> None:
    if runtime.session.mode == "LIVE":
        runtime.require_live()
        position = runtime.position()
        if not position:
            return
        runtime.session.executor.begin("SELL " + runtime.market.selected.token)
        amount = min(
            position["amount"],
            runtime.connections.reader.balance(runtime.market.selected.token, runtime.session.executor.owner),
        )
        if not amount:
            raise ValueError("Кэш позиции не совпадает с балансом; нужна сверка")
        received = runtime.session.executor.swap(
            runtime.market.selected,
            amount,
            False,
            runtime.session.strategy.settings.slippage,
            quote_reader=lambda pool, amount, buy: runtime.exit_read(
                lambda source: require_reader(source).quote(pool, amount, buy), stopping=reason == "STOP"
            ),
        )
        if isinstance(received, int) and position.get("cost_quote") is not None:
            pnl = D(received) / D(10) ** runtime.market.selected.quote_decimals - D(
                cast(str, position["cost_quote"])
            )
            key = runtime.session.executor.owner.lower() + ":" + runtime.market.selected.quote.lower()
            ledger = runtime.store.data.setdefault("realized_quote", {})
            ledger[key] = str(D(ledger.get(key, "0")) + pnl)
            runtime.log.emit(f"LIVE P&L: {pnl:+.8g} базового актива без газа; газ отдельно в журнале BNB")
        if isinstance(received, int):
            record_close(
                runtime.store,
                runtime.session.executor.owner,
                runtime.market.selected,
                position,
                received,
                getattr(runtime.session.live, "operation", None),
                runtime.rates.snapshot(runtime.market.selected.quote),
                inventory_matches=amount == position["amount"],
            )
        if isinstance(received, int):
            entry_detail = entry_view(
                D(position["amount"]) / D(10) ** runtime.market.selected.token_decimals,
                position.get("cost_quote"),
                position.get("entry_fees", {}).get("usd"),
                position.get("entry_rate"),
                total_usd=position.get("entry_cost_usd"),
            )
            runtime.session.trade_detail = exit_view(
                entry_detail,
                D(amount) / D(10) ** runtime.market.selected.token_decimals,
                D(received) / D(10) ** runtime.market.selected.quote_decimals,
                operation_fees(getattr(runtime.session.live, "operation", None))["usd"],
                runtime.rates.snapshot(runtime.market.selected.quote),
                complete=amount == position["amount"] and "cost_quote" in position,
            )
        runtime.set_position(0, 0)
        runtime.session.executor.finish()
        price = runtime.market.current_price or D(position["entry"])
    else:
        if not runtime.session.paper.position:
            return
        if runtime.session.mode == "PAPER" and reason != "STOP":
            runtime.stop_event.wait(runtime.session.paper_policy.latency_seconds)
        mark(runtime, "paper_delay_finished")
        price = runtime.exit_read(
            lambda source: (
                runtime.read_price() if source is runtime.connections.chain else runtime.backup_price()
            ),
            stopping=reason == "STOP",
        )
        mark(runtime, "fill_price_read")
        paper_cost = runtime.session.paper.cost
        quantity = runtime.session.paper.position
        if runtime.session.mode == "PAPER" and callable(getattr(runtime.connections.chain, "quote", None)):
            amount = raw_amount(runtime.session.paper.position, runtime.market.selected.token_decimals)
            source, output = runtime.exit_read(
                lambda source: (
                    source,
                    getattr(source, "paper_quote", require_reader(source).quote)(
                        runtime.market.selected, amount, False
                    ),
                ),
                stopping=reason == "STOP",
            )
            mark(runtime, "fill_quote_received")
            paper_fee = runtime.paper_operation_cost()
            runtime.record_quote(source, "paper_fill", "SELL", amount, output)
            pnl = runtime.session.paper.sell_quoted(
                D(output) / D(10) ** runtime.market.selected.quote_decimals, paper_fee
            )
            rate = runtime.rates.snapshot(runtime.market.selected.quote)
            runtime.session.trade_detail = exit_view(
                runtime.session.trade_detail,
                quantity,
                D(output) / D(10) ** runtime.market.selected.quote_decimals,
                marked_value(paper_fee, rate),
                rate,
            )
        else:
            pnl = runtime.session.paper.sell(price)
        proceeds_usd = (
            marked_value(paper_cost + pnl, runtime.rates.snapshot(runtime.market.selected.quote))
            if runtime.session.mode == "PAPER" and runtime.market.pool
            else None
        )
        runtime.session.paper_usd["closed"] += 1
        if proceeds_usd is None or runtime.session.paper_usd["entry"] is None:
            runtime.session.paper_usd["missing"] += 1
            if runtime.session.trade_detail is not None:
                runtime.session.trade_detail["net_usd"] = None
        else:
            closed_delta = D(proceeds_usd) - D(runtime.session.paper_usd["entry"])
            runtime.session.paper_usd["value"] += closed_delta
            if runtime.session.trade_detail is not None:
                # Show the ledger result, not a differently ordered Decimal recomputation.
                runtime.session.trade_detail["net_usd"] = str(closed_delta)
        runtime.session.paper_usd["entry"] = None
        runtime.log.emit(
            f"PAPER P&L: {pnl:+.8g} базового актива (стоимость операции по модели; token tax не учтён)"
        )
    runtime.session.open_estimate = None
    runtime.session.strategy.sold(price, reason, now=time.monotonic())
    mark(runtime, "execution_applied")
    runtime.record_market("execution", side="SELL", price=str(price), reason=reason)
    runtime.exit_return = None
    runtime.status()
    runtime.emit_event(
        EventKind.TRADE_MARKER, {"mode": runtime.session.mode, "side": "SELL", "price": str(price)}
    )
    if runtime.session.mode == "LIVE":
        # SELL is already accounted for if this independent read fails.
        runtime.session.strategy.base = runtime.read_price()
        runtime.session.strategy.last_price = runtime.session.strategy.base
        runtime.session.strategy.last_time = runtime.market.price_time
    runtime.session.halt_reason = ""
    runtime.log.emit(f"{runtime.session.mode} SELL: {reason}")


T = TypeVar("T")


def require_reader(source: Chain | None) -> Chain:
    if source is None:
        raise ValueError("Сначала подключите HTTP RPC")
    return source
