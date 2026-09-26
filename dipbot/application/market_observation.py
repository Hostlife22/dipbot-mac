from __future__ import annotations
from dipbot.application.messages import CommandKind, EventKind
from dipbot.application.errors import safe_error
from dipbot.observability.telemetry import timed
from dipbot.observability.cycle_trace import signal_cycle
import time
from requests.exceptions import ConnectionError as RPCConnectionError, Timeout as RPCTimeout, HTTPError

from web3.exceptions import Web3RPCError

from dipbot.domain.strategy import D, raw_amount
from dipbot.execution.errors import UncertainTransaction


from dipbot.market.exit_reads import transient
from dipbot.domain.entry_guard import EntryRejected
from dipbot.market.chain import StaleBlock
from dipbot.execution.accounting import marked_value


from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from dipbot.application.worker import Worker

@timed("worker.read_price")
def read_price(runtime: Worker, force_chain=False):
    if runtime.mode == "DEMO" and not force_chain:
        # Deterministic local market; no network, funds or signing.
        runtime.tick += 1
        # Include a sudden dip: smooth declines reanchor every two moves.
        cycle = ("1", "1.01", "1.02", "0.97", "0.98", "1.00", "1.01", "1")
        price = D(cycle[(runtime.tick - 1) % len(cycle)])
    else:
        runtime.require_chain()
        if not runtime.pool:
            raise ValueError("Пул не выбран")
        price = runtime.market_price()
    runtime.current_price = price
    runtime.price_time = time.monotonic()
    demo = runtime.mode == 'DEMO' and not force_chain
    source_chain = runtime.backup_chain if runtime.market_source != 'BSC' else runtime.chain
    header = getattr(source_chain, 'price_block', None) if not demo else None
    runtime.emit_event(EventKind.PRICE_CONTEXT, {'source': 'DEMO' if demo else getattr(runtime.chain, 'price_source', 'BSC'),
                                    'rpc_source': runtime.market_source,
                                    'same_block_cache': bool(getattr(source_chain, 'price_cache_hit', False)) if not demo else False,
                                    'block': header['number'] if header else None,
                                    'block_timestamp': header.get('timestamp') if header else None,
                                    'quote': '' if demo else runtime.pool.quote})
    runtime.record_market('price', price=str(price), block=header['number'] if header else None,
                       block_hash=bytes(header['hash']).hex() if header else None,
                       block_timestamp=header.get('timestamp') if header else None,
                       source='DEMO' if demo else 'BSC')
    runtime.emit_event(EventKind.PRICE, str(price))
    return price


def adaptive_market_price(runtime: Worker):
    source_id = runtime.rpc_health.choose(time.monotonic())
    for attempt in range(2):
        source = runtime.backup_chain if source_id else runtime.chain
        started = time.monotonic()
        try:
            price = runtime.backup_price() if source_id else runtime.chain.price(runtime.pool)
            header = getattr(source, 'price_block', None)
            previous = runtime.last_market_header
            if header and previous and (header['number'] < previous['number'] or (
                    header['number'] == previous['number'] and header['hash'] != previous['hash'])):
                raise TimeoutError('RPC вернул более старый блок или другую ветвь')
        except (RPCConnectionError, RPCTimeout, TimeoutError, HTTPError, Web3RPCError) as exc:
            if not transient(exc):
                raise
            runtime.rpc_health.failure(source_id, time.monotonic())
            other = 1-source_id
            if attempt or time.monotonic() < runtime.rpc_health.blocked_until[other]:
                raise
            source_id = other
            continue
        runtime.rpc_health.success(source_id, time.monotonic()-started, time.monotonic())
        runtime.last_market_header = dict(header) if header else previous
        runtime.market_source = 'BSC · резервный RPC' if source_id else 'BSC'
        return price


