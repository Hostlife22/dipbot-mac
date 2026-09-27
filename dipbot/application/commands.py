from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import asdict, replace
from typing import TYPE_CHECKING, Any

from eth_account import Account

from dipbot.application.messages import CommandKind, EventKind
from dipbot.domain.assets import WBNB
from dipbot.domain.cost_policy import CostPolicy
from dipbot.domain.exit_policy import ExitPolicy
from dipbot.domain.paper_policy import PaperPolicy
from dipbot.domain.signal_policy import SignalPolicy
from dipbot.domain.sizing import SizingPolicy
from dipbot.domain.strategy import D, Settings, Strategy, minimum_out, raw_amount
from dipbot.execution.accounting import accounting_report
from dipbot.execution.errors import UncertainTransaction
from dipbot.execution.paper import PaperTrader
from dipbot.execution.reconciliation import reconcile_receipts
from dipbot.execution.trader import LiveTrader
from dipbot.market.chain import Chain, Pool, address, profiles
from dipbot.market.head_feed import HeadFeed, HeadSchedule
from dipbot.market.routes import seed_preference
from dipbot.market.rpc_health import RpcHealth
from dipbot.observability.cycle_trace import signal_cycle
from dipbot.persistence import dynamic, wallet_registry
from dipbot.persistence.vault import Vault
from dipbot.research.market_tape import MarketTape

if TYPE_CHECKING:
    from dipbot.application.contexts import CommandRuntime


