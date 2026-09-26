from __future__ import annotations
from dipbot.application.errors import safe_error
from dipbot.observability.telemetry import timed
from dipbot.observability.cycle_trace import mark
from dataclasses import replace
import time
from requests.exceptions import ConnectionError as RPCConnectionError, Timeout as RPCTimeout, HTTPError

from web3.exceptions import Web3RPCError

from dipbot.domain.strategy import D, raw_amount, snapshot_minimum
from dipbot.execution.errors import UncertainTransaction


from dipbot.market.exit_reads import retry_read, transient
from dipbot.application.trade_view import entry_view, exit_view
from dipbot.domain.entry_guard import EntryRejected
from dipbot.market.market_monitor import monitor_execution
from dipbot.execution.accounting import marked_value, operation_fees, record_close


from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from dipbot.application.worker import Worker

@monitor_execution
@timed("worker.open_position")
def open_position(runtime: Worker):
    mark(runtime, 'preflight_started')
    if runtime.stop_event.is_set():
        raise EntryRejected('STOP: вход отменён до проверки и исполнения')
    if runtime.strategy.entry is not None:
        raise ValueError("Позиция уже открыта")
    settings = runtime.strategy.settings
    if runtime.sizing.unit == 'usd':
        settings = replace(settings, amount=runtime.sizing.amount_quote(runtime.requested_amount, runtime.pool.quote, runtime.rates))
    if runtime.mode in ('LIVE', 'PAPER') and settings.min_swaps:
        from dipbot.market.activity import swap_count
        try:
            activity = swap_count(runtime.chain, runtime.pool)
        except (Web3RPCError, RPCConnectionError, RPCTimeout, TimeoutError, HTTPError) as exc:
            if runtime.backup_chain is None:
                raise EntryRejected('Не удалось прочитать активность пула; вход запрещён') from exc
            if runtime.backup_chain.verify_pool(runtime.pool.address, runtime.pool.token) != runtime.pool:
                raise ValueError('Резервный RPC вернул другой пул')
            try:
                activity = swap_count(runtime.backup_chain, runtime.pool)
            except (Web3RPCError, RPCConnectionError, RPCTimeout, TimeoutError, HTTPError) as second:
                raise EntryRejected('Активность недоступна на обоих RPC; вход запрещён') from second
            runtime.log.emit('Активность проверена через резервный RPC')
        runtime.record_market('activity', count=activity['count'], from_block=activity['from_block'], to_block=activity['to_block'], pool=runtime.pool.address)
        runtime.log.emit(f"Активность пула: {activity['count']} Swap за блоки {activity['from_block']}–{activity['to_block']}")
        if activity['count'] < settings.min_swaps:
            raise EntryRejected(f"Недостаточная активность: {activity['count']} Swap, нужно минимум {settings.min_swaps:g}")
        mark(runtime, 'activity_checked')
    else:
        mark(runtime, 'activity_skipped')
    if runtime.mode in ('LIVE', 'PAPER') and callable(getattr(runtime.chain, 'entry_quote', None)):
        if runtime.mode == 'LIVE':
            runtime.require_live()
        raw = raw_amount(settings.amount, runtime.pool.quote_decimals)
        try:
            check = runtime.chain.entry_quote(runtime.pool, raw, settings.max_roundtrip_loss)
        except (RPCConnectionError, RPCTimeout, TimeoutError, HTTPError, Web3RPCError) as exc:
            if not transient(exc):
                raise
            # This is strictly before live.begin/swap. Discard the old signal;
            # observe() applies a pause and requires a new DIP after recovery.
            raise EntryRejected('Проверка входа недоступна: ' + safe_error(exc)) from None
        runtime.record_quote(runtime.chain, 'entry_screen', 'BUY', check.amount_in, check.target_out, check.reverse_out)
        estimated_cost = runtime.cost_policy.assess(check, runtime.pool, runtime.gas_gwei, runtime.rates)
        if estimated_cost is not None:
            runtime.log.emit(f'Расчётные расходы цикла: {estimated_cost:.2f}% (модель газа, без token tax)')
        runtime.event.emit('entry_check', {'estimated_cost_pct':str(estimated_cost) if estimated_cost is not None else None,
            'block': check.block,
            'roundtrip_loss_pct': str(check.roundtrip_loss_pct)})
        if runtime.stop_event.is_set():
            raise ValueError('STOP запрошен во время проверки входа')
    mark(runtime, 'entry_screened')
    if runtime.mode == "LIVE":
        runtime.require_live()
        if runtime.stop_event.is_set():
            raise ValueError("STOP запрошен во время подготовки")
        amount = raw_amount(settings.amount, runtime.pool.quote_decimals)
        bound = snapshot_minimum(amount, runtime.current_price, runtime.pool.quote_decimals,
                                 runtime.pool.token_decimals, settings.buy_tolerance)
        runtime.live.begin("BUY " + runtime.pool.token)
        received = runtime.live.swap(runtime.pool, amount, True, settings.buy_tolerance,
                                  signal_minimum=bound)
        execution = (D(amount) / D(10)**runtime.pool.quote_decimals) / (D(received) / D(10)**runtime.pool.token_decimals)
        # Persist actual holdings even if the post-receipt price read fails.
        runtime.set_position(received, execution)
        runtime.position()['cost_quote'] = str(D(amount) / D(10)**runtime.pool.quote_decimals)
        runtime.position()['execution_price'] = str(execution)
        rate = runtime.rates.snapshot(runtime.pool.quote)
        fees = operation_fees(getattr(runtime.live, 'operation', None))
        cost_usd = marked_value(D(amount)/D(10)**runtime.pool.quote_decimals, rate)
        runtime.position()['entry_rate'] = rate
        runtime.position()['entry_fees'] = fees
        operation = getattr(runtime.live,'operation',None)
        runtime.position()['entry_gas_hashes'] = [row['hash'] for row in operation['transactions']] if operation else None
        runtime.position()['entry_cost_usd'] = (str(D(cost_usd)+D(fees['usd']))
            if cost_usd is not None and fees['usd'] is not None else None)
        runtime.store.save()
        entry = runtime.read_price()
        runtime.position()['peak_price'] = str(entry)
        runtime.set_position(received, entry)
        runtime.live.finish()
    else:
        try:
            paper_fee = runtime.paper_operation_cost() if runtime.mode == 'PAPER' and runtime.pool else runtime.paper_policy.fee_quote
        except TimeoutError as exc:
            raise EntryRejected(str(exc)) from None
        signal_price = runtime.current_price
        entry = runtime.current_price
        if runtime.mode == 'PAPER' and runtime.stop_event.wait(runtime.paper_policy.latency_seconds):
            raise EntryRejected('STOP во время ожидания PAPER; виртуальный вход отменён')
        mark(runtime, 'paper_delay_finished')
        if runtime.mode == 'PAPER' and callable(getattr(runtime.chain, 'quote', None)):
            raw = raw_amount(settings.amount, runtime.pool.quote_decimals)
            if callable(getattr(runtime.chain, 'price', None)):
                entry = runtime.read_price()
            mark(runtime, 'fill_price_read')
            quote = getattr(runtime.chain, 'paper_quote', runtime.chain.quote)
            quoted = quote(runtime.pool, raw, True)
            mark(runtime, 'fill_quote_received')
            runtime.record_quote(runtime.chain, 'paper_fill', 'BUY', raw, quoted)
            if runtime.stop_event.is_set():
                raise EntryRejected('STOP во время котировки PAPER; виртуальный вход отменён')
            bound = snapshot_minimum(raw, signal_price, runtime.pool.quote_decimals,
                                     runtime.pool.token_decimals, settings.buy_tolerance)
            if quoted < bound:
                raise EntryRejected('PAPER BUY: котировка ниже minOut снимка; покупка не исполнена')
            try:
                paper_fee = runtime.paper_operation_cost()
            except TimeoutError as exc:
                raise EntryRejected(str(exc)) from None
            paper_gross = D(raw)/D(10)**runtime.pool.quote_decimals
            execution = runtime.paper.buy_quoted(paper_gross+paper_fee,
                D(quoted)/D(10)**runtime.pool.token_decimals)
            runtime.log.emit(f'PAPER: router quote после задержки {runtime.paper_policy.latency_seconds:g} с; '
                f'стоимость операции {paper_fee} в базе добавлена по модели; token tax не учтён')
        else:
            execution = runtime.paper.buy(settings.amount, runtime.current_price)
            paper_gross = runtime.paper.cost-paper_fee
        runtime.paper_usd['entry'] = marked_value(runtime.paper.cost,
            runtime.rates.snapshot(runtime.pool.quote)) if runtime.mode == 'PAPER' and runtime.pool else None
    if runtime.mode == 'PAPER' and runtime.pool:
        rate = runtime.rates.snapshot(runtime.pool.quote)
        runtime.trade_detail = entry_view(runtime.paper.position, paper_gross,
            marked_value(paper_fee, rate), rate, total_usd=runtime.paper_usd['entry'])
    elif runtime.mode == 'LIVE':
        pos = runtime.position()
        runtime.trade_detail = entry_view(D(pos['amount'])/D(10)**runtime.pool.token_decimals,
            D(pos['cost_quote']), pos['entry_fees']['usd'], pos['entry_rate'],
            total_usd=pos.get('entry_cost_usd'))
    else:
        runtime.trade_detail = None
    runtime.open_estimate = None
    runtime.strategy.bought(entry, now=time.monotonic())
    mark(runtime, 'execution_applied')
    runtime.record_market("execution", side="BUY", price=str(entry))
    runtime.exit_return = None
    # Publish settled holdings before the chart marker. Monitor shutdown and
    # later RPC reads must not leave a filled trade paired with old UI state.
    runtime.status()
    runtime.event.emit("trade_marker", {"mode": runtime.mode, "side": "BUY", "price": str(entry)})
    runtime.log.emit(f"{runtime.mode} BUY: исполнение {execution:.10g}; база TP/SL {entry:.10g}")