def market_price(runtime: Worker):
    if runtime.adaptive_rpc and runtime.backup_chain is not None:
        return runtime.adaptive_market_price()
    # Execution always keeps runtime.chain / LiveTrader.chain on the primary.
    if runtime.backup_chain is not None and time.monotonic() < runtime.backup_until:
        return runtime.backup_price()
    try:
        price = runtime.chain.price(runtime.pool)
    except (RPCConnectionError, RPCTimeout, TimeoutError, HTTPError, Web3RPCError) as exc:
        if not transient(exc):
            raise
        if runtime.backup_chain is None:
            raise
        runtime.backup_until = time.monotonic() + 30
        runtime.log.emit('Основной RPC недоступен: котировки через резервный. Отправка сделок остаётся на основном RPC')
        return runtime.backup_price()
    if runtime.market_source != 'BSC':
        runtime.log.emit('Котировки снова поступают с основного RPC')
    runtime.market_source = 'BSC'
    return price


def backup_price(runtime: Worker):
    if runtime.backup_verified_pool != runtime.pool:
        runtime.backup_chain.check()
        verified = runtime.backup_chain.verify_pool(runtime.pool.address, runtime.pool.token)
        if verified != runtime.pool:
            raise ValueError('Резервный RPC вернул другой пул/маршрут; торговля приостановлена')
        runtime.backup_verified_pool = verified
    price = runtime.backup_chain.price(runtime.pool)
    primary = getattr(runtime.chain, 'price_block', None)
    backup = getattr(runtime.backup_chain, 'price_block', None)
    if primary and backup and (backup['number'] < primary['number'] or
            (backup['number'] == primary['number'] and backup['hash'] != primary['hash'])):
        raise StaleBlock('Резервный RPC отстаёт или вернул другую ветвь цепочки')
    runtime.market_source = 'BSC · резервный RPC'
    return price


def watchable_position(runtime: Worker):
    return (runtime.mode in ('PAPER', 'LIVE') and runtime.pool is not None
            and bool(runtime.position() if runtime.mode == 'LIVE' else runtime.paper.position))


def watch_position(runtime: Worker):
    # Serial read-only observation: no strategy signals, persistence or execution.
    runtime.position_watch_error = ""
    runtime.open_estimate = None
    if runtime.store.data.get('operation'):
        runtime.position_watch_error = "Результат транзакции неизвестен; требуется сверка"
        return
    try:
        runtime.observe(read_only=True)
        if runtime.quote_unavailable:
            runtime.position_watch_error = "Ошибка RPC · повтор чтения с паузой до 5 с"
    except Exception as exc:
        # Monitoring must not raise modal errors or change the trading halt reason.
        runtime.open_estimate = None
        runtime.quote_unavailable = True
        runtime.quote_failures += 1
        runtime.position_watch_error = safe_error(exc)