def configure(runtime: CommandRuntime, data: dict[str, Any]) -> None:
    mode = data["mode"]
    policy = SignalPolicy.parse(data.get("signal_policy", {}))
    sizing = SizingPolicy.parse(data.get("sizing", {}))
    paper_policy = PaperPolicy.parse(data.get("paper_policy", {}))
    cost_policy = CostPolicy.parse(data.get("entry_cost_policy", {}))
    exit_policy = ExitPolicy.parse(data.get("exit_policy", {}))
    settings_values: dict[str, Any] = {k: D(v) for k, v in data["settings"].items()}
    settings = Settings(**settings_values)
    requested_amount = settings.amount
    if sizing.unit == "usd":
        if mode == "DEMO" or runtime.market.pool is None:
            raise ValueError("AMOUNT в USD требует PAPER/LIVE и выбранный пул")
        settings = replace(
            settings,
            amount=sizing.amount_quote(requested_amount, runtime.market.selected.quote, runtime.rates),
        )
    interval = float(data["interval"])
    if not 0.1 <= interval <= 0.5:
        raise ValueError("Интервал от 0.1 до 0.5 секунд (защита разрыва: 0.55 с)")
    if mode != "DEMO":
        runtime.require_chain()
        if not runtime.market.pool:
            raise ValueError("Выберите проверенный пул через AutoPair / CHECK + ADD")
        if (
            address(data["token"]) != runtime.market.selected.token
            or address(data["pool"]) != runtime.market.selected.address
        ):
            raise ValueError("Адреса изменились: заново выберите и проверьте пул")
        if data.get("router", "AUTO") not in ("AUTO", runtime.market.selected.router):
            raise ValueError("Router изменился: повторите AutoPair / CHECK POOL")
        if data.get("generation") is not None and data["generation"] != runtime.market.pool_generation:
            raise ValueError("Ввод изменился: заново проверьте пул")
        runtime.connections.reader.max_block_age = policy.max_block_age
        if runtime.connections.backup_chain is not None:
            runtime.connections.backup.max_block_age = policy.max_block_age
        runtime.connections.reader.verify_pool(runtime.market.selected.address, runtime.market.selected.token)
    if mode != runtime.session.mode and (runtime.session.paper.position or runtime.position()):
        raise ValueError("Закройте текущую позицию перед сменой режима")
    if mode != runtime.session.mode:
        runtime.session.trade_detail = None
        runtime.session.open_estimate = None
    live = None
    if mode == "LIVE":
        key = Vault().get("wallet")
        if not key:
            raise ValueError("Сначала сохраните отдельный кошелёк в Keychain")
        live = LiveTrader(runtime.connections.reader, key, runtime.store, D(data["gas"]), runtime.log.emit)
        live.broadcast_chain = runtime.connections.broadcast_chain
        if runtime.connections.broadcast_chain is not None:
            runtime.connections.broadcast_chain.max_block_age = policy.max_block_age
        live.trade_router = runtime.market.selected.router
        live.rates = runtime.rates
        live.stop_requested = runtime.stop_event.is_set
        live.reserve_wei = raw_amount(sizing.reserve_bnb, 18) if sizing.reserve_bnb else 0
        if runtime.store.data.get("operation"):
            raise UncertainTransaction("Есть незавершённая операция: используйте сверку в настройках")
        pair_name = next(
            (
                n
                for n, t in dynamic.catalog(runtime.store, runtime.market.selected.router).items()
                if address(t) == runtime.market.selected.quote
            ),
            runtime.market.selected.quote,
        )
        wallet_registry.register(runtime.store, live.owner, runtime.market.selected, pair_name)
    if runtime.market.pool and runtime.market.selected.router == "V3":
        interval = max(interval, 0.103)
    if mode != "LIVE":
        context = (
            (mode,)
            if mode == "DEMO"
            else (mode, runtime.market.selected.token.lower(), runtime.market.selected.quote.lower())
        )
        if runtime.session.paper_context != context:
            if runtime.session.paper.position and runtime.session.paper_context is not None:
                raise ValueError("Сначала закройте позицию предыдущего PAPER-рынка")
            if not runtime.session.paper.position:
                runtime.session.paper = PaperTrader(settings.slippage)
                runtime.session.paper_usd = {"value": D(0), "closed": 0, "missing": 0, "entry": None}
            runtime.session.paper_context = context
    old_cooldown = runtime.session.strategy.cooldown_until if mode == runtime.session.mode else None
    runtime.session.mode, runtime.market.interval, runtime.session.live = mode, interval, live
    runtime.session.sizing, runtime.session.requested_amount = sizing, requested_amount
    runtime.session.paper_policy = paper_policy
    runtime.session.cost_policy, runtime.session.gas_gwei = cost_policy, D(data["gas"])
    old_entry = runtime.session.strategy.entry
    old_entry_time, old_peak = runtime.session.strategy.entry_time, runtime.session.strategy.peak_price
    runtime.session.strategy = Strategy(settings, policy, exit_policy)
    runtime.session.strategy.cooldown_until = old_cooldown
    if mode == "LIVE" and runtime.position():
        runtime.session.strategy.entry = D(runtime.position()["entry"])
        runtime.session.strategy.peak_price = D(
            runtime.position().get("peak_price", runtime.position()["entry"])
        )
        started = runtime.position().get("opened_at")
        if exit_policy.max_hold_seconds and started is None:
            raise ValueError(
                "В старой позиции нет времени входа; отключите выход по времени или выполните ручной SELL"
            )
        runtime.session.strategy.entry_time = (
            time.monotonic() - max(0, time.time() - started) if started is not None else None
        )
    elif mode != "LIVE" and runtime.session.paper.position:
        runtime.session.strategy.entry = old_entry
        runtime.session.strategy.entry_time, runtime.session.strategy.peak_price = old_entry_time, old_peak
    runtime.session.paper.slippage = settings.slippage
    runtime.session.entry_retry_at = 0.0
    runtime.session.entry_notice = ""
    runtime.session.halt_reason = ""
    runtime.market.quote_unavailable = False
    runtime.market.quote_failures = 0
    previous_closed = runtime.recording.recorder is None or runtime.recording.recorder.close()
    runtime.recording.recorder = None
    runtime.recording.recorder_notice = False
    if data.get("record_market", False) and previous_closed:
        try:
            runtime.recording.recorder = MarketTape(
                runtime.store.path.parent / "market-recordings",
                {
                    "mode": runtime.session.mode,
                    "pool": asdict(runtime.market.pool) if runtime.market.pool else None,
                    "settings": asdict(settings),
                    "signal_policy": policy.export(),
                    "sizing": sizing.export(),
                    "requested_amount": str(requested_amount),
                    "exit_policy": exit_policy.export(),
                    "entry_cost_policy": cost_policy.export(),
                    "paper_policy": paper_policy.export(),
                    "starts_with_position": runtime.session.strategy.entry is not None,
                },
            )
            runtime.log.emit(
                "Запись рынка включена: локальный архив market-recordings (части до 10 MiB, архив до 200 MiB)"
            )
        except OSError:
            runtime.log.emit("Запись рынка недоступна: проверьте свободное место и лимит архива")


