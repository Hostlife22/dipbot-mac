from __future__ import annotations
from dipbot.application.messages import CommandKind, EventKind
from dataclasses import asdict, replace
import time

from eth_account import Account

from dipbot.market.chain import Chain, WBNB, address, profiles
from dipbot.persistence.vault import Vault
from dipbot.persistence import dynamic
from dipbot.persistence import wallet_registry
from dipbot.market.routes import seed_preference
from dipbot.domain.strategy import D, Settings, Strategy, raw_amount, minimum_out
from dipbot.execution.trader import LiveTrader
from dipbot.execution.paper import PaperTrader
from dipbot.execution.errors import UncertainTransaction
from dipbot.execution.reconciliation import reconcile_receipts


from dipbot.domain.signal_policy import SignalPolicy
from dipbot.market.head_feed import HeadFeed, HeadSchedule
from dipbot.research.market_tape import MarketTape
from dipbot.domain.sizing import SizingPolicy
from dipbot.domain.exit_policy import ExitPolicy
from dipbot.market.rpc_health import RpcHealth
from dipbot.domain.cost_policy import CostPolicy
from dipbot.domain.paper_policy import PaperPolicy
from dipbot.execution.accounting import accounting_report


from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from dipbot.application.worker import Worker

def configure(runtime: Worker, data):
    mode = data["mode"]
    policy = SignalPolicy.parse(data.get("signal_policy", {}))
    sizing = SizingPolicy.parse(data.get("sizing", {}))
    paper_policy = PaperPolicy.parse(data.get("paper_policy", {}))
    cost_policy = CostPolicy.parse(data.get("entry_cost_policy", {}))
    exit_policy = ExitPolicy.parse(data.get("exit_policy", {}))
    settings = Settings(**{k: D(v) for k, v in data["settings"].items()})
    requested_amount = settings.amount
    if sizing.unit == 'usd':
        if mode == 'DEMO' or runtime.pool is None:
            raise ValueError('AMOUNT в USD требует PAPER/LIVE и выбранный пул')
        settings = replace(settings, amount=sizing.amount_quote(requested_amount, runtime.pool.quote, runtime.rates))
    interval = float(data["interval"])
    if not 0.1 <= interval <= 0.5:
        raise ValueError("Интервал от 0.1 до 0.5 секунд (защита разрыва: 0.55 с)")
    if mode != "DEMO":
        runtime.require_chain()
        if not runtime.pool:
            raise ValueError("Выберите проверенный пул через AutoPair / CHECK + ADD")
        if address(data["token"]) != runtime.pool.token or address(data["pool"]) != runtime.pool.address:
            raise ValueError("Адреса изменились: заново выберите и проверьте пул")
        if data.get("router", "AUTO") not in ("AUTO", runtime.pool.router):
            raise ValueError("Router изменился: повторите AutoPair / CHECK POOL")
        if data.get("generation") is not None and data["generation"] != runtime.pool_generation:
            raise ValueError("Ввод изменился: заново проверьте пул")
        runtime.chain.max_block_age = policy.max_block_age
        if runtime.backup_chain is not None:
            runtime.backup_chain.max_block_age = policy.max_block_age
        runtime.chain.verify_pool(runtime.pool.address, runtime.pool.token)
    if mode != runtime.mode and (runtime.paper.position or runtime.position()):
        raise ValueError("Закройте текущую позицию перед сменой режима")
    if mode != runtime.mode:
        runtime.trade_detail = None
        runtime.open_estimate = None
    live = None
    if mode == "LIVE":
        key = Vault().get("wallet")
        if not key:
            raise ValueError("Сначала сохраните отдельный кошелёк в Keychain")
        live = LiveTrader(runtime.chain, key, runtime.store, D(data["gas"]), runtime.log.emit)
        live.broadcast_chain = runtime.broadcast_chain
        if runtime.broadcast_chain is not None:
            runtime.broadcast_chain.max_block_age = policy.max_block_age
        live.trade_router = runtime.pool.router
        live.rates = runtime.rates
        live.stop_requested = runtime.stop_event.is_set
        live.reserve_wei = raw_amount(sizing.reserve_bnb, 18) if sizing.reserve_bnb else 0
        if runtime.store.data.get("operation"):
            raise UncertainTransaction("Есть незавершённая операция: используйте сверку в настройках")
        pair_name = next((n for n,t in dynamic.catalog(runtime.store,runtime.pool.router).items()
                          if address(t) == runtime.pool.quote), runtime.pool.quote)
        wallet_registry.register(runtime.store, live.owner, runtime.pool, pair_name)
    if runtime.pool and runtime.pool.router == "V3":
        interval = max(interval, 0.103)
    if mode != "LIVE":
        context = (mode,) if mode == "DEMO" else (mode, runtime.pool.token.lower(), runtime.pool.quote.lower())
        if runtime.paper_context != context:
            if runtime.paper.position and runtime.paper_context is not None:
                raise ValueError("Сначала закройте позицию предыдущего PAPER-рынка")
            if not runtime.paper.position:
                runtime.paper = PaperTrader(settings.slippage)
                runtime.paper_usd = {"value": D(0), "closed": 0, "missing": 0, "entry": None}
            runtime.paper_context = context
    old_cooldown = runtime.strategy.cooldown_until if mode == runtime.mode else None
    runtime.mode, runtime.interval, runtime.live = mode, interval, live
    runtime.sizing, runtime.requested_amount = sizing, requested_amount
    runtime.paper_policy = paper_policy
    runtime.cost_policy, runtime.gas_gwei = cost_policy, D(data["gas"])
    old_entry = runtime.strategy.entry
    old_entry_time, old_peak = runtime.strategy.entry_time, runtime.strategy.peak_price
    runtime.strategy = Strategy(settings, policy, exit_policy)
    runtime.strategy.cooldown_until = old_cooldown
    if mode == "LIVE" and runtime.position():
        runtime.strategy.entry = D(runtime.position()["entry"])
        runtime.strategy.peak_price = D(runtime.position().get('peak_price', runtime.position()['entry']))
        started = runtime.position().get('opened_at')
        if exit_policy.max_hold_seconds and started is None:
            raise ValueError('В старой позиции нет времени входа; отключите выход по времени или выполните ручной SELL')
        runtime.strategy.entry_time = time.monotonic()-max(0,time.time()-started) if started is not None else None
    elif mode != "LIVE" and runtime.paper.position:
        runtime.strategy.entry = old_entry
        runtime.strategy.entry_time, runtime.strategy.peak_price = old_entry_time, old_peak
    runtime.paper.slippage = settings.slippage
    runtime.entry_retry_at = 0.0
    runtime.entry_notice = ""
    runtime.halt_reason = ""
    runtime.quote_unavailable = False
    runtime.quote_failures = 0
    previous_closed = runtime.recorder is None or runtime.recorder.close()
    runtime.recorder = None
    runtime.recorder_notice = False
    if data.get('record_market', False) and previous_closed:
        try:
            runtime.recorder = MarketTape(runtime.store.path.parent / 'market-recordings', {
                'mode': runtime.mode, 'pool': asdict(runtime.pool) if runtime.pool else None,
                'settings': asdict(settings), 'signal_policy': policy.export(), 'sizing':sizing.export(),
                'requested_amount':str(requested_amount), 'exit_policy':exit_policy.export(), 'entry_cost_policy':cost_policy.export(), 'paper_policy':paper_policy.export(),
                'starts_with_position': runtime.strategy.entry is not None})
            runtime.log.emit('Запись рынка включена: локальный архив market-recordings (части до 10 MiB, архив до 200 MiB)')
        except OSError:
            runtime.log.emit('Запись рынка недоступна: проверьте свободное место и лимит архива')


