from .telemetry import timed
from dataclasses import asdict, replace
import queue
import re
import threading
import time
from requests.exceptions import ConnectionError as RPCConnectionError, Timeout as RPCTimeout, HTTPError

from PySide6.QtCore import QThread, Signal
from eth_account import Account
from web3.exceptions import Web3RPCError

from .chain import Chain, Pool, WBNB, address, profiles
from .storage import Store, Vault, SaveAfterReplaceError
from . import dynamic, wallet_registry
from .routes import seed_preference
from .strategy import D, Settings, Strategy, raw_amount, snapshot_minimum, minimum_out
from .trader import LiveTrader, PaperTrader, UncertainTransaction, reconcile_receipts


from .entry_guard import EntryRejected
from .signal_policy import SignalPolicy
from .head_feed import HeadFeed, HeadSchedule
from .market_monitor import monitor_execution
from .market_tape import MarketTape
from .sizing import SizingPolicy
from .exit_policy import ExitPolicy
from .rpc_health import RpcHealth
from .chain import StaleBlock
from .accounting import RateBook, marked_value, operation_fees, record_close, closed_summary, accounting_report


def safe_error(exc):
    if type(exc) is SaveAfterReplaceError:
        return "Файл заменён, но надёжность сохранения не подтверждена; проверьте сохранённое состояние"
    # Provider exceptions can contain RPC credentials. Do not log arbitrary text.
    if type(exc) in (ValueError, RuntimeError, UncertainTransaction, EntryRejected):
        message = str(exc)
        if type(exc) is ValueError:
            message = re.sub(r"(?:0x)?[0-9a-fA-F]{64}", "[REDACTED]", message)
        if "http" not in message.lower() and len(message) < 250:
            return message
    return f"{type(exc).__name__}: операция прервана. Проверьте RPC, баланс и доступность пула"