def command(runtime: CommandRuntime, name: str, data: dict[str, Any]) -> None:
    if runtime.stop_event.is_set() and name in ("start", "buy", "convert", "sweep"):
        raise ValueError("STOP запрошен: новая торговая операция отменена")
    if runtime.session.running and name not in ("sell", "accounting_report"):
        raise ValueError("Сначала остановите BOT")
    if name in ("connect", "discover", "verify", "select", "wallet", "remove_profile") and (
        runtime.session.paper.position or runtime.position()
    ):
        raise ValueError("Сначала закройте текущую позицию")
    HANDLERS[CommandKind(name)](runtime, CommandKind(name), data)


def select_pool(runtime: CommandRuntime, pool: Pool, *, generation: int | None = None) -> None:
    if runtime.session.paper.position or runtime.position():
        raise ValueError("Сначала закройте позицию текущего пула")
    verified = runtime.connections.reader.verify_pool(pool.address, pool.token)
    if not runtime.discovery_current(generation):
        return
    if runtime.session.paper_context is not None and runtime.session.paper_context != (
        runtime.session.mode,
        verified.token.lower(),
        verified.quote.lower(),
    ):
        runtime.session.paper = PaperTrader(runtime.session.paper.slippage)
        runtime.session.paper_context = None
    runtime.session.trade_detail = None
    runtime.session.open_estimate = None
    runtime.market.pool = verified
    runtime.market.pool_generation = runtime.market.discovery_generation if generation is None else generation
    runtime.store.data.setdefault("known_pools", {})[runtime.market.selected.address.lower()] = asdict(
        runtime.market.pool
    )
    runtime.store.data["last_pool"] = asdict(runtime.market.pool)
    runtime.store.save()
    runtime.discovery_emit(generation, "selected", runtime.market.pool)
    runtime.log.emit("Выбран " + runtime.market.selected.label)
    runtime.read_price(force_chain=True)


def handle_connect(runtime: CommandRuntime, name: CommandKind, data: dict[str, Any]) -> None:
    chain = Chain(data["rpc"])
    block = chain.check()
    backup = None
    if data.get("backup_rpc", "").strip():
        backup = Chain(data["backup_rpc"].strip())
        backup.restrict_to_reads()
        backup.check()
    broadcaster = None
    if data.get("send_rpc", "").strip():
        broadcaster = Chain(data["send_rpc"].strip())
        broadcaster.check()
    feed = HeadFeed(data["ws_rpc"].strip()) if data.get("ws_rpc", "").strip() else None
    if runtime.connections.gap_recovery is not None:
        runtime.connections.gap_recovery.stop()
        runtime.connections.gap_recovery = None
    if runtime.connections.head_feed is not None:
        runtime.connections.head_feed.stop()
    runtime.connections.head_feed = feed.start() if feed is not None else None
    runtime.connections.head_schedule = HeadSchedule()
    runtime.connections.chain = chain
    runtime.connections.backup_chain = backup
    runtime.connections.broadcast_chain = broadcaster
    if broadcaster is not None:
        runtime.log.emit("Отправка через отдельный RPC; чтение receipts через основной")
    runtime.connections.backup_until = 0.0
    runtime.connections.rpc_health = RpcHealth()
    runtime.connections.adaptive_rpc = bool(data.get("adaptive_rpc", False))
    runtime.connections.last_market_header = None
    runtime.connections.backup_verified_pool = None
    runtime.market.pool = None
    runtime.emit_event(EventKind.POOLS, [])
    runtime.log.emit(f"BSC подключена, chainId 56, блок {block}")
    if data.get("save"):
        Vault().save("rpc", data["rpc"])
        Vault().save("backup_rpc", data.get("backup_rpc", "").strip())
        Vault().save("ws_rpc", data.get("ws_rpc", "").strip())
        Vault().save("send_rpc", data.get("send_rpc", "").strip())