def command(runtime: Worker, name, data):
    if runtime.stop_event.is_set() and name in ("start", "buy", "convert", "sweep"):
        raise ValueError("STOP запрошен: новая торговая операция отменена")
    if runtime.running and name not in ("sell", "accounting_report"):
        raise ValueError("Сначала остановите BOT")
    if name in ("connect", "discover", "verify", "select", "wallet", "remove_profile") and (runtime.paper.position or runtime.position()):
        raise ValueError("Сначала закройте текущую позицию")
    if name == CommandKind.CONNECT:
        chain = Chain(data["rpc"])
        block = chain.check()
        backup = None
        if data.get('backup_rpc', '').strip():
            backup = Chain(data['backup_rpc'].strip())
            backup.restrict_to_reads()
            backup.check()
        broadcaster = None
        if data.get('send_rpc','').strip():
            broadcaster = Chain(data['send_rpc'].strip())
            broadcaster.check()
        feed = HeadFeed(data['ws_rpc'].strip()) if data.get('ws_rpc', '').strip() else None
        if runtime.gap_recovery is not None:
            runtime.gap_recovery.stop()
            runtime.gap_recovery = None
        if runtime.head_feed is not None:
            runtime.head_feed.stop()
        runtime.head_feed = feed.start() if feed is not None else None
        runtime.head_schedule = HeadSchedule()
        runtime.chain = chain
        runtime.backup_chain = backup
        runtime.broadcast_chain = broadcaster
        if broadcaster is not None:
            runtime.log.emit("Отправка через отдельный RPC; чтение receipts через основной")
        runtime.backup_until = 0.0
        runtime.rpc_health = RpcHealth()
        runtime.adaptive_rpc = bool(data.get('adaptive_rpc', False))
        runtime.last_market_header = None
        runtime.backup_verified_pool = None
        runtime.pool = None
        runtime.emit_event(EventKind.POOLS, [])
        runtime.log.emit(f"BSC подключена, chainId 56, блок {block}")
        if data.get("save"):
            Vault().save("rpc", data["rpc"])
            Vault().save('backup_rpc', data.get('backup_rpc', '').strip())
            Vault().save('ws_rpc', data.get('ws_rpc', '').strip())
            Vault().save('send_rpc', data.get('send_rpc', '').strip())
    elif name == CommandKind.WALLET:
        account = Account.from_key(data["key"])
        Vault().save("wallet", data["key"])
        runtime.store.data["wallet_address"] = account.address
        runtime.store.save()
        runtime.emit_event(EventKind.WALLET, account.address)
        runtime.log.emit("Кошелёк сохранён в macOS Keychain: " + account.address)
    elif name == CommandKind.DISCOVER:
        runtime.require_chain()
        generation = data.get("generation")
        if not runtime.discovery_current(generation):
            return
        runtime.pool = None
        runtime.pool_generation = None
        runtime.discovery_emit(generation, "pools", [])
        catalogs = {}
        routers = ("V2", "V3") if data["router"] == "AUTO" else (data["router"],)
        for router in routers:
            catalog = dynamic.catalog(runtime.store, router)
            catalogs[router] = catalog if data["quote"] == "ALL" else {
                name: token for name, token in catalog.items() if name == data["quote"]}
        result = runtime.chain.resolve_address(data["token"], catalogs)
        if not runtime.discovery_current(generation):
            return
        runtime.discovery_emit(generation, "pools", [c.pool for c in result.candidates if c.ready])
        runtime.log.emit(f"AutoPair: {result.state}; найдено {len(result.candidates)} пулов")
        if result.state == "RESOLVED":
            runtime.select_pool(result.selected.pool, generation=generation)
        runtime.discovery_emit(generation, "autopair", result.state)
    elif name == CommandKind.COMPARE_ROUTES:
        runtime.require_chain()
        generation = data.get('generation')
        if not runtime.discovery_current(generation):
            return
        from dipbot.market.route_comparison import compare
        reference = data['reference']
        amount = SizingPolicy.parse(data['sizing']).amount_quote(D(data['amount']), reference.quote, runtime.rates)
        report = compare(runtime.chain, data['pools'], reference, amount,
            D(data['maximum']), CostPolicy.parse(data['cost_policy']), D(data['gas']), runtime.rates,
            cancelled=lambda: not runtime.discovery_current(generation))
        runtime.discovery_emit(generation, 'route_comparison', report)
    elif name == CommandKind.VERIFY:
        runtime.require_chain()
        pool = runtime.chain.verify_pool(data["pool"], data["token"])
        generation = data.get("generation")
        if not runtime.discovery_current(generation):
            return
        runtime.discovery_emit(generation, "pools", [pool])
        runtime.select_pool(pool, generation=generation)
    elif name == CommandKind.SELECT:
        runtime.select_pool(data["pool"], generation=data.get("generation"))
    elif name == CommandKind.ADD_PROFILE:
        runtime.require_chain()
        if data.get("generation") is not None and data["generation"] != runtime.pool_generation:
            raise ValueError("Ввод изменился: повторите AutoPair или CHECK POOL")
        if not runtime.pool:
            raise ValueError("Сначала CHECK + ADD или AutoPair")
        # Recheck target pool too: selection may predate a liquidity change.
        runtime.pool = runtime.chain.verify_pool(runtime.pool.address, runtime.pool.token)
        # Quote round trip is checked without a private key or any transaction.
        helper = object.__new__(LiveTrader)
        helper.chain = runtime.chain
        helper.store = runtime.store
        helper.trade_router = runtime.pool.router
        sample = 10**15  # Native LIVE_PAIR_SAMPLE_BNB_WEI: 0.001 BNB.
        if runtime.pool.quote != address(WBNB):
            helper.converter_preference = dynamic.preference(runtime.store, runtime.pool.quote, runtime.pool.router) or seed_preference(runtime.pool.quote)
            try:
                buy_route = helper.conversion_route(WBNB, runtime.pool.quote, sample)
            except ValueError:
                # Route failure permits ETH fallback; RPC timeouts remain errors.
                if helper.converter_preference["converter_mode"] == "via_eth_v3":
                    raise
                helper.converter_preference = {"converter_mode": "via_eth_v3", "converter_fee": 500}
                buy_route = helper.conversion_route(WBNB, runtime.pool.quote, sample)
            output = runtime.chain.quote_route(buy_route, sample)
            minimum_out(output, D("3"))  # Native preview slippage is 3%, not a loss cap.
            returned = runtime.chain.quote_route(buy_route, output, reverse=True)
            if returned * 10000 < sample * 8500:
                raise ValueError("Round-trip loss превышает 15%")
            if runtime.pool.quote not in profiles().values():
                symbol = runtime.chain.symbol(runtime.pool.quote) if hasattr(runtime.chain, "symbol") else None
                try:
                    dynamic.upsert(runtime.store, runtime.pool, buy_route, max(0, (sample-returned)*10000//sample), symbol=symbol)
                finally:
                    runtime.emit_event(EventKind.PROFILES, runtime.store.data.get("dynamic_profiles", {}))
                runtime.emit_event(EventKind.SELECTED, runtime.pool)
        runtime.log.emit("ADDED: базовый актив проверен для конвертера; котировка не проверяет token tax / blacklist")
    elif name == CommandKind.REMOVE_PROFILE:
        if runtime.store.data.get("operation"):
            raise UncertainTransaction("Сначала выполните сверку незавершённой операции")
        runtime.require_chain()
        symbol = data["symbol"]
        dynamic_profiles = runtime.store.data.get("dynamic_profiles", {})
        if symbol not in dynamic_profiles:
            raise ValueError("Встроенные профили удалять нельзя")
        owner = address(data["wallet"])
        token = dynamic_profiles[symbol]
        if runtime.chain.balance(token, owner):
            raise ValueError("Сначала продайте остаток базового актива")
        for raw in runtime.store.data.get("known_pools", {}).values():
            if raw["quote"].lower() == token.lower() and runtime.chain.balance(raw["token"], owner):
                raise ValueError("Сначала продайте остаток target этой базы")
        for key, position in runtime.store.data.get("positions", {}).items():
            if key.startswith(owner.lower() + ":") and position["pool"]["quote"].lower() == token.lower():
                raise ValueError("Сначала закройте сохранённую позицию этой базы")
        try:
            dynamic.remove(runtime.store, symbol)
        finally:
            if symbol not in runtime.store.data.get("dynamic_profiles", {}):
                runtime.pool = None
                runtime.pool_generation = None
                runtime.emit_event(EventKind.POOLS, [])
                runtime.emit_event(EventKind.PROFILES, runtime.store.data.get("dynamic_profiles", {}))
                runtime.emit_event(EventKind.PROFILE_REMOVED, symbol)
    elif name in ("start", "buy"):
        runtime.configure(data)
        if runtime.mode == "LIVE":
            other_positions = [key for key in runtime.store.data.get("positions", {})
                               if key.startswith(runtime.live.owner.lower() + ":")
                               and key != runtime.position_key()]
            if other_positions:
                raise ValueError("Есть сохранённая позиция другого пула: выберите её пул или используйте SELL WALLET → BNB")
        if runtime.stop_event.is_set():
            raise ValueError("STOP запрошен во время подготовки")
        if name == CommandKind.START:
            runtime.running = True
            runtime.log.emit(f"START {runtime.mode}: DIP {runtime.strategy.settings.dip}% / TP {runtime.strategy.settings.take_profit}% / SL {runtime.strategy.settings.stop_loss}%")
        else:
            runtime.read_price()
            if runtime.stop_event.is_set():
                raise ValueError("STOP запрошен во время чтения цены")
            runtime.open_position()
    elif name == CommandKind.SELL:
        runtime.close_position("MANUAL")
    elif name == CommandKind.ACCOUNTING_REPORT:
        owner = runtime.live.owner if runtime.live else runtime.store.data.get('wallet_address', '')
        runtime.emit_event(EventKind.ACCOUNTING_REPORT, accounting_report(runtime.store, owner))
    elif name == CommandKind.BALANCE:
        runtime.require_chain()
        owner = address(data["wallet"])
        balances = {"BNB": str(D(runtime.chain.w3.eth.get_balance(owner)) / 10**18)}
        for index, (symbol, token) in enumerate((profiles() | runtime.store.data.get("dynamic_profiles", {})).items()):
            if index % 10 == 0:
                runtime.log.emit(f"Чтение балансов: {index+1}…")
            try:
                balances[symbol] = str(D(runtime.chain.balance(token, owner)) / D(10)**runtime.chain.decimals(token))
            except Exception:
                balances[symbol] = "?"
        if runtime.pool:
            balances["TARGET"] = str(D(runtime.chain.balance(runtime.pool.token, owner)) / D(10)**runtime.pool.token_decimals)
        runtime.emit_event(EventKind.BALANCES, balances)
    elif name == CommandKind.CONVERT:
        runtime.configure(data)
        runtime.require_live()
        if runtime.stop_event.is_set():
            raise ValueError("STOP запрошен во время подготовки")
        amount = raw_amount(D(data["amount"]), 18) if data["buy"] else runtime.chain.balance(runtime.pool.quote, runtime.live.owner)
        if amount <= 0:
            raise ValueError("Нулевой баланс")
        if runtime.stop_event.is_set():
            raise ValueError("STOP запрошен во время чтения баланса")
        runtime.live.begin("Converter BUY" if data["buy"] else "Converter SELL ALL")
        runtime.live.convert(runtime.pool.quote, amount, data["buy"], runtime.strategy.settings.slippage)
        runtime.live.finish()
        runtime.log.emit("Converter завершён")
    elif name == CommandKind.SWEEP:
        runtime.configure(data)
        runtime.require_live()
        runtime.sweep()
    elif name == CommandKind.CANCEL_PENDING:
        runtime.require_chain()
        if data.get('mode') != 'LIVE':
            raise ValueError('Отмена транзакции доступна только в LIVE')
        key = Vault().get('wallet')
        if not key:
            raise ValueError('Нет кошелька в Keychain')
        helper = LiveTrader(runtime.chain, key, runtime.store, D(data['gas']), runtime.log.emit)
        helper.broadcast_chain = runtime.broadcast_chain
        helper.rates = runtime.rates
        from dipbot.execution.cancellation import cancel_pending
        result = cancel_pending(helper, expected_hash=data['expected_hash'],
                                expected_gas_price=data['expected_gas_price'])
        runtime.log.emit(result)
        runtime.emit_event(EventKind.RECEIPT_REVIEW, result)
    elif name == CommandKind.COMPARE_POSITIONS:
        runtime.require_chain()
        from dipbot.execution.recovery import compare_positions
        runtime.emit_event(EventKind.POSITION_COMPARISON, compare_positions(runtime.chain, runtime.store))
    elif name in ("reconcile", "unlock"):
        runtime.require_chain()
        if name == CommandKind.RECONCILE:
            operation = runtime.store.data.get("operation")
            result = reconcile_receipts(runtime.chain, runtime.store, operation["wallet"] if operation else "")
            runtime.log.emit(result)
            runtime.emit_event(EventKind.RECEIPT_REVIEW, result)
            return
        key = Vault().get("wallet")
        if not key:
            raise ValueError("Нет кошелька в Keychain")
        helper = LiveTrader(runtime.chain, key, runtime.store, D(data["gas"]), runtime.log.emit)
        runtime.log.emit(helper.reconcile())
        if name == CommandKind.UNLOCK and runtime.store.data.get("operation"):
            operation = runtime.store.data["operation"]
            if not operation["transactions"]:
                runtime.log.emit("Операция не дошла до записи транзакции")
            # Remove cached positions: user must explicitly handle actual wallet balances.
            for key in list(runtime.store.data.get("positions", {})):
                if key.startswith(helper.owner.lower() + ":"):
                    del runtime.store.data["positions"][key]
            helper.operation = operation
            helper.finish()
            runtime.strategy.entry = None
            runtime.log.emit("Блокировка снята. Кэш позиций сброшен; остатки продавайте через SELL WALLET → BNB")
    else:
        raise ValueError("Неизвестная команда")


def select_pool(runtime: Worker, pool, *, generation=None):
    if runtime.paper.position or runtime.position():
        raise ValueError("Сначала закройте позицию текущего пула")
    verified = runtime.chain.verify_pool(pool.address, pool.token)
    if not runtime.discovery_current(generation):
        return
    if runtime.paper_context is not None and runtime.paper_context != (
            runtime.mode, verified.token.lower(), verified.quote.lower()):
        runtime.paper = PaperTrader(runtime.paper.slippage)
        runtime.paper_context = None
    runtime.trade_detail = None
    runtime.open_estimate = None
    runtime.pool = verified
    runtime.pool_generation = runtime.discovery_generation if generation is None else generation
    runtime.store.data.setdefault("known_pools", {})[runtime.pool.address.lower()] = asdict(runtime.pool)
    runtime.store.data["last_pool"] = asdict(runtime.pool)
    runtime.store.save()
    runtime.discovery_emit(generation, "selected", runtime.pool)
    runtime.log.emit("Выбран " + runtime.pool.label)
    runtime.read_price(force_chain=True)