class Worker(QThread):
    log = Signal(str)
    event = Signal(str, object)

    def __init__(self, store: Store):
        super().__init__()
        self.store = store
        self.commands = queue.Queue()
        self.quit_event = threading.Event()
        self.stop_event = threading.Event()
        self.chain = None
        self.backup_chain = None
        self.head_feed = None
        self.execution_monitor = None
        self.rates = RateBook()
        self.sizing = SizingPolicy()
        self.requested_amount = None
        self.paper_usd = {"value": D(0), "closed": 0, "missing": 0, "entry": None}
        self.recorder = None
        self.recorder_notice = False
        self.head_schedule = HeadSchedule()
        self.backup_until = 0.0
        self.rpc_health = RpcHealth()
        self.adaptive_rpc = False
        self.last_market_header = None
        self.backup_verified_pool = None
        self.market_source = 'BSC'
        self.pool = None
        self.mode = "DEMO"
        self.running = False
        self.strategy = Strategy(Settings())
        self.paper = PaperTrader(D("2"))
        self.paper_context = None
        self.live = None
        self.tick = 0
        self.current_price = None
        self.price_time = 0.0
        self.interval = 0.1
        self.discovery_generation = 0
        self.pool_generation = None
        self.quote_failures = 0
        self.quote_unavailable = False
        self.entry_retry_at = 0.0
        self.entry_notice = ""
        self.halt_reason = ""

    def discovery_current(self, generation):
        return (generation is None or generation == self.discovery_generation) and not (
            self.stop_event.is_set() or self.quit_event.is_set())

    def discovery_emit(self, generation, name, value):
        if generation is None:
            self.event.emit(name, value)
        else:
            self.event.emit("discovery_event", (generation, name, value))

    def submit(self, name, **data):
        self.commands.put((name, data))

    def run(self):
        try:
            self.run_loop()
        finally:
            if self.head_feed is not None:
                self.head_feed.stop()
            if self.recorder is not None:
                self.recorder.close()

    def run_loop(self):
        next_tick = time.monotonic()
        while not self.quit_event.is_set():
            try:
                timeout = (min(0.05, max(0, next_tick - time.monotonic()))
                           if self.running and self.head_feed is None else 0.05)
                name, data = self.commands.get(timeout=timeout)
            except queue.Empty:
                name = None
            if name:
                self.event.emit("busy", True)
                try:
                    self.command(name, data)
                except Exception as exc:
                    if name in ("discover", "verify", "select") and not self.discovery_current(data.get("generation")):
                        continue
                    if name in ("reconcile", "compare_positions"):
                        self.event.emit("position_comparison_error" if name == "compare_positions" else "receipt_review", safe_error(exc))
                    self.running = False
                    self.halt_reason = safe_error(exc)
                    self.log.emit("ОШИБКА: " + safe_error(exc))
                    if name in ("discover", "verify", "select"):
                        self.pool = None
                        self.pool_generation = None
                        self.discovery_emit(data.get("generation"), "pools", [])
                        self.discovery_emit(data.get("generation"), "error", safe_error(exc))
                    else:
                        self.event.emit("error", safe_error(exc))
                finally:
                    self.event.emit("busy", False)
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
                    self.event.emit("error", safe_error(exc))
                    self.log.emit("STOP: " + safe_error(exc))
                self.status()
            head = self.head_feed.snapshot() if self.head_feed is not None and self.mode != 'DEMO' and not self.quote_unavailable else None
            due = (self.head_schedule.due(head, time.monotonic(), next_tick)
                   if self.head_feed is not None and self.mode != 'DEMO' else time.monotonic() >= next_tick)
            if self.running and due:
                poll_started = time.monotonic()
                if self.head_schedule.consume(head, poll_started) and self.strategy.entry is None:
                    self.strategy.reset_anchor()
                    self.log.emit('Пропуск или смена ветви WebSocket: база DIP сброшена; читается актуальное состояние HTTP')
                try:
                    self.observe()
                except Exception as exc:
                    # Unhandled execution failures must still halt, including uncertain LIVE results.
                    self.running = False
                    self.halt_reason = safe_error(exc)
                    self.log.emit("BOT приостановлен: " + safe_error(exc))
                    self.event.emit("error", safe_error(exc))
                # One observation at a time; slow RPC skips missed slots instead
                # of queuing catch-up requests or adding another full delay.
                delay = min(5, 0.5 * 2**min(self.quote_failures, 4)) if self.quote_unavailable else self.interval
                next_tick = max(poll_started + delay, time.monotonic())
                self.status()

    def record_market(self, kind, **data):
        if self.recorder is not None:
            self.recorder.record(kind, **data)
            if not self.recorder_notice and (self.recorder.dropped or self.recorder.error_type):
                self.recorder_notice = True
                self.log.emit('Архив рынка неполный: ошибка записи или достигнут лимит; торговый журнал не затронут')

    def status(self):
        settings = self.strategy.settings
        realized = str(self.paper.realized) if self.mode != 'LIVE' else '—'
        if self.mode == 'LIVE' and self.live and self.pool:
            key = self.live.owner.lower() + ':' + self.pool.quote.lower()
            realized = self.store.data.get('realized_quote', {}).get(key, '—')
        base, entry = self.strategy.base or D(0), self.strategy.entry or D(0)
        levels = ({'ENTRY': str(entry), 'TP': str(entry*(1+settings.take_profit/100)),
                   'SL': str(entry*(1-settings.stop_loss/100))} if entry else
                  {'DIP': str(base*(1-settings.dip/100))})
        if entry and self.strategy.exit_policy.tp_sl_basis == 'quote':
            levels.pop('TP', None)
            levels.pop('SL', None)
        if entry and self.strategy.exit_policy.trailing_pct and self.strategy.peak_price:
            levels['TRAIL'] = str(self.strategy.peak_price*(1-self.strategy.exit_policy.trailing_pct/100))
        self.event.emit("status", {"running": self.running, "mode": self.mode,
                         "levels": levels,
                         "position": str(self.paper.position) if self.mode != "LIVE" else str(D(self.position().get("amount", 0)) / D(10)**(self.pool.token_decimals if self.pool else 18)),
                         "base": str(self.strategy.base or 0),
                         "rpc_health": self.rpc_health.report() if self.adaptive_rpc else [],
                         "exit_basis": self.strategy.exit_policy.tp_sl_basis,
                         "exit_return": getattr(self, "exit_return", None),
                         "signal_mode": self.strategy.policy.mode,
                         "base_reason": self.strategy.base_reason,
                         "base_age": max(0, time.monotonic() - self.strategy.base_time) if self.strategy.base_time is not None else None,
                         "entry": str(self.strategy.entry or 0),
                         "realized": realized,
                         "historical_usd": (closed_summary(self.store, self.live.owner) if self.mode == 'LIVE' and self.live else
                             {'value':str(self.paper_usd['value']) if self.paper_usd['closed'] and not self.paper_usd['missing'] else None,
                              'closed':self.paper_usd['closed'], 'missing':self.paper_usd['missing'], 'includes_gas':False}),
                         "pnl_quote": (self.paper_context[2] if self.mode == "PAPER" and self.paper_context and len(self.paper_context) == 3
                                       else self.pool.quote if self.mode != "DEMO" and self.pool else ""),
                         "quote_unavailable": self.quote_unavailable,
                         "entry_notice": self.entry_notice or (
                             f'Пауза после выхода: {max(0,self.strategy.cooldown_until-time.monotonic()):.1f} с'
                             if self.strategy.cooldown_until and time.monotonic() < self.strategy.cooldown_until else ''),
                         "halt_reason": self.halt_reason,
                         "locked": bool(self.store.data.get("operation"))})

    def position_key(self):
        return f"{self.live.owner.lower()}:{self.pool.address.lower()}" if self.live and self.pool else ""

    def position(self):
        return self.store.data.get("positions", {}).get(self.position_key(), {})

    def set_position(self, amount, entry):
        positions = self.store.data.setdefault("positions", {})
        if amount:
            positions[self.position_key()] = {**positions.get(self.position_key(), {}),
                "amount": amount, "entry": str(entry), "pool": asdict(self.pool),
                "opened_at": positions.get(self.position_key(), {}).get("opened_at", time.time()),
                "peak_price": positions.get(self.position_key(), {}).get("peak_price", str(entry))}
        else:
            positions.pop(self.position_key(), None)
        self.store.save()

    def require_chain(self):
        if self.chain is None:
            raise ValueError("Сначала подключите HTTP RPC")

    def require_live(self):
        if self.mode != "LIVE" or self.live is None:
            raise ValueError("Нужен LIVE-режим и кошелёк в Keychain")

    def configure(self, data):
        mode = data["mode"]
        policy = SignalPolicy.parse(data.get("signal_policy", {}))
        sizing = SizingPolicy.parse(data.get("sizing", {}))
        exit_policy = ExitPolicy.parse(data.get("exit_policy", {}))
        settings = Settings(**{k: D(v) for k, v in data["settings"].items()})
        requested_amount = settings.amount
        if sizing.unit == 'usd':
            if mode == 'DEMO' or self.pool is None:
                raise ValueError('AMOUNT в USD требует PAPER/LIVE и выбранный пул')
            settings = replace(settings, amount=sizing.amount_quote(requested_amount, self.pool.quote, self.rates))
        interval = float(data["interval"])
        if not 0.1 <= interval <= 0.5:
            raise ValueError("Интервал от 0.1 до 0.5 секунд (защита разрыва: 0.55 с)")
        if mode != "DEMO":
            self.require_chain()
            if not self.pool:
                raise ValueError("Выберите проверенный пул через AutoPair / CHECK + ADD")
            if address(data["token"]) != self.pool.token or address(data["pool"]) != self.pool.address:
                raise ValueError("Адреса изменились: заново выберите и проверьте пул")
            if data.get("router", "AUTO") not in ("AUTO", self.pool.router):
                raise ValueError("Router изменился: повторите AutoPair / CHECK POOL")
            if data.get("generation") is not None and data["generation"] != self.pool_generation:
                raise ValueError("Ввод изменился: заново проверьте пул")
            self.chain.max_block_age = policy.max_block_age
            if self.backup_chain is not None:
                self.backup_chain.max_block_age = policy.max_block_age
            self.chain.verify_pool(self.pool.address, self.pool.token)
        if mode != self.mode and (self.paper.position or self.position()):
            raise ValueError("Закройте текущую позицию перед сменой режима")
        live = None
        if mode == "LIVE":
            key = Vault().get("wallet")
            if not key:
                raise ValueError("Сначала сохраните отдельный кошелёк в Keychain")
            live = LiveTrader(self.chain, key, self.store, D(data["gas"]), self.log.emit)
            live.trade_router = self.pool.router
            live.rates = self.rates
            live.reserve_wei = raw_amount(sizing.reserve_bnb, 18) if sizing.reserve_bnb else 0
            if self.store.data.get("operation"):
                raise UncertainTransaction("Есть незавершённая операция: используйте сверку в настройках")
            pair_name = next((n for n,t in dynamic.catalog(self.store,self.pool.router).items()
                              if address(t) == self.pool.quote), self.pool.quote)
            wallet_registry.register(self.store, live.owner, self.pool, pair_name)
        if self.pool and self.pool.router == "V3":
            interval = max(interval, 0.103)
        if mode != "LIVE":
            context = (mode,) if mode == "DEMO" else (mode, self.pool.token.lower(), self.pool.quote.lower())
            if self.paper_context != context:
                if self.paper.position and self.paper_context is not None:
                    raise ValueError("Сначала закройте позицию предыдущего PAPER-рынка")
                if not self.paper.position:
                    self.paper = PaperTrader(settings.slippage)
                    self.paper_usd = {"value": D(0), "closed": 0, "missing": 0, "entry": None}
                self.paper_context = context
        old_cooldown = self.strategy.cooldown_until if mode == self.mode else None
        self.mode, self.interval, self.live = mode, interval, live
        self.sizing, self.requested_amount = sizing, requested_amount
        old_entry = self.strategy.entry
        old_entry_time, old_peak = self.strategy.entry_time, self.strategy.peak_price
        self.strategy = Strategy(settings, policy, exit_policy)
        self.strategy.cooldown_until = old_cooldown
        if mode == "LIVE" and self.position():
            self.strategy.entry = D(self.position()["entry"])
            self.strategy.peak_price = D(self.position().get('peak_price', self.position()['entry']))
            started = self.position().get('opened_at')
            if exit_policy.max_hold_seconds and started is None:
                raise ValueError('В старой позиции нет времени входа; отключите выход по времени или выполните ручной SELL')
            self.strategy.entry_time = time.monotonic()-max(0,time.time()-started) if started is not None else None
        elif mode != "LIVE" and self.paper.position:
            self.strategy.entry = old_entry
            self.strategy.entry_time, self.strategy.peak_price = old_entry_time, old_peak
        self.paper.slippage = settings.slippage
        self.entry_retry_at = 0.0
        self.entry_notice = ""
        self.halt_reason = ""
        self.quote_unavailable = False
        self.quote_failures = 0
        previous_closed = self.recorder is None or self.recorder.close()
        self.recorder = None
        self.recorder_notice = False
        if data.get('record_market', False) and previous_closed:
            try:
                self.recorder = MarketTape(self.store.path.parent / 'market-recordings', {
                    'mode': self.mode, 'pool': asdict(self.pool) if self.pool else None,
                    'settings': asdict(settings), 'signal_policy': policy.export(), 'sizing':sizing.export(),
                    'requested_amount':str(requested_amount), 'exit_policy':exit_policy.export(),
                    'starts_with_position': self.strategy.entry is not None})
                self.log.emit('Запись рынка включена: локальный архив market-recordings (до 10 MiB на запуск)')
            except OSError:
                self.log.emit('Запись рынка недоступна: проверьте свободное место и лимит архива')

    def command(self, name, data):
        if self.stop_event.is_set() and name in ("start", "buy", "convert", "sweep"):
            raise ValueError("STOP запрошен: новая торговая операция отменена")
        if self.running and name not in ("sell", "accounting_report"):
            raise ValueError("Сначала остановите BOT")
        if name in ("connect", "discover", "verify", "select", "wallet", "remove_profile") and (self.paper.position or self.position()):
            raise ValueError("Сначала закройте текущую позицию")
        if name == "connect":
            chain = Chain(data["rpc"])
            block = chain.check()
            backup = None
            if data.get('backup_rpc', '').strip():
                backup = Chain(data['backup_rpc'].strip())
                backup.restrict_to_reads()
                backup.check()
            feed = HeadFeed(data['ws_rpc'].strip()) if data.get('ws_rpc', '').strip() else None
            if self.head_feed is not None:
                self.head_feed.stop()
            self.head_feed = feed.start() if feed is not None else None
            self.head_schedule = HeadSchedule()
            self.chain = chain
            self.backup_chain = backup
            self.backup_until = 0.0
            self.rpc_health = RpcHealth()
            self.adaptive_rpc = bool(data.get('adaptive_rpc', False))
            self.last_market_header = None
            self.backup_verified_pool = None
            self.pool = None
            self.event.emit("pools", [])
            self.log.emit(f"BSC подключена, chainId 56, блок {block}")
            if data.get("save"):
                Vault().save("rpc", data["rpc"])
                Vault().save('backup_rpc', data.get('backup_rpc', '').strip())
                Vault().save('ws_rpc', data.get('ws_rpc', '').strip())
        elif name == "wallet":
            account = Account.from_key(data["key"])
            Vault().save("wallet", data["key"])
            self.store.data["wallet_address"] = account.address
            self.store.save()
            self.event.emit("wallet", account.address)
            self.log.emit("Кошелёк сохранён в macOS Keychain: " + account.address)
        elif name == "discover":
            self.require_chain()
            generation = data.get("generation")
            if not self.discovery_current(generation):
                return
            self.pool = None
            self.pool_generation = None
            self.discovery_emit(generation, "pools", [])
            catalogs = {}
            routers = ("V2", "V3") if data["router"] == "AUTO" else (data["router"],)
            for router in routers:
                catalog = dynamic.catalog(self.store, router)
                catalogs[router] = catalog if data["quote"] == "ALL" else {
                    name: token for name, token in catalog.items() if name == data["quote"]}
            result = self.chain.resolve_address(data["token"], catalogs)
            if not self.discovery_current(generation):
                return
            self.discovery_emit(generation, "pools", [c.pool for c in result.candidates if c.ready])
            self.log.emit(f"AutoPair: {result.state}; найдено {len(result.candidates)} пулов")
            if result.state == "RESOLVED":
                self.select_pool(result.selected.pool, generation=generation)
            self.discovery_emit(generation, "autopair", result.state)
        elif name == "verify":
            self.require_chain()
            pool = self.chain.verify_pool(data["pool"], data["token"])
            generation = data.get("generation")
            if not self.discovery_current(generation):
                return
            self.discovery_emit(generation, "pools", [pool])
            self.select_pool(pool, generation=generation)
        elif name == "select":
            self.select_pool(data["pool"], generation=data.get("generation"))
        elif name == "add_profile":
            self.require_chain()
            if data.get("generation") is not None and data["generation"] != self.pool_generation:
                raise ValueError("Ввод изменился: повторите AutoPair или CHECK POOL")
            if not self.pool:
                raise ValueError("Сначала CHECK + ADD или AutoPair")
            # Recheck target pool too: selection may predate a liquidity change.
            self.pool = self.chain.verify_pool(self.pool.address, self.pool.token)
            # Quote round trip is checked without a private key or any transaction.
            helper = object.__new__(LiveTrader)
            helper.chain = self.chain
            helper.store = self.store
            helper.trade_router = self.pool.router
            sample = 10**15  # Native LIVE_PAIR_SAMPLE_BNB_WEI: 0.001 BNB.
            if self.pool.quote != address(WBNB):
                helper.converter_preference = dynamic.preference(self.store, self.pool.quote, self.pool.router) or seed_preference(self.pool.quote)
                try:
                    buy_route = helper.conversion_route(WBNB, self.pool.quote, sample)
                except ValueError:
                    # Route failure permits ETH fallback; RPC timeouts remain errors.
                    if helper.converter_preference["converter_mode"] == "via_eth_v3":
                        raise
                    helper.converter_preference = {"converter_mode": "via_eth_v3", "converter_fee": 500}
                    buy_route = helper.conversion_route(WBNB, self.pool.quote, sample)
                output = self.chain.quote_route(buy_route, sample)
                minimum_out(output, D("3"))  # Native preview slippage is 3%, not a loss cap.
                returned = self.chain.quote_route(buy_route, output, reverse=True)
                if returned * 10000 < sample * 8500:
                    raise ValueError("Round-trip loss превышает 15%")
                if self.pool.quote not in profiles().values():
                    symbol = self.chain.symbol(self.pool.quote) if hasattr(self.chain, "symbol") else None
                    try:
                        dynamic.upsert(self.store, self.pool, buy_route, max(0, (sample-returned)*10000//sample), symbol=symbol)
                    finally:
                        self.event.emit("profiles", self.store.data.get("dynamic_profiles", {}))
                    self.event.emit("selected", self.pool)
            self.log.emit("ADDED: базовый актив проверен для конвертера; котировка не проверяет token tax / blacklist")
        elif name == "remove_profile":
            if self.store.data.get("operation"):
                raise UncertainTransaction("Сначала выполните сверку незавершённой операции")
            self.require_chain()
            symbol = data["symbol"]
            dynamic_profiles = self.store.data.get("dynamic_profiles", {})
            if symbol not in dynamic_profiles:
                raise ValueError("Встроенные профили удалять нельзя")
            owner = address(data["wallet"])
            token = dynamic_profiles[symbol]
            if self.chain.balance(token, owner):
                raise ValueError("Сначала продайте остаток базового актива")
            for raw in self.store.data.get("known_pools", {}).values():
                if raw["quote"].lower() == token.lower() and self.chain.balance(raw["token"], owner):
                    raise ValueError("Сначала продайте остаток target этой базы")
            for key, position in self.store.data.get("positions", {}).items():
                if key.startswith(owner.lower() + ":") and position["pool"]["quote"].lower() == token.lower():
                    raise ValueError("Сначала закройте сохранённую позицию этой базы")
            try:
                dynamic.remove(self.store, symbol)
            finally:
                if symbol not in self.store.data.get("dynamic_profiles", {}):
                    self.pool = None
                    self.pool_generation = None
                    self.event.emit("pools", [])
                    self.event.emit("profiles", self.store.data.get("dynamic_profiles", {}))
                    self.event.emit("profile_removed", symbol)
        elif name in ("start", "buy"):
            self.configure(data)
            if self.mode == "LIVE":
                other_positions = [key for key in self.store.data.get("positions", {})
                                   if key.startswith(self.live.owner.lower() + ":")
                                   and key != self.position_key()]
                if other_positions:
                    raise ValueError("Есть сохранённая позиция другого пула: выберите её пул или используйте SELL WALLET → BNB")
            if self.stop_event.is_set():
                raise ValueError("STOP запрошен во время подготовки")
            if name == "start":
                self.running = True
                self.log.emit(f"START {self.mode}: DIP {self.strategy.settings.dip}% / TP {self.strategy.settings.take_profit}% / SL {self.strategy.settings.stop_loss}%")
            else:
                self.read_price()
                if self.stop_event.is_set():
                    raise ValueError("STOP запрошен во время чтения цены")
                self.open_position()
        elif name == "sell":
            self.close_position("MANUAL")
        elif name == 'accounting_report':
            owner = self.live.owner if self.live else self.store.data.get('wallet_address', '')
            self.event.emit('accounting_report', accounting_report(self.store, owner))
        elif name == "balance":
            self.require_chain()
            owner = address(data["wallet"])
            balances = {"BNB": str(D(self.chain.w3.eth.get_balance(owner)) / 10**18)}
            for index, (symbol, token) in enumerate((profiles() | self.store.data.get("dynamic_profiles", {})).items()):
                if index % 10 == 0:
                    self.log.emit(f"Чтение балансов: {index+1}…")
                try:
                    balances[symbol] = str(D(self.chain.balance(token, owner)) / D(10)**self.chain.decimals(token))
                except Exception:
                    balances[symbol] = "?"
            if self.pool:
                balances["TARGET"] = str(D(self.chain.balance(self.pool.token, owner)) / D(10)**self.pool.token_decimals)
            self.event.emit("balances", balances)
        elif name == "convert":
            self.configure(data)
            self.require_live()
            if self.stop_event.is_set():
                raise ValueError("STOP запрошен во время подготовки")
            amount = raw_amount(D(data["amount"]), 18) if data["buy"] else self.chain.balance(self.pool.quote, self.live.owner)
            if amount <= 0:
                raise ValueError("Нулевой баланс")
            if self.stop_event.is_set():
                raise ValueError("STOP запрошен во время чтения баланса")
            self.live.begin("Converter BUY" if data["buy"] else "Converter SELL ALL")
            self.live.convert(self.pool.quote, amount, data["buy"], self.strategy.settings.slippage)
            self.live.finish()
            self.log.emit("Converter завершён")
        elif name == "sweep":
            self.configure(data)
            self.require_live()
            self.sweep()
        elif name == "compare_positions":
            self.require_chain()
            from .recovery import compare_positions
            self.event.emit("position_comparison", compare_positions(self.chain, self.store))
        elif name in ("reconcile", "unlock"):
            self.require_chain()
            if name == "reconcile":
                operation = self.store.data.get("operation")
                result = reconcile_receipts(self.chain, self.store, operation["wallet"] if operation else "")
                self.log.emit(result)
                self.event.emit("receipt_review", result)
                return
            key = Vault().get("wallet")
            if not key:
                raise ValueError("Нет кошелька в Keychain")
            helper = LiveTrader(self.chain, key, self.store, D(data["gas"]), self.log.emit)
            self.log.emit(helper.reconcile())
            if name == "unlock" and self.store.data.get("operation"):
                operation = self.store.data["operation"]
                if not operation["transactions"]:
                    self.log.emit("Операция не дошла до записи транзакции")
                # Remove cached positions: user must explicitly handle actual wallet balances.
                for key in list(self.store.data.get("positions", {})):
                    if key.startswith(helper.owner.lower() + ":"):
                        del self.store.data["positions"][key]
                helper.operation = operation
                helper.finish()
                self.strategy.entry = None
                self.log.emit("Блокировка снята. Кэш позиций сброшен; остатки продавайте через SELL WALLET → BNB")
        else:
            raise ValueError("Неизвестная команда")

    def select_pool(self, pool, *, generation=None):
        if self.paper.position or self.position():
            raise ValueError("Сначала закройте позицию текущего пула")
        verified = self.chain.verify_pool(pool.address, pool.token)
        if not self.discovery_current(generation):
            return
        if self.paper_context is not None and self.paper_context != (
                self.mode, verified.token.lower(), verified.quote.lower()):
            self.paper = PaperTrader(self.paper.slippage)
            self.paper_context = None
        self.pool = verified
        self.pool_generation = self.discovery_generation if generation is None else generation
        self.store.data.setdefault("known_pools", {})[self.pool.address.lower()] = asdict(self.pool)
        self.store.data["last_pool"] = asdict(self.pool)
        self.store.save()
        self.discovery_emit(generation, "selected", self.pool)
        self.log.emit("Выбран " + self.pool.label)
        self.read_price(force_chain=True)

    @timed("worker.read_price")
    def read_price(self, force_chain=False):
        if self.mode == "DEMO" and not force_chain:
            # Deterministic local market; no network, funds or signing.
            self.tick += 1
            # Include a sudden dip: smooth declines reanchor every two moves.
            cycle = ("1", "1.01", "1.02", "0.97", "0.98", "1.00", "1.01", "1")
            price = D(cycle[(self.tick - 1) % len(cycle)])
        else:
            self.require_chain()
            if not self.pool:
                raise ValueError("Пул не выбран")
            price = self.market_price()
        self.current_price = price
        self.price_time = time.monotonic()
        demo = self.mode == 'DEMO' and not force_chain
        source_chain = self.backup_chain if self.market_source != 'BSC' else self.chain
        header = getattr(source_chain, 'price_block', None) if not demo else None
        self.event.emit('price_context', {'source': 'DEMO' if demo else getattr(self.chain, 'price_source', 'BSC'),
                                        'rpc_source': self.market_source,
                                        'block': header['number'] if header else None,
                                        'block_timestamp': header.get('timestamp') if header else None,
                                        'quote': '' if demo else self.pool.quote})
        self.record_market('price', price=str(price), block=header['number'] if header else None,
                           block_hash=bytes(header['hash']).hex() if header else None,
                           block_timestamp=header.get('timestamp') if header else None,
                           source='DEMO' if demo else 'BSC')
        self.event.emit("price", str(price))
        return price

    def adaptive_market_price(self):
        source_id = self.rpc_health.choose(time.monotonic())
        for attempt in range(2):
            source = self.backup_chain if source_id else self.chain
            started = time.monotonic()
            try:
                price = self.backup_price() if source_id else self.chain.price(self.pool)
                header = getattr(source, 'price_block', None)
                previous = self.last_market_header
                if header and previous and (header['number'] < previous['number'] or (
                        header['number'] == previous['number'] and header['hash'] != previous['hash'])):
                    raise TimeoutError('RPC вернул более старый блок или другую ветвь')
            except (RPCConnectionError, RPCTimeout, TimeoutError, HTTPError) as exc:
                if isinstance(exc, HTTPError) and getattr(exc.response, 'status_code', 0) not in (429, 500, 502, 503, 504):
                    raise
                self.rpc_health.failure(source_id, time.monotonic())
                other = 1-source_id
                if attempt or time.monotonic() < self.rpc_health.blocked_until[other]:
                    raise
                source_id = other
                continue
            self.rpc_health.success(source_id, time.monotonic()-started, time.monotonic())
            self.last_market_header = dict(header) if header else previous
            self.market_source = 'BSC · резервный RPC' if source_id else 'BSC'
            return price

    def market_price(self):
        if self.adaptive_rpc and self.backup_chain is not None:
            return self.adaptive_market_price()
        # Execution always keeps self.chain / LiveTrader.chain on the primary.
        if self.backup_chain is not None and time.monotonic() < self.backup_until:
            return self.backup_price()
        try:
            price = self.chain.price(self.pool)
        except (RPCConnectionError, RPCTimeout, TimeoutError, HTTPError) as exc:
            if isinstance(exc, HTTPError) and getattr(exc.response, 'status_code', 0) not in (429, 500, 502, 503, 504):
                raise
            if self.backup_chain is None:
                raise
            self.backup_until = time.monotonic() + 30
            self.log.emit('Основной RPC недоступен: котировки через резервный. Отправка сделок остаётся на основном RPC')
            return self.backup_price()
        if self.market_source != 'BSC':
            self.log.emit('Котировки снова поступают с основного RPC')
        self.market_source = 'BSC'
        return price

    def backup_price(self):
        if self.backup_verified_pool != self.pool:
            self.backup_chain.check()
            verified = self.backup_chain.verify_pool(self.pool.address, self.pool.token)
            if verified != self.pool:
                raise ValueError('Резервный RPC вернул другой пул/маршрут; торговля приостановлена')
            self.backup_verified_pool = verified
        price = self.backup_chain.price(self.pool)
        primary = getattr(self.chain, 'price_block', None)
        backup = getattr(self.backup_chain, 'price_block', None)
        if primary and backup and (backup['number'] < primary['number'] or
                (backup['number'] == primary['number'] and backup['hash'] != primary['hash'])):
            raise StaleBlock('Резервный RPC отстаёт или вернул другую ветвь цепочки')
        self.market_source = 'BSC · резервный RPC'
        return price

    @timed("worker.observe")
    def observe(self):
        # Retry only a failed read, never an execution or post-receipt failure.
        if self.store.data.get("operation"):
            raise UncertainTransaction("Незавершённая операция: автоматические сделки заблокированы")
        try:
            price = self.read_price()
            exit_return = None
            if self.strategy.entry is not None and self.strategy.exit_policy.tp_sl_basis == 'quote':
                if self.mode == 'LIVE':
                    position = self.position()
                    amount, cost = position['amount'], D(position.get('cost_quote', 0))
                else:
                    cost = self.paper.cost
                    amount = raw_amount(self.paper.position, self.pool.token_decimals) if self.pool else 0
                if cost <= 0:
                    raise ValueError('Неизвестна себестоимость позиции: TP/SL по выходу недоступен')
                if self.mode == 'DEMO':
                    proceeds = self.paper.position*price
                else:
                    source = self.backup_chain if self.market_source != 'BSC' else self.chain
                    proceeds = D(source.exit_quote(self.pool, amount))/D(10)**self.pool.quote_decimals
                if time.monotonic()-self.price_time > self.strategy.settings.max_gap:
                    raise TimeoutError("Снимок цены устарел во время котировки выхода")
                exit_return = (proceeds/cost-1)*100
            self.exit_return = str(exit_return) if exit_return is not None else None
        except (RPCConnectionError, RPCTimeout, TimeoutError, HTTPError) as exc:
            if isinstance(exc, HTTPError) and getattr(exc.response, 'status_code', 0) not in (429, 500, 502, 503, 504):
                raise
            self.record_market('read_error', type=type(exc).__name__)
            self.quote_failures += 1
            if not self.quote_unavailable:
                self.log.emit("Котировки недоступны: входы запрещены, повтор чтения с паузой до 5 с. Открытая позиция сохраняется")
            self.quote_unavailable = True
            self.exit_return = None
            return
        if self.quote_unavailable:
            self.log.emit("Чтение котировок восстановлено; проверка позиции возобновлена")
        self.quote_unavailable = False
        self.quote_failures = 0
        now = time.monotonic()
        if self.entry_notice and self.strategy.entry is None:
            if now < self.entry_retry_at:
                return
            # Require a new signal from a fresh baseline, not the rejected signal.
            self.strategy.reset_anchor()
            self.entry_notice = ""
        if (self.strategy.entry is None and self.strategy.last_time is not None
                and now - self.strategy.last_time > self.strategy.settings.max_gap):
            self.log.emit("Разрыв котировок > 0.55 с: база DIP сброшена")
        source = self.backup_chain if self.market_source != 'BSC' else self.chain
        header = getattr(source, 'price_block', None) if self.mode != 'DEMO' else None
        observation_id = (header['number'], bytes(header['hash']), price) if header else None
        usd_mark = self.rates.snapshot(self.pool.quote) if self.pool and self.mode != 'DEMO' else None
        self.record_market('observation', price=str(price), block=header['number'] if header else None,
                           block_hash=bytes(header['hash']).hex() if header else None,
                           quote_usd=usd_mark['usd'] if usd_mark else None,
                           quote_usd_observed_at=usd_mark['observed_at'] if usd_mark else None)
        action = self.strategy.observe(price, now, observation_id=observation_id, exit_return=exit_return)
        if self.mode == 'LIVE' and self.strategy.entry is not None and self.strategy.peak_price is not None:
            position = self.position()
            if position and self.strategy.peak_price > D(position.get('peak_price', position['entry'])):
                position['peak_price'] = str(self.strategy.peak_price)
                self.store.save()
        if self.stop_event.is_set():
            return
        if action:
            self.record_market('signal', action=action, price=str(price), base=str(self.strategy.base),
                               entry=str(self.strategy.entry))
        if action == "BUY":
            try:
                self.open_position()
            except EntryRejected as exc:
                if self.mode not in ("PAPER", "LIVE") or self.store.data.get("operation") or self.paper.position or self.position():
                    raise
                self.entry_retry_at = time.monotonic() + 5.0
                self.entry_notice = str(exc) + "; пауза 5 с, затем новый сигнал DIP"
                self.log.emit("Вход пропущен: " + self.entry_notice)
        elif action:
            self.close_position(action)
            if self.strategy.stopped:
                self.running = False

    @monitor_execution
    @timed("worker.open_position")
    def open_position(self):
        if self.strategy.entry is not None:
            raise ValueError("Позиция уже открыта")
        settings = self.strategy.settings
        if self.sizing.unit == 'usd':
            settings = replace(settings, amount=self.sizing.amount_quote(self.requested_amount, self.pool.quote, self.rates))
        if self.mode in ('LIVE', 'PAPER') and settings.min_swaps:
            from .activity import swap_count
            try:
                activity = swap_count(self.chain, self.pool)
            except (Web3RPCError, RPCConnectionError, RPCTimeout, TimeoutError, HTTPError) as exc:
                if self.backup_chain is None:
                    raise EntryRejected('Не удалось прочитать активность пула; вход запрещён') from exc
                if self.backup_chain.verify_pool(self.pool.address, self.pool.token) != self.pool:
                    raise ValueError('Резервный RPC вернул другой пул')
                try:
                    activity = swap_count(self.backup_chain, self.pool)
                except (Web3RPCError, RPCConnectionError, RPCTimeout, TimeoutError, HTTPError) as second:
                    raise EntryRejected('Активность недоступна на обоих RPC; вход запрещён') from second
                self.log.emit('Активность проверена через резервный RPC')
            self.log.emit(f"Активность пула: {activity['count']} Swap за блоки {activity['from_block']}–{activity['to_block']}")
            if activity['count'] < settings.min_swaps:
                raise EntryRejected('Вход пропущен: недостаточно Swap в выбранном пуле')
        if self.mode in ('LIVE', 'PAPER') and callable(getattr(self.chain, 'entry_quote', None)):
            if self.mode == 'LIVE':
                self.require_live()
            raw = raw_amount(settings.amount, self.pool.quote_decimals)
            check = self.chain.entry_quote(self.pool, raw, settings.max_roundtrip_loss)
            self.event.emit('entry_check', {'block': check.block,
                'roundtrip_loss_pct': str(check.roundtrip_loss_pct)})
            if self.stop_event.is_set():
                raise ValueError('STOP запрошен во время проверки входа')
        if self.mode == "LIVE":
            self.require_live()
            if self.stop_event.is_set():
                raise ValueError("STOP запрошен во время подготовки")
            amount = raw_amount(settings.amount, self.pool.quote_decimals)
            bound = snapshot_minimum(amount, self.current_price, self.pool.quote_decimals,
                                     self.pool.token_decimals, settings.buy_tolerance)
            self.live.begin("BUY " + self.pool.token)
            received = self.live.swap(self.pool, amount, True, settings.buy_tolerance,
                                      signal_minimum=bound)
            execution = (D(amount) / D(10)**self.pool.quote_decimals) / (D(received) / D(10)**self.pool.token_decimals)
            # Persist actual holdings even if the post-receipt price read fails.
            self.set_position(received, execution)
            self.position()['cost_quote'] = str(D(amount) / D(10)**self.pool.quote_decimals)
            self.position()['execution_price'] = str(execution)
            rate = self.rates.snapshot(self.pool.quote)
            fees = operation_fees(getattr(self.live, 'operation', None))
            cost_usd = marked_value(D(amount)/D(10)**self.pool.quote_decimals, rate)
            self.position()['entry_rate'] = rate
            self.position()['entry_fees'] = fees
            self.position()['entry_cost_usd'] = (str(D(cost_usd)+D(fees['usd']))
                if cost_usd is not None and fees['usd'] is not None else None)
            self.store.save()
            entry = self.read_price()
            self.position()['peak_price'] = str(entry)
            self.set_position(received, entry)
            self.live.finish()
        else:
            if self.mode == 'PAPER' and callable(getattr(self.chain, 'quote', None)):
                raw = raw_amount(settings.amount, self.pool.quote_decimals)
                quote = getattr(self.chain, 'paper_quote', self.chain.quote)
                quoted = quote(self.pool, raw, True)
                bound = snapshot_minimum(raw, self.current_price, self.pool.quote_decimals,
                                         self.pool.token_decimals, settings.buy_tolerance)
                if quoted < bound:
                    raise EntryRejected('PAPER BUY: котировка ниже minOut снимка; покупка не исполнена')
                execution = self.paper.buy_quoted(D(raw)/D(10)**self.pool.quote_decimals,
                    D(quoted)/D(10)**self.pool.token_decimals)
                self.log.emit('PAPER: router quote на сумму; комиссии/impact включены, газ и token tax не учтены')
            else:
                execution = self.paper.buy(settings.amount, self.current_price)
            entry = self.current_price
            self.paper_usd['entry'] = marked_value(self.paper.cost,
                self.rates.snapshot(self.pool.quote)) if self.mode == 'PAPER' and self.pool else None
        self.strategy.bought(entry, now=time.monotonic())
        self.record_market("execution", side="BUY", price=str(entry))
        self.event.emit("trade_marker", {"mode": self.mode, "side": "BUY", "price": str(entry)})
        self.log.emit(f"{self.mode} BUY: исполнение {execution:.10g}; база TP/SL {entry:.10g}")

    @monitor_execution
    @timed("worker.close_position")
    def close_position(self, reason):
        if self.mode == "LIVE":
            self.require_live()
            position = self.position()
            if not position:
                return
            self.live.begin("SELL " + self.pool.token)
            amount = min(position["amount"], self.chain.balance(self.pool.token, self.live.owner))
            if not amount:
                raise ValueError("Кэш позиции не совпадает с балансом; нужна сверка")
            received = self.live.swap(self.pool, amount, False, self.strategy.settings.slippage)
            if isinstance(received, int) and 'cost_quote' in position:
                pnl = D(received)/D(10)**self.pool.quote_decimals - D(position['cost_quote'])
                key = self.live.owner.lower() + ':' + self.pool.quote.lower()
                ledger = self.store.data.setdefault('realized_quote', {})
                ledger[key] = str(D(ledger.get(key, '0')) + pnl)
                self.log.emit(f'LIVE P&L: {pnl:+.8g} базового актива без газа; газ отдельно в журнале BNB')
            if isinstance(received, int):
                record_close(self.store, self.live.owner, self.pool, position, received,
                             getattr(self.live, 'operation', None), self.rates.snapshot(self.pool.quote),
                             inventory_matches=amount == position['amount'])
            self.set_position(0, 0)
            self.live.finish()
            price = self.current_price or D(position["entry"])
        else:
            if not self.paper.position:
                return
            price = self.read_price()
            paper_cost = self.paper.cost
            if self.mode == 'PAPER' and callable(getattr(self.chain, 'quote', None)):
                amount = raw_amount(self.paper.position, self.pool.token_decimals)
                quote = getattr(self.chain, 'paper_quote', self.chain.quote)
                output = quote(self.pool, amount, False)
                pnl = self.paper.sell_quoted(D(output)/D(10)**self.pool.quote_decimals)
            else:
                pnl = self.paper.sell(price)
            proceeds_usd = marked_value(paper_cost+pnl,
                self.rates.snapshot(self.pool.quote)) if self.mode == 'PAPER' and self.pool else None
            self.paper_usd['closed'] += 1
            if proceeds_usd is None or self.paper_usd['entry'] is None:
                self.paper_usd['missing'] += 1
            else:
                self.paper_usd['value'] += D(proceeds_usd)-D(self.paper_usd['entry'])
            self.paper_usd['entry'] = None
            self.log.emit(f"PAPER P&L: {pnl:+.8g} базового актива (без газа и token tax)")
        self.strategy.sold(price, reason, now=time.monotonic())
        self.record_market("execution", side="SELL", price=str(price), reason=reason)
        self.event.emit("trade_marker", {"mode": self.mode, "side": "SELL", "price": str(price)})
        if self.mode == "LIVE":
            # SELL is already accounted for if this independent read fails.
            self.strategy.base = self.read_price()
            self.strategy.last_price = self.strategy.base
            self.strategy.last_time = self.price_time
        self.halt_reason = ""
        self.log.emit(f"{self.mode} SELL: {reason}")

    def sweep(self):
        report = {"sold": [], "failed": [], "skipped": [], "remaining": {},
                  "unknown": [], "status": "interrupted"}
        tokens, checked = set(), set()
        try:
            completed = self._sweep(report, tokens, checked)
            report["status"] = "completed" if completed else "stopped"
            # STOP can arrive after a confirmed sale. Keep TP/SL consistent with
            # the accounted position, but never reconcile a pending operation here.
            if not self.store.data.get("operation"):
                position = self.position()
                self.strategy.entry = D(position["entry"]) if position else None
        except Exception as exc:
            report["status"] = "interrupted"
            report["error"] = safe_error(exc)
            raise
        finally:
            report["unknown"] = sorted(set(report["unknown"]) | (tokens - checked))
            report["needs_reconciliation"] = bool(self.store.data.get("operation"))
            self.event.emit("sweep_report", report)
            status = {"completed": "завершён", "stopped": "остановлен",
                      "interrupted": "прерван из-за ошибки"}[report["status"]]
            self.log.emit(f"SWEEP {status}: продано {len(report['sold'])}, ошибок {len(report['failed'])}, "
                          f"пропущено {len(report['skipped'])}, остатков {len(report['remaining'])}, "
                          f"не проверено {len(report['unknown'])}")

    def _account_sweep_token(self, token, residual):
        """Update this wallet's tracked positions after a verified balance read."""
        changed = False
        positions = self.store.data.get("positions", {})
        for key in list(positions):
            if (key.startswith(self.live.owner.lower() + ":")
                    and positions[key]["pool"]["token"].lower() == token.lower()):
                if residual:
                    amount = min(positions[key]["amount"], residual)
                    changed |= amount != positions[key]["amount"]
                    positions[key]["amount"] = amount
                else:
                    del positions[key]
                    changed = True
        return changed

    def _sweep(self, report, tokens, checked):
        self.log.emit("SWEEP: зарегистрированные target, затем базовые активы → BNB")
        # Saved positions remain recoverable even if another selection replaced
        # the known-pool entry for the same target before a restart.
        saved = [p["pool"] for key, p in self.store.data.get("positions", {}).items()
                 if key.startswith(self.live.owner.lower() + ":")]
        registered = wallet_registry.records(self.store, self.live.owner)
        # Once wallet-specific history exists, another wallet's selections must
        # not become targets. Legacy global history is used only before migration.
        historical = [r["pool"] for r in registered.values() if r.get("pool")] if registered else list(
            self.store.data.get("known_pools", {}).values())
        candidates = saved + historical
        unassigned = [p["token"] for p in self.store.data.get("known_pools", {}).values()
                      if registered and p["token"].lower() not in registered
                      and not any(s["token"].lower() == p["token"].lower() for s in saved)]
        pools = list({p["address"].lower(): p for p in candidates}.values())
        failures, skipped, sold = report["failed"], report["skipped"], report["sold"]
        skipped.extend(dict.fromkeys(unassigned))
        seen_targets = set()
        all_quotes = profiles() | self.store.data.get("dynamic_profiles", {})
        all_quotes.update({"POOL-"+p["quote"]: p["quote"] for p in pools})
        tokens.update(address(p["token"]) for p in pools)
        tokens.update(address(t) for t in all_quotes.values())
        tokens.update(address(t) for t in unassigned)
        catalogs = {router: dynamic.catalog(self.store,router) for router in ("V2","V3")}
        for raw in pools:
            if self.stop_event.is_set():
                self.log.emit("SWEEP остановлен между операциями")
                return
            pool = Pool(**raw)
            if pool.token.lower() in seen_targets:
                continue
            seen_targets.add(pool.token.lower())
            try:
                amount = self.chain.balance(pool.token, self.live.owner)
                if not amount:
                    continue
                if pool.token.lower() in registered:
                    result = self.chain.resolve_address(pool.token,catalogs)
                    candidate = wallet_registry.choose_registered(result,registered[pool.token.lower()])
                    if candidate is None:
                        skipped.append(pool.token)
                        continue
                    pool = candidate.pool
                self.chain.verify_pool(pool.address,pool.token)
                self.chain.quote(pool,amount,False)
            except Exception as exc:
                # No begin/sign/broadcast has happened: skip only this target.
                failures.append(pool.token)
                self.log.emit("SWEEP TARGET: " + safe_error(exc))
                continue
            if self.stop_event.is_set():
                return
            self.live.begin("SWEEP TARGET " + pool.token)
            self.live.swap(pool,amount,False,self.strategy.settings.slippage,
                           simulate=True, deadline_seconds=60)
            try:
                residual = self.chain.balance(pool.token, self.live.owner)
            except Exception:
                # A receipt confirms execution, not that the token balance is zero.
                # Keep the operation and position for reconciliation after restart.
                raise UncertainTransaction("SWEEP подтверждён, но остаток TARGET не прочитан; нужна сверка") from None
            positions = self.store.data.get("positions", {})
            for key in list(positions):
                if key.startswith(self.live.owner.lower()+":") and positions[key]["pool"]["token"].lower() == pool.token.lower():
                    if residual:
                        positions[key]["amount"] = min(positions[key]["amount"], residual)
                    else:
                        del positions[key]
            self.live.finish()
            sold.append(pool.token)
        seen = set()
        for symbol, token in wallet_registry.ordered_bases(all_quotes):
            token = address(token)
            if token in seen:
                continue
            seen.add(token)
            if self.stop_event.is_set():
                self.log.emit("SWEEP остановлен между операциями")
                return
            try:
                amount = self.chain.balance(token, self.live.owner)
            except Exception as exc:
                failures.append(token)
                self.log.emit("SWEEP BASE: " + safe_error(exc))
                continue
            if not amount:
                if self._account_sweep_token(token, 0):
                    self.store.save()
                if token in skipped:
                    skipped.remove(token)
                continue
            original_router = getattr(self.live, "trade_router", None)
            try:
                try:
                    self.live.trade_router = wallet_registry.base_router(symbol, token, catalogs)
                except ValueError as exc:
                    skipped.append(token)
                    self.log.emit("SWEEP BASE " + symbol + ": " + safe_error(exc))
                    continue
                # Missing route can be skipped only before a durable operation.
                if token != address(WBNB):
                    try:
                        self.live.conversion_route(token, WBNB, amount)
                    except Exception as exc:
                        failures.append(token)
                        self.log.emit("SWEEP BASE " + symbol + ": " + safe_error(exc))
                        continue
                if self.stop_event.is_set():
                    self.log.emit("SWEEP остановлен после проверки маршрута")
                    return
                self.live.begin("SWEEP BASE " + symbol)
                self.live.convert(token, amount, False, self.strategy.settings.slippage)
                # A target can also be a catalog base (e.g. USDT). Its sale via
                # Converter must account for positions just like a target swap.
                residual = self.chain.balance(token, self.live.owner)
                self._account_sweep_token(token, residual)
                self.live.finish()
                sold.append(token)
                if not residual and token in skipped:
                    skipped.remove(token)
            finally:
                self.live.trade_router = original_router
        remaining, unknown = report["remaining"], report["unknown"]
        # Enumerated target balances are part of verification, not only the UI's
        # current TARGET. Failed reads must never be reported as zero.
        for token in sorted(tokens):
            if self.stop_event.is_set():
                unknown.extend(sorted(tokens - checked))
                break
            checked.add(token)
            try:
                value = self.chain.balance(token,self.live.owner)
                if value:
                    remaining[token] = value
            except Exception:
                unknown.append(token)
        return not self.stop_event.is_set()