def handle_wallet(runtime: CommandRuntime, name: CommandKind, data: dict[str, Any]) -> None:
    account = Account.from_key(data["key"])
    Vault().save("wallet", data["key"])
    runtime.store.data["wallet_address"] = account.address
    runtime.store.save()
    runtime.emit_event(EventKind.WALLET, account.address)
    runtime.log.emit("Кошелёк сохранён в macOS Keychain: " + account.address)


def handle_discover(runtime: CommandRuntime, name: CommandKind, data: dict[str, Any]) -> None:
    runtime.require_chain()
    generation = data.get("generation")
    if not runtime.discovery_current(generation):
        return
    runtime.market.pool = None
    runtime.market.pool_generation = None
    runtime.discovery_emit(generation, "pools", [])
    catalogs = {}
    routers = ("V2", "V3") if data["router"] == "AUTO" else (data["router"],)
    for router in routers:
        catalog = dynamic.catalog(runtime.store, router)
        catalogs[router] = (
            catalog
            if data["quote"] == "ALL"
            else {name: token for name, token in catalog.items() if name == data["quote"]}
        )
    result = runtime.connections.reader.resolve_address(data["token"], catalogs)
    if not runtime.discovery_current(generation):
        return
    runtime.discovery_emit(generation, "pools", [c.pool for c in result.candidates if c.ready])
    runtime.log.emit(f"AutoPair: {result.state}; найдено {len(result.candidates)} пулов")
    if result.state == "RESOLVED":
        if result.selected is None:
            raise ValueError("AutoPair не вернул выбранный маршрут")
        runtime.select_pool(result.selected.pool, generation=generation)
    runtime.discovery_emit(generation, "autopair", result.state)


def handle_compare_routes(runtime: CommandRuntime, name: CommandKind, data: dict[str, Any]) -> None:
    runtime.require_chain()
    generation = data.get("generation")
    if not runtime.discovery_current(generation):
        return
    from dipbot.market.route_comparison import compare

    reference = data["reference"]
    amount = SizingPolicy.parse(data["sizing"]).amount_quote(
        D(data["amount"]), reference.quote, runtime.rates
    )
    report = compare(
        runtime.connections.reader,
        data["pools"],
        reference,
        amount,
        D(data["maximum"]),
        CostPolicy.parse(data["cost_policy"]),
        D(data["gas"]),
        runtime.rates,
        cancelled=lambda: not runtime.discovery_current(generation),
    )
    runtime.discovery_emit(generation, "route_comparison", report)


def handle_verify(runtime: CommandRuntime, name: CommandKind, data: dict[str, Any]) -> None:
    runtime.require_chain()
    pool = runtime.connections.reader.verify_pool(data["pool"], data["token"])
    generation = data.get("generation")
    if not runtime.discovery_current(generation):
        return
    runtime.discovery_emit(generation, "pools", [pool])
    runtime.select_pool(pool, generation=generation)


