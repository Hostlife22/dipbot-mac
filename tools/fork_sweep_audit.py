"""Sweep fault scenarios on the already isolated Anvil chain; no real wallet."""

from copy import deepcopy
from decimal import Decimal as D
from pathlib import Path

from dipbot.application.worker import Worker
from dipbot.domain.assets import USDT, WBNB
from dipbot.execution.errors import UncertainTransaction
from dipbot.execution.trader import LiveTrader
from dipbot.market.chain import address
from dipbot.persistence.storage import Store


def audit(chain, account, directory, pool, amount, results=None):
    other = next(p for p in chain.find_pools(address(USDT), address(WBNB)) if p.router == "V2")
    if pool.token == other.token:
        raise ValueError("Two distinct targets required")
    state = Path(directory) / "sweep.json"
    store = Store(state)
    trader = LiveTrader(chain, account.key, store, D(".1"), lambda _: None)
    trader.begin("LOCAL WRAP")
    trader.wrap(amount * 2)
    trader.finish()
    w = Worker(store)
    w.mode, w.chain, w.live = "LIVE", chain, trader
    for p in (pool, other):
        trader.begin("BUY local sweep setup")
        received = trader.swap(p, amount, True, D(3), simulate=True)
        trader.finish()
        w.pool = p
        w.set_position(received, D(1))
    store.save()
    baseline = deepcopy(store.data)
    results = [] if results is None else results
    original_quote = chain.quote
    original_send = chain.w3.eth.send_raw_transaction
    original_trader_send = trader.send
    for scenario in ("partial_preflight", "stop_quote", "stop_approve", "stop_receipt", "lost_send_response"):
        print("Local Sweep scenario: " + scenario, flush=True)
        snapshot = chain.w3.provider.make_request("evm_snapshot", [])["result"]
        store.data = deepcopy(baseline)
        store.save()
        trader.operation = None
        w.pool = pool
        w.stop_event.clear()
        reports = []
        messages = []
        traces = []
        w.record_market = lambda event, **kw: traces.append(dict(event=event, environment="FORK", **kw))
        attempt = {"scenario": scenario, "passed": False, "traces": traces}
        results.append(attempt)
        w.log.connect(messages.append)

        def report(name, value):
            if name == "sweep_report":
                reports.append(value)

        w.event.connect(report)

        def quote(p, *args):
            if p.token == pool.token:
                if scenario == "partial_preflight":
                    raise TimeoutError("Injected preflight read failure")
                if scenario == "stop_quote":
                    w.stop_event.set()
            return original_quote(p, *args)

        def send(function, label, **kwargs):
            receipt = original_trader_send(function, label, **kwargs)
            if (
                scenario == "stop_approve"
                and label.startswith("APPROVE")
                or scenario == "stop_receipt"
                and label == "SELL"
            ):
                w.stop_event.set()
            return receipt

        def lost(raw):
            original_send(raw)  # Actually mined locally; reply is deliberately lost.
            raise TimeoutError("Injected lost send reply")

        chain.quote = quote
        trader.send = send
        if scenario == "lost_send_response":
            chain.w3.eth.send_raw_transaction = lost
        try:
            try:
                w.sweep()
                assert scenario != "lost_send_response"
            except UncertainTransaction:
                assert scenario == "lost_send_response"
            result = reports[-1]
            attempt["report"] = result
            assert result["status"] == (
                "interrupted"
                if scenario == "lost_send_response"
                else "completed"
                if scenario == "partial_preflight"
                else "stopped"
            )
            if scenario == "partial_preflight":
                assert pool.token in result["failed"] and other.token in result["sold"]
                assert result["remaining"][pool.token] > 0
            elif scenario == "stop_quote":
                assert not result["sold"] and not store.data.get("operation")
            elif scenario in ("stop_approve", "stop_receipt"):
                # Current exit finishes/accounting completes; no next token starts.
                assert result["sold"] == [pool.token]
                assert not store.data.get("operation")
            else:
                assert result["needs_reconciliation"]
                restored = Store(state)
                recovered = LiveTrader(chain, account.key, restored, D(".1"), lambda _: None)

                def no_send(*args):
                    raise AssertionError("Recovery must never resend")

                chain.w3.eth.send_raw_transaction = no_send
                # A lost broadcast reply can precede local mining; first observe
                # the pending state, then wait for its receipt without resending.
                pending = restored.data["operation"]["transactions"][0]
                chain.w3.eth.wait_for_transaction_receipt(pending["hash"], timeout=20)
                recovered.reconcile()
                assert restored.data["operation"]["transactions"][0]["status"] == "confirmed"
                try:
                    recovered.begin("duplicate")
                except UncertainTransaction:
                    pass
                else:
                    raise AssertionError("Balance review must remain required")
            attempt["passed"] = True
        except Exception as exc:
            attempt["error_type"] = type(exc).__name__
            attempt["logs"] = messages[-30:]
            if reports:
                attempt["report"] = reports[-1]
            raise
        finally:
            w.log.disconnect(messages.append)
            chain.quote = original_quote
            chain.w3.eth.send_raw_transaction = original_send
            trader.send = original_trader_send
            w.event.disconnect(report)
            assert chain.w3.provider.make_request("evm_revert", [snapshot])["result"]
    return results