@timed("worker.observe")
def observe(runtime: Worker, read_only=False):
    # Retry only a failed read, never an execution or post-receipt failure.
    if runtime.store.data.get("operation"):
        raise UncertainTransaction("Незавершённая операция: автоматические сделки заблокированы")
    runtime.open_estimate = None
    try:
        price = runtime.read_price()
        exit_return = None
        if read_only or (runtime.strategy.entry is not None and runtime.strategy.exit_policy.tp_sl_basis == 'quote'):
            if runtime.mode == 'LIVE':
                position = runtime.position()
                amount, cost = position['amount'], D(position.get('cost_quote', 0))
            else:
                cost = runtime.paper.cost
                amount = raw_amount(runtime.paper.position, runtime.pool.token_decimals) if runtime.pool else 0
            if cost <= 0 and not read_only:
                raise ValueError('Неизвестна себестоимость позиции: TP/SL по выходу недоступен')
            if runtime.mode == 'DEMO':
                proceeds = runtime.paper.position*price
            else:
                source = runtime.backup_chain if runtime.market_source != 'BSC' else runtime.chain
                exit_raw = source.exit_quote(runtime.pool, amount)
                runtime.record_quote(source, 'position_mark' if read_only else 'exit_signal', 'SELL', amount, exit_raw)
                proceeds = D(exit_raw)/D(10)**runtime.pool.quote_decimals
            if time.monotonic()-runtime.price_time > runtime.strategy.settings.max_gap:
                raise TimeoutError("Снимок цены устарел во время котировки выхода")
            if runtime.mode == 'PAPER':
                proceeds -= runtime.paper_operation_cost()
            exit_return = (proceeds/cost-1)*100 if cost > 0 else None
            rate = runtime.rates.snapshot(runtime.pool.quote) if runtime.pool else None
            entry_usd = (runtime.position().get('entry_cost_usd') if runtime.mode == 'LIVE'
                         else runtime.paper_usd['entry'])
            value = marked_value(proceeds, rate)
            runtime.open_estimate = {'at': time.monotonic(), 'value_usd': value,
                'pnl_usd': str(D(value)-D(entry_usd)) if value is not None and entry_usd is not None else None,
                'excludes_exit_gas': runtime.mode == 'LIVE'}
        runtime.exit_return = str(exit_return) if exit_return is not None else None
    except (RPCConnectionError, RPCTimeout, TimeoutError, HTTPError, Web3RPCError) as exc:
        if not transient(exc):
            raise
        runtime.record_market('read_error', type=type(exc).__name__)
        runtime.quote_failures += 1
        if not runtime.quote_unavailable:
            runtime.log.emit("Котировки недоступны: входы запрещены, повтор чтения с паузой до 5 с. Открытая позиция сохраняется")
        runtime.quote_unavailable = True
        runtime.exit_return = None
        return
    if runtime.quote_unavailable:
        runtime.log.emit("Чтение котировок восстановлено; проверка позиции возобновлена")
    runtime.quote_unavailable = False
    runtime.quote_failures = 0
    if read_only:
        return
    now = time.monotonic()
    if runtime.entry_notice and runtime.strategy.entry is None:
        if now < runtime.entry_retry_at:
            return
        # Require a new signal from a fresh baseline, not the rejected signal.
        runtime.strategy.reset_anchor()
        runtime.entry_notice = ""
    if (runtime.strategy.entry is None and runtime.strategy.last_time is not None
            and now - runtime.strategy.last_time > runtime.strategy.settings.max_gap):
        runtime.log.emit("Разрыв котировок > 0.55 с: база DIP сброшена")
    source = runtime.backup_chain if runtime.market_source != 'BSC' else runtime.chain
    header = getattr(source, 'price_block', None) if runtime.mode != 'DEMO' else None
    observation_id = (header['number'], bytes(header['hash']), price) if header else None
    usd_mark = runtime.rates.snapshot(runtime.pool.quote) if runtime.pool and runtime.mode != 'DEMO' else None
    runtime.record_market('observation', price=str(price), block=header['number'] if header else None,
                       block_hash=bytes(header['hash']).hex() if header else None,
                       quote_usd=usd_mark['usd'] if usd_mark else None,
                       quote_usd_observed_at=usd_mark['observed_at'] if usd_mark else None)
    action = runtime.strategy.observe(price, now, observation_id=observation_id, exit_return=exit_return)
    if runtime.mode == 'LIVE' and runtime.strategy.entry is not None and runtime.strategy.peak_price is not None:
        position = runtime.position()
        if position and runtime.strategy.peak_price > D(position.get('peak_price', position['entry'])):
            position['peak_price'] = str(runtime.strategy.peak_price)
            runtime.store.save()
    if runtime.stop_event.is_set():
        return
    if action:
        runtime.record_market('signal', action=action, price=str(price), base=str(runtime.strategy.base),
                           entry=str(runtime.strategy.entry))
    if action == "BUY":
        try:
            with signal_cycle(runtime, action, header):
                runtime.open_position()
        except EntryRejected as exc:
            if runtime.mode not in ("PAPER", "LIVE") or runtime.store.data.get("operation") or runtime.paper.position or runtime.position():
                raise
            runtime.entry_retry_at = time.monotonic() + 5.0
            runtime.entry_notice = str(exc) + "; пауза 5 с, затем новый сигнал DIP"
            runtime.log.emit("Вход пропущен: " + runtime.entry_notice)
    elif action:
        with signal_cycle(runtime, action, header):
            runtime.close_position(action)
        if runtime.strategy.stopped:
            runtime.running = False