def handle_select(runtime: CommandRuntime, name: CommandKind, data: dict[str, Any]) -> None:
    runtime.select_pool(data["pool"], generation=data.get("generation"))


def handle_add_profile(runtime: CommandRuntime, name: CommandKind, data: dict[str, Any]) -> None:
    runtime.require_chain()
    if data.get("generation") is not None and data["generation"] != runtime.market.pool_generation:
        raise ValueError("Ввод изменился: повторите AutoPair или CHECK POOL")
    if not runtime.market.pool:
        raise ValueError("Сначала CHECK + ADD или AutoPair")
    # Recheck target pool too: selection may predate a liquidity change.
    runtime.market.pool = runtime.connections.reader.verify_pool(
        runtime.market.selected.address, runtime.market.selected.token
    )
    # Quote round trip is checked without a private key or any transaction.
    helper = object.__new__(LiveTrader)
    helper.chain = runtime.connections.reader
    helper.store = runtime.store
    helper.trade_router = runtime.market.selected.router
    sample = 10**15  # Native LIVE_PAIR_SAMPLE_BNB_WEI: 0.001 BNB.
    if runtime.market.selected.quote != address(WBNB):
        helper.converter_preference = dynamic.preference(
            runtime.store, runtime.market.selected.quote, runtime.market.selected.router
        ) or seed_preference(runtime.market.selected.quote)
        try:
            buy_route = helper.conversion_route(WBNB, runtime.market.selected.quote, sample)
        except ValueError:
            # Route failure permits ETH fallback; RPC timeouts remain errors.
            if helper.converter_preference["converter_mode"] == "via_eth_v3":
                raise
            helper.converter_preference = {"converter_mode": "via_eth_v3", "converter_fee": 500}
            buy_route = helper.conversion_route(WBNB, runtime.market.selected.quote, sample)
        output = runtime.connections.reader.quote_route(buy_route, sample)
        minimum_out(output, D("3"))  # Native preview slippage is 3%, not a loss cap.
        returned = runtime.connections.reader.quote_route(buy_route, output, reverse=True)
        if returned * 10000 < sample * 8500:
            raise ValueError("Round-trip loss превышает 15%")
        if runtime.market.selected.quote not in profiles().values():
            symbol = (
                runtime.connections.reader.symbol(runtime.market.selected.quote)
                if hasattr(runtime.connections.chain, "symbol")
                else None
            )
            try:
                dynamic.upsert(
                    runtime.store,
                    runtime.market.pool,
                    buy_route,
                    max(0, (sample - returned) * 10000 // sample),
                    symbol=symbol,
                )
            finally:
                runtime.emit_event(EventKind.PROFILES, runtime.store.data.get("dynamic_profiles", {}))
            runtime.emit_event(EventKind.SELECTED, runtime.market.pool)
    runtime.log.emit(
        "ADDED: базовый актив проверен для конвертера; котировка не проверяет token tax / blacklist"
    )


def handle_remove_profile(runtime: CommandRuntime, name: CommandKind, data: dict[str, Any]) -> None:
    if runtime.store.data.get("operation"):
        raise UncertainTransaction("Сначала выполните сверку незавершённой операции")
    runtime.require_chain()
    symbol = data["symbol"]
    dynamic_profiles = runtime.store.data.get("dynamic_profiles", {})
    if symbol not in dynamic_profiles:
        raise ValueError("Встроенные профили удалять нельзя")
    owner = address(data["wallet"])
    token = dynamic_profiles[symbol]
    if runtime.connections.reader.balance(token, owner):
        raise ValueError("Сначала продайте остаток базового актива")
    for raw in runtime.store.data.get("known_pools", {}).values():
        if raw["quote"].lower() == token.lower() and runtime.connections.reader.balance(raw["token"], owner):
            raise ValueError("Сначала продайте остаток target этой базы")
    for key, position in runtime.store.data.get("positions", {}).items():
        if key.startswith(owner.lower() + ":") and position["pool"]["quote"].lower() == token.lower():
            raise ValueError("Сначала закройте сохранённую позицию этой базы")
    try:
        dynamic.remove(runtime.store, symbol)
    finally:
        if symbol not in runtime.store.data.get("dynamic_profiles", {}):
            runtime.market.pool = None
            runtime.market.pool_generation = None
            runtime.emit_event(EventKind.POOLS, [])
            runtime.emit_event(EventKind.PROFILES, runtime.store.data.get("dynamic_profiles", {}))
            runtime.emit_event(EventKind.PROFILE_REMOVED, symbol)


def handle_start(runtime: CommandRuntime, name: CommandKind, data: dict[str, Any]) -> None:
    runtime.configure(data)
    if runtime.session.mode == "LIVE":
        other_positions = [
            key
            for key in runtime.store.data.get("positions", {})
            if key.startswith(runtime.session.executor.owner.lower() + ":") and key != runtime.position_key()
        ]
        if other_positions:
            raise ValueError(
                "Есть сохранённая позиция другого пула: выберите её пул или используйте SELL WALLET → BNB"
            )
    if runtime.stop_event.is_set():
        raise ValueError("STOP запрошен во время подготовки")
    if name == CommandKind.START:
        runtime.session.running = True
        runtime.log.emit(
            f"START {runtime.session.mode}: DIP {runtime.session.strategy.settings.dip}% / TP {runtime.session.strategy.settings.take_profit}% / SL {runtime.session.strategy.settings.stop_loss}%"
        )
    else:
        runtime.read_price()
        if runtime.stop_event.is_set():
            raise ValueError("STOP запрошен во время чтения цены")
        runtime.open_position()


def handle_sell(runtime: CommandRuntime, name: CommandKind, data: dict[str, Any]) -> None:
    runtime.close_position("MANUAL")


def handle_accounting_report(runtime: CommandRuntime, name: CommandKind, data: dict[str, Any]) -> None:
    owner = (
        runtime.session.executor.owner
        if runtime.session.live
        else runtime.store.data.get("wallet_address", "")
    )
    runtime.emit_event(EventKind.ACCOUNTING_REPORT, accounting_report(runtime.store, owner))


def handle_balance(runtime: CommandRuntime, name: CommandKind, data: dict[str, Any]) -> None:
    runtime.require_chain()
    owner = address(data["wallet"])
    balances = {"BNB": str(D(runtime.connections.reader.w3.eth.get_balance(owner)) / 10**18)}
    for index, (symbol, token) in enumerate(
        (profiles() | runtime.store.data.get("dynamic_profiles", {})).items()
    ):
        if index % 10 == 0:
            runtime.log.emit(f"Чтение балансов: {index + 1}…")
        try:
            balances[symbol] = str(
                D(runtime.connections.reader.balance(token, owner))
                / D(10) ** runtime.connections.reader.decimals(token)
            )
        except Exception:
            balances[symbol] = "?"
    if runtime.market.pool:
        balances["TARGET"] = str(
            D(runtime.connections.reader.balance(runtime.market.selected.token, owner))
            / D(10) ** runtime.market.selected.token_decimals
        )
    runtime.emit_event(EventKind.BALANCES, balances)


def handle_convert(runtime: CommandRuntime, name: CommandKind, data: dict[str, Any]) -> None:
    runtime.configure(data)
    runtime.require_live()
    if runtime.stop_event.is_set():
        raise ValueError("STOP запрошен во время подготовки")
    amount = (
        raw_amount(D(data["amount"]), 18)
        if data["buy"]
        else runtime.connections.reader.balance(runtime.market.selected.quote, runtime.session.executor.owner)
    )
    if amount <= 0:
        raise ValueError("Нулевой баланс")
    if runtime.stop_event.is_set():
        raise ValueError("STOP запрошен во время чтения баланса")
    with signal_cycle(runtime, "CONVERT_BUY" if data["buy"] else "CONVERT_SELL", None):
        runtime.session.executor.begin("Converter BUY" if data["buy"] else "Converter SELL ALL")
        runtime.session.executor.convert(
            runtime.market.selected.quote, amount, data["buy"], runtime.session.strategy.settings.slippage
        )
        runtime.session.executor.finish()
    runtime.log.emit("Converter завершён")


def handle_sweep(runtime: CommandRuntime, name: CommandKind, data: dict[str, Any]) -> None:
    runtime.configure(data)
    runtime.require_live()
    runtime.sweep()


def handle_cancel_pending(runtime: CommandRuntime, name: CommandKind, data: dict[str, Any]) -> None:
    runtime.require_chain()
    if data.get("mode") != "LIVE":
        raise ValueError("Отмена транзакции доступна только в LIVE")
    key = Vault().get("wallet")
    if not key:
        raise ValueError("Нет кошелька в Keychain")
    helper = LiveTrader(runtime.connections.reader, key, runtime.store, D(data["gas"]), runtime.log.emit)
    helper.broadcast_chain = runtime.connections.broadcast_chain
    helper.rates = runtime.rates
    from dipbot.execution.cancellation import cancel_pending

    result = cancel_pending(
        helper, expected_hash=data["expected_hash"], expected_gas_price=data["expected_gas_price"]
    )
    runtime.log.emit(result)
    runtime.emit_event(EventKind.RECEIPT_REVIEW, result)


def handle_compare_positions(runtime: CommandRuntime, name: CommandKind, data: dict[str, Any]) -> None:
    runtime.require_chain()
    from dipbot.execution.recovery import compare_positions

    runtime.emit_event(
        EventKind.POSITION_COMPARISON, compare_positions(runtime.connections.reader, runtime.store)
    )


def handle_reconcile(runtime: CommandRuntime, name: CommandKind, data: dict[str, Any]) -> None:
    runtime.require_chain()
    if name == CommandKind.RECONCILE:
        operation = runtime.store.data.get("operation")
        result = reconcile_receipts(
            runtime.connections.reader, runtime.store, operation["wallet"] if operation else ""
        )
        runtime.log.emit(result)
        runtime.emit_event(EventKind.RECEIPT_REVIEW, result)
        return
    key = Vault().get("wallet")
    if not key:
        raise ValueError("Нет кошелька в Keychain")
    helper = LiveTrader(runtime.connections.reader, key, runtime.store, D(data["gas"]), runtime.log.emit)
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
        runtime.session.strategy.entry = None
        runtime.log.emit("Блокировка снята. Кэш позиций сброшен; остатки продавайте через SELL WALLET → BNB")


HANDLERS: dict[CommandKind, Callable[[CommandRuntime, CommandKind, dict[str, Any]], None]] = {
    CommandKind.CONNECT: handle_connect,
    CommandKind.WALLET: handle_wallet,
    CommandKind.DISCOVER: handle_discover,
    CommandKind.COMPARE_ROUTES: handle_compare_routes,
    CommandKind.VERIFY: handle_verify,
    CommandKind.SELECT: handle_select,
    CommandKind.ADD_PROFILE: handle_add_profile,
    CommandKind.REMOVE_PROFILE: handle_remove_profile,
    CommandKind.START: handle_start,
    CommandKind.BUY: handle_start,
    CommandKind.SELL: handle_sell,
    CommandKind.ACCOUNTING_REPORT: handle_accounting_report,
    CommandKind.BALANCE: handle_balance,
    CommandKind.CONVERT: handle_convert,
    CommandKind.SWEEP: handle_sweep,
    CommandKind.CANCEL_PENDING: handle_cancel_pending,
    CommandKind.COMPARE_POSITIONS: handle_compare_positions,
    CommandKind.RECONCILE: handle_reconcile,
    CommandKind.UNLOCK: handle_reconcile,
}