def exit_read(runtime: Worker, read, *, stopping=False):
    """Retry only the supplied price/quote read; never wrap swap or send."""
    def notify(value):
        runtime.exit_retry = value
        if value:
            runtime.record_market('exit_read_retry', **value)
        # Do not turn an active, known LIVE operation into a UI recovery latch.
        runtime.event.emit('exit_retry', value)

    def attempt(index):
        operation = runtime.store.data.get('operation')
        if operation and any(t.get('status') != 'confirmed' for t in operation.get('transactions', [])):
            raise UncertainTransaction('Результат транзакции неизвестен; повтор выхода заблокирован')
        source = runtime.chain
        if index and runtime.backup_chain is not None:
            # Includes network, canonical pool, freshness and fork checks.
            runtime.backup_price()
            source = runtime.backup_chain
        return read(source)

    return retry_read(attempt,
        cancelled=lambda: runtime.quit_event.is_set() or (runtime.stop_event.is_set() and not stopping),
        wait=lambda delay: runtime.quit_event.wait(delay) if stopping else runtime.stop_event.wait(delay),
        notify=notify)


@monitor_execution
@timed("worker.close_position")
def close_position(runtime: Worker, reason):
    if runtime.mode == "LIVE":
        runtime.require_live()
        position = runtime.position()
        if not position:
            return
        runtime.live.begin("SELL " + runtime.pool.token)
        amount = min(position["amount"], runtime.chain.balance(runtime.pool.token, runtime.live.owner))
        if not amount:
            raise ValueError("Кэш позиции не совпадает с балансом; нужна сверка")
        received = runtime.live.swap(runtime.pool, amount, False, runtime.strategy.settings.slippage,
            quote_reader=lambda pool, amount, buy: runtime.exit_read(
                lambda source: source.quote(pool, amount, buy), stopping=reason == "STOP"))
        if isinstance(received, int) and 'cost_quote' in position:
            pnl = D(received)/D(10)**runtime.pool.quote_decimals - D(position['cost_quote'])
            key = runtime.live.owner.lower() + ':' + runtime.pool.quote.lower()
            ledger = runtime.store.data.setdefault('realized_quote', {})
            ledger[key] = str(D(ledger.get(key, '0')) + pnl)
            runtime.log.emit(f'LIVE P&L: {pnl:+.8g} базового актива без газа; газ отдельно в журнале BNB')
        if isinstance(received, int):
            record_close(runtime.store, runtime.live.owner, runtime.pool, position, received,
                         getattr(runtime.live, 'operation', None), runtime.rates.snapshot(runtime.pool.quote),
                         inventory_matches=amount == position['amount'])
        if isinstance(received, int):
            entry_detail = entry_view(D(position['amount'])/D(10)**runtime.pool.token_decimals,
                position.get('cost_quote'), position.get('entry_fees', {}).get('usd'),
                position.get('entry_rate'), total_usd=position.get('entry_cost_usd'))
            runtime.trade_detail = exit_view(entry_detail, D(amount)/D(10)**runtime.pool.token_decimals,
                D(received)/D(10)**runtime.pool.quote_decimals,
                operation_fees(getattr(runtime.live, 'operation', None))['usd'], runtime.rates.snapshot(runtime.pool.quote),
                complete=amount == position['amount'] and 'cost_quote' in position)
        runtime.set_position(0, 0)
        runtime.live.finish()
        price = runtime.current_price or D(position["entry"])
    else:
        if not runtime.paper.position:
            return
        if runtime.mode == 'PAPER' and reason != 'STOP':
            runtime.stop_event.wait(runtime.paper_policy.latency_seconds)
        mark(runtime, 'paper_delay_finished')
        price = runtime.exit_read(lambda source: runtime.read_price() if source is runtime.chain else runtime.backup_price(),
                               stopping=reason == "STOP")
        mark(runtime, 'fill_price_read')
        paper_cost = runtime.paper.cost
        quantity = runtime.paper.position
        if runtime.mode == 'PAPER' and callable(getattr(runtime.chain, 'quote', None)):
            amount = raw_amount(runtime.paper.position, runtime.pool.token_decimals)
            source, output = runtime.exit_read(lambda source: (source, getattr(source, 'paper_quote', source.quote)(
                runtime.pool, amount, False)), stopping=reason == 'STOP')
            mark(runtime, 'fill_quote_received')
            paper_fee = runtime.paper_operation_cost()
            runtime.record_quote(source, 'paper_fill', 'SELL', amount, output)
            pnl = runtime.paper.sell_quoted(D(output)/D(10)**runtime.pool.quote_decimals, paper_fee)
            rate = runtime.rates.snapshot(runtime.pool.quote)
            runtime.trade_detail = exit_view(runtime.trade_detail, quantity,
                D(output)/D(10)**runtime.pool.quote_decimals, marked_value(paper_fee, rate), rate)
        else:
            pnl = runtime.paper.sell(price)
        proceeds_usd = marked_value(paper_cost+pnl,
            runtime.rates.snapshot(runtime.pool.quote)) if runtime.mode == 'PAPER' and runtime.pool else None
        runtime.paper_usd['closed'] += 1
        if proceeds_usd is None or runtime.paper_usd['entry'] is None:
            runtime.paper_usd['missing'] += 1
            if runtime.trade_detail is not None:
                runtime.trade_detail['net_usd'] = None
        else:
            closed_delta = D(proceeds_usd)-D(runtime.paper_usd['entry'])
            runtime.paper_usd['value'] += closed_delta
            if runtime.trade_detail is not None:
                # Show the ledger result, not a differently ordered Decimal recomputation.
                runtime.trade_detail['net_usd'] = str(closed_delta)
        runtime.paper_usd['entry'] = None
        runtime.log.emit(f"PAPER P&L: {pnl:+.8g} базового актива (стоимость операции по модели; token tax не учтён)")
    runtime.open_estimate = None
    runtime.strategy.sold(price, reason, now=time.monotonic())
    mark(runtime, 'execution_applied')
    runtime.record_market("execution", side="SELL", price=str(price), reason=reason)
    runtime.exit_return = None
    runtime.status()
    runtime.event.emit("trade_marker", {"mode": runtime.mode, "side": "SELL", "price": str(price)})
    if runtime.mode == "LIVE":
        # SELL is already accounted for if this independent read fails.
        runtime.strategy.base = runtime.read_price()
        runtime.strategy.last_price = runtime.strategy.base
        runtime.strategy.last_time = runtime.price_time
    runtime.halt_reason = ""
    runtime.log.emit(f"{runtime.mode} SELL: {reason}")
