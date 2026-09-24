from dataclasses import asdict
import queue
import re
import threading
import time

from PySide6.QtCore import QThread, Signal
from eth_account import Account

from .chain import Chain, Pool, WBNB, address, profiles
from .storage import Store, Vault
from . import dynamic
from .autopair import choose, ordered
from .strategy import D, Settings, Strategy, raw_amount, snapshot_minimum
from .trader import LiveTrader, PaperTrader, UncertainTransaction


def safe_error(exc):
    # Provider exceptions can contain RPC credentials. Do not log arbitrary text.
    if type(exc) in (ValueError, RuntimeError, UncertainTransaction):
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
        self.pool = None
        self.mode = "DEMO"
        self.running = False
        self.strategy = Strategy(Settings())
        self.paper = PaperTrader(D("2"))
        self.live = None
        self.tick = 0
        self.current_price = None
        self.price_time = 0.0
        self.interval = 0.1

    def submit(self, name, **data):
        self.commands.put((name, data))

    def run(self):
        next_tick = time.monotonic()
        while not self.quit_event.is_set():
            try:
                timeout = min(0.05, max(0, next_tick - time.monotonic())) if self.running else 0.05
                name, data = self.commands.get(timeout=timeout)
            except queue.Empty:
                name = None
            if name:
                self.event.emit("busy", True)
                try:
                    self.command(name, data)
                except Exception as exc:
                    self.running = False
                    self.log.emit("ОШИБКА: " + safe_error(exc))
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
                    self.log.emit("BOT остановлен")
                except Exception as exc:
                    self.running = False
                    self.event.emit("error", safe_error(exc))
                    self.log.emit("STOP: " + safe_error(exc))
                self.status()
            if self.running and time.monotonic() >= next_tick:
                try:
                    self.observe()
                except Exception as exc:
                    # Never continue automatically after a trade/RPC failure.
                    self.running = False
                    self.log.emit("BOT приостановлен: " + safe_error(exc))
                    self.event.emit("error", safe_error(exc))
                next_tick = time.monotonic() + self.interval
                self.status()

    def status(self):
        self.event.emit("status", {"running": self.running, "mode": self.mode,
                         "position": str(self.paper.position) if self.mode != "LIVE" else str(D(self.position().get("amount", 0)) / D(10)**(self.pool.token_decimals if self.pool else 18)),
                         "base": str(self.strategy.base or 0),
                         "entry": str(self.strategy.entry or 0),
                         "realized": str(self.paper.realized) if self.mode != "LIVE" else "—",
                         "locked": bool(self.store.data.get("operation"))})

    def position_key(self):
        return f"{self.live.owner.lower()}:{self.pool.address.lower()}" if self.live and self.pool else ""

    def position(self):
        return self.store.data.get("positions", {}).get(self.position_key(), {})

    def set_position(self, amount, entry):
        positions = self.store.data.setdefault("positions", {})
        if amount:
            positions[self.position_key()] = {"amount": amount, "entry": str(entry), "pool": asdict(self.pool)}
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
        settings = Settings(**{k: D(v) for k, v in data["settings"].items()})
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
            if self.store.data.get("operation"):
                raise UncertainTransaction("Есть незавершённая операция: используйте сверку в настройках")
        if self.pool and self.pool.router == "V3":
            interval = max(interval, 0.103)
        self.mode, self.interval, self.live = mode, interval, live
        old_entry = self.strategy.entry
        self.strategy = Strategy(settings)
        if mode == "LIVE" and self.position():
            self.strategy.entry = D(self.position()["entry"])
        elif mode != "LIVE" and self.paper.position:
            self.strategy.entry = old_entry
        self.paper.slippage = settings.slippage

    def command(self, name, data):
        if self.stop_event.is_set() and name in ("start", "buy", "convert", "sweep"):
            raise ValueError("STOP запрошен: новая торговая операция отменена")
        if self.running and name not in ("sell",):
            raise ValueError("Сначала остановите BOT")
        if name in ("connect", "discover", "verify", "select", "wallet", "remove_profile") and (self.paper.position or self.position()):
            raise ValueError("Сначала закройте текущую позицию")
        if name == "connect":
            chain = Chain(data["rpc"])
            block = chain.check()
            self.chain = chain
            self.pool = None
            self.event.emit("pools", [])
            self.log.emit(f"BSC подключена, chainId 56, блок {block}")
            if data.get("save"):
                Vault().save("rpc", data["rpc"])
        elif name == "wallet":
            account = Account.from_key(data["key"])
            Vault().save("wallet", data["key"])
            self.store.data["wallet_address"] = account.address
            self.store.save()
            self.event.emit("wallet", account.address)
            self.log.emit("Кошелёк сохранён в macOS Keychain: " + account.address)
        elif name == "discover":
            self.require_chain()
            # Clear stale selection before RPC: failure/cancellation cannot retain it.
            self.pool = None
            self.event.emit("pools", [])
            target = address(data["token"])
            candidates = []
            routers = ("V2", "V3") if data["router"] == "AUTO" else (data["router"],)
            for router in routers:
                catalog = dynamic.catalog(self.store, router)
                if data["quote"] == "ALL":
                    quotes = list(catalog.items())
                elif data["quote"] in catalog:
                    quotes = [(data["quote"], catalog[data["quote"]])]
                else:
                    quotes = []
                for i, (name, quote) in enumerate(quotes):
                    if self.quit_event.is_set() or self.stop_event.is_set():
                        self.log.emit("AutoPair отменён; выбор пула сброшен")
                        return
                    self.log.emit(f"AutoPair {router}: {i+1}/{len(quotes)}")
                    candidates.extend(self.chain.discover_candidates(target, quote, router, name))
            if self.quit_event.is_set() or self.stop_event.is_set():
                return
            candidates = ordered(candidates)
            state, selected = choose(candidates)
            self.event.emit("pools", [c.pool for c in candidates if c.ready])
            self.log.emit(f"AutoPair: {state}; найдено {len(candidates)} пулов")
            if state == "RESOLVED":
                self.select_pool(selected.pool)
            elif state == "PENDING":
                self.log.emit("PENDING: пул найден, ликвидности нет. Повторите AutoPair после её появления")
            self.event.emit("autopair", state)
        elif name == "verify":
            self.require_chain()
            pool = self.chain.verify_pool(data["pool"], data["token"])
            # Discovery alone is not proof of converter availability.
            self.event.emit("pools", [pool])
            self.select_pool(pool)
        elif name == "select":
            self.select_pool(data["pool"])
        elif name == "add_profile":
            self.require_chain()
            if not self.pool:
                raise ValueError("Сначала CHECK + ADD или AutoPair")
            # Recheck target pool too: selection may predate a liquidity change.
            self.pool = self.chain.verify_pool(self.pool.address, self.pool.token)
            # Quote round trip is checked without a private key or any transaction.
            helper = object.__new__(LiveTrader)
            helper.chain = self.chain
            helper.store = self.store
            helper.trade_router = self.pool.router
            buy_route = None
            sample = 10**16  # 0.01 WBNB
            amount = sample
            if self.pool.quote != address(WBNB):
                route = helper.conversion_route(WBNB, self.pool.quote, amount)
                buy_route = route
                amount = self.chain.quote_route(route, amount)
                route = helper.conversion_route(self.pool.quote, WBNB, amount)
                amount = self.chain.quote_route(route, amount)
            if amount < sample * 90 // 100:
                raise ValueError("Round-trip loss для 0.01 WBNB превышает 10%")
            if self.pool.quote not in profiles().values():
                dynamic.upsert(self.store, self.pool, buy_route, max(0, (sample-amount)*10000//sample))
                self.event.emit("profiles", self.store.data["dynamic_profiles"])
            self.log.emit("ADDED: базовый актив проверен для конвертера; котировка не проверяет token tax / blacklist")
        elif name == "remove_profile":
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
            dynamic.remove(self.store, symbol)
            self.pool = None
            self.event.emit("pools", [])
            self.event.emit("profiles", self.store.data.get("dynamic_profiles", {}))
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
        elif name in ("reconcile", "unlock"):
            self.require_chain()
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

    def select_pool(self, pool):
        if self.paper.position or self.position():
            raise ValueError("Сначала закройте позицию текущего пула")
        self.pool = self.chain.verify_pool(pool.address, pool.token)
        self.store.data.setdefault("known_pools", {})[self.pool.address.lower()] = asdict(self.pool)
        self.store.data["last_pool"] = asdict(self.pool)
        self.store.save()
        self.event.emit("selected", self.pool)
        self.log.emit("Выбран " + self.pool.label)
        self.read_price(force_chain=True)

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
            price = self.chain.price(self.pool)
        self.current_price = price
        self.price_time = time.monotonic()
        self.event.emit("price", str(price))
        return price

    def observe(self):
        price = self.read_price()
        now = time.monotonic()
        if (self.strategy.entry is None and self.strategy.last_time is not None
                and now - self.strategy.last_time > self.strategy.settings.max_gap):
            self.log.emit("Разрыв котировок > 0.55 с: база DIP сброшена")
        action = self.strategy.observe(price, now)
        if self.stop_event.is_set():
            return
        if action == "BUY":
            self.open_position()
        elif action:
            self.close_position(action)
            if self.strategy.stopped:
                self.running = False

    def open_position(self):
        if self.strategy.entry is not None:
            raise ValueError("Позиция уже открыта")
        settings = self.strategy.settings
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
            entry = self.read_price()
            self.set_position(received, entry)
            self.live.finish()
        else:
            execution = self.paper.buy(settings.amount, self.current_price)
            entry = self.current_price
        self.strategy.bought(entry)
        self.log.emit(f"{self.mode} BUY: исполнение {execution:.10g}; база TP/SL {entry:.10g}")

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
            self.live.swap(self.pool, amount, False, self.strategy.settings.slippage)
            self.set_position(0, 0)
            self.live.finish()
            price = self.current_price or D(position["entry"])
        else:
            if not self.paper.position:
                return
            price = self.read_price()
            pnl = self.paper.sell(price)
            self.log.emit(f"PAPER P&L: {pnl:+.8g} базового актива (без газа и token tax)")
        self.strategy.sold(price, reason)
        if self.mode == "LIVE":
            # SELL is already accounted for if this independent read fails.
            self.strategy.base = self.read_price()
            self.strategy.last_price = self.strategy.base
            self.strategy.last_time = self.price_time
        self.log.emit(f"{self.mode} SELL: {reason}")

    def sweep(self):
        self.log.emit("SWEEP: зарегистрированные target, затем базовые активы → BNB")
        # Saved positions remain recoverable even if another selection replaced
        # the known-pool entry for the same target before a restart.
        saved = [p["pool"] for key, p in self.store.data.get("positions", {}).items()
                 if key.startswith(self.live.owner.lower() + ":")]
        candidates = saved + list(self.store.data.get("known_pools", {}).values())
        pools = list({p["address"].lower(): p for p in candidates}.values())
        failures = []
        for raw in pools:
            if self.stop_event.is_set():
                self.log.emit("SWEEP остановлен между операциями")
                return
            pool = Pool(**raw)
            amount = self.chain.balance(pool.token, self.live.owner)
            if not amount:
                continue
            # Verify/quote before opening an operation so a missing route can be skipped.
            try:
                self.chain.verify_pool(pool.address, pool.token)
                self.chain.quote(pool, amount, False)
            except ValueError:
                failures.append(pool.token)
                continue
            if self.stop_event.is_set():
                self.log.emit("SWEEP остановлен после проверки маршрута")
                return
            self.live.begin("SWEEP TARGET " + pool.token)
            try:
                self.live.swap(pool, amount, False, self.strategy.settings.slippage)
                positions = self.store.data.get("positions", {})
                for key in list(positions):
                    if key.startswith(self.live.owner.lower()+":") and positions[key]["pool"]["token"].lower() == pool.token.lower():
                        del positions[key]
                self.live.finish()
            except Exception:
                # Ambiguous transactions stop the whole sweep; never risk a duplicate send.
                raise
        all_quotes = profiles() | self.store.data.get("dynamic_profiles", {})
        all_quotes.update({"POOL-"+p["quote"]: p["quote"] for p in pools})
        seen = set()
        for symbol, token in all_quotes.items():
            token = address(token)
            if token in seen:
                continue
            seen.add(token)
            if self.stop_event.is_set():
                self.log.emit("SWEEP остановлен между операциями")
                return
            amount = self.chain.balance(token, self.live.owner)
            if not amount:
                continue
            # Select registry context per base; a V3-only base must not inherit
            # an unrelated V2 trading selection. Conflicts require explicit context.
            registered = [r for r in dynamic.records(self.store).values()
                          if address(r["token_address"]) == token]
            original_router = getattr(self.live, "trade_router", None)
            context = next((r["trade_router"] for r in registered
                            if r["trade_router"] == original_router), None)
            if context is None and len(registered) == 1:
                context = registered[0]["trade_router"]
            self.live.trade_router = context or original_router
            try:
                # Missing route can be skipped only before a durable operation.
                if token != address(WBNB):
                    try:
                        self.live.conversion_route(token, WBNB, amount)
                    except ValueError:
                        failures.append(symbol)
                        self.log.emit("Нет маршрута: " + symbol)
                        continue
                if self.stop_event.is_set():
                    self.log.emit("SWEEP остановлен после проверки маршрута")
                    return
                self.live.begin("SWEEP BASE " + symbol)
                self.live.convert(token, amount, False, self.strategy.settings.slippage)
                self.live.finish()
            finally:
                self.live.trade_router = original_router
        # A skipped target still has an open position and must retain TP/SL.
        position = self.position()
        self.strategy.entry = D(position["entry"]) if position else None
        self.log.emit("SWEEP завершён" + ("; без маршрута: " + ", ".join(failures) if failures else ""))
        # Always report residual balances, including dust or transfer-tax artifacts.
        self.command("balance", {"wallet": self.live.owner})
