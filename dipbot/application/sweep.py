from __future__ import annotations
"""Sequential Sweep use case with explicit dependencies and durable operations."""
from dipbot.application.errors import safe_error
from dipbot.application.ports import (StateStore, MarketReader, TradeExecutor, RateSource, StopSignal, LogSink, EventSink, PositionReader)
from dipbot.domain.strategy import Strategy
from dipbot.domain.strategy import D
from dipbot.market.chain import Pool, WBNB, address, profiles
from dipbot.persistence import dynamic, wallet_registry
from dipbot.persistence.storage import SaveAfterReplaceError
from dipbot.execution.errors import UncertainTransaction


class SweepService:
    def __init__(self, *, store: StateStore, chain: MarketReader, live: TradeExecutor,
                 strategy: Strategy, rates: RateSource, stop_event: StopSignal,
                 log: LogSink, emit: EventSink, position: PositionReader) -> None:
        self.store = store
        self.chain = chain
        self.live = live
        self.strategy = strategy
        self.rates = rates
        self.stop_event = stop_event
        self.log = log
        self.emit = emit
        self.position = position

    def run(self):
        report = {"sold": [], "failed": [], "skipped": [], "remaining": {},
                  "unknown": [], "status": "interrupted"}
        tokens, checked = set(), set()
        try:
            if self.store.data.get('operation'):
                raise UncertainTransaction("Незавершённая операция: Sweep требует сверки")
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
            self.emit("sweep_report", report)
            status = {"completed": "завершён", "stopped": "остановлен",
                      "interrupted": "прерван из-за ошибки"}[report["status"]]
            self.log(f"SWEEP {status}: продано {len(report['sold'])}, ошибок {len(report['failed'])}, "
                          f"пропущено {len(report['skipped'])}, остатков {len(report['remaining'])}, "
                          f"не проверено {len(report['unknown'])}")


    def _account_sweep_token(self, token, residual):
        """Bound aggregate tracked quantity; an unexplained remainder has no trusted basis."""
        if type(residual) is not int or not 0 <= residual < 2**256:
            raise ValueError('Некорректный остаток Sweep')
        changed = False
        remaining = residual
        positions = self.store.data.get('positions', {})
        for key in sorted(list(positions)):
            position = positions[key]
            if key.startswith(self.live.owner.lower()+':') and position['pool']['token'].lower()==token.lower():
                amount = min(position['amount'],remaining)
                remaining -= amount
                if not amount:
                    del positions[key]
                else:
                    position['amount'] = amount
                    position['entry_cost_usd'] = None
                    position.pop('cost_quote',None)
                    position['basis_incomplete'] = 'SWEEP residual / external flow requires reconciliation'
                changed = True
        return changed


    def _clear_zero_sweep_position(self, token):
        # An external zero balance has no known proceeds: do not invent a close/P&L.
        from copy import deepcopy
        previous = deepcopy(self.store.data.get('positions', {}))
        try:
            if self._account_sweep_token(token, 0):
                self.store.save()
        except SaveAfterReplaceError:
            # File already replaced; keep the matching visible state and halt.
            raise
        except Exception:
            self.store.data['positions'] = previous
            raise


    def _record_sweep_exit(self, token, amount, residual, received, quote, decimals, pool_address=''):
        from dipbot.execution.accounting import record_sweep_exit
        record_sweep_exit(self.store,self.live.owner,token,amount,residual,received,quote,decimals,
            getattr(self.live,'operation',None),self.rates.snapshot(quote),pool_address=pool_address)


    def _sweep(self, report, tokens, checked):
        self.log("SWEEP: зарегистрированные target, затем базовые активы → BNB")
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
                self.log("SWEEP остановлен между операциями")
                return
            pool = Pool(**raw)
            if pool.token.lower() in seen_targets:
                continue
            seen_targets.add(pool.token.lower())
            try:
                amount = self.chain.balance(pool.token, self.live.owner)
            except Exception as exc:
                failures.append(pool.token)
                self.log("SWEEP TARGET: " + safe_error(exc))
                continue
            if not amount:
                self._clear_zero_sweep_position(pool.token)
                continue
            try:
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
                self.log("SWEEP TARGET: " + safe_error(exc))
                continue
            if self.stop_event.is_set():
                return
            self.live.begin("SWEEP TARGET " + pool.token)
            received = self.live.swap(pool,amount,False,self.strategy.settings.slippage,
                           simulate=True, deadline_seconds=60)
            try:
                residual = self.chain.balance(pool.token, self.live.owner)
            except Exception:
                # A receipt confirms execution, not that the token balance is zero.
                # Keep the operation and position for reconciliation after restart.
                raise UncertainTransaction("SWEEP подтверждён, но остаток TARGET не прочитан; нужна сверка") from None
            self._record_sweep_exit(pool.token,amount,residual,received,pool.quote,pool.quote_decimals,pool.address)
            self._account_sweep_token(pool.token,residual)
            self.live.finish()
            sold.append(pool.token)
        seen = set()
        for symbol, token in wallet_registry.ordered_bases(all_quotes):
            token = address(token)
            if token in seen:
                continue
            seen.add(token)
            if self.stop_event.is_set():
                self.log("SWEEP остановлен между операциями")
                return
            try:
                amount = self.chain.balance(token, self.live.owner)
            except Exception as exc:
                failures.append(token)
                self.log("SWEEP BASE: " + safe_error(exc))
                continue
            if not amount:
                self._clear_zero_sweep_position(token)
                if token in skipped:
                    skipped.remove(token)
                continue
            original_router = getattr(self.live, "trade_router", None)
            try:
                try:
                    self.live.trade_router = wallet_registry.base_router(symbol, token, catalogs)
                except ValueError as exc:
                    skipped.append(token)
                    self.log("SWEEP BASE " + symbol + ": " + safe_error(exc))
                    continue
                # Missing route can be skipped only before a durable operation.
                if token != address(WBNB):
                    try:
                        self.live.conversion_route(token, WBNB, amount)
                    except Exception as exc:
                        failures.append(token)
                        self.log("SWEEP BASE " + symbol + ": " + safe_error(exc))
                        continue
                if self.stop_event.is_set():
                    self.log("SWEEP остановлен после проверки маршрута")
                    return
                self.live.begin("SWEEP BASE " + symbol)
                received = self.live.convert(token, amount, False, self.strategy.settings.slippage)
                # A target can also be a catalog base (e.g. USDT). Its sale via
                # Converter must account for positions just like a target swap.
                residual = self.chain.balance(token, self.live.owner)
                self._record_sweep_exit(token,amount,residual,received,WBNB,18)
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
