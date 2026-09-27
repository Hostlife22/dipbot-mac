"""Explicitly authorized, budget-limited LIVE UI audit on BSC USDT/WBNB only.

Requires --execute. Uses a separate durable journal. Never persists the private key.
Fees plus ALL native principal introduced are bounded conservatively by $0.90
at 110% of the latest fetched BNB/USD, inside the user's $1 loss/fee budget.
"""

import argparse
import json
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from decimal import Decimal as D
from pathlib import Path
from unittest.mock import patch

import requests
from eth_account import Account
from PySide6.QtWidgets import QApplication, QMessageBox, QPushButton

from dipbot.domain.assets import USDT, V2_ROUTER, V3_ROUTER, WBNB
from dipbot.domain.usd import select_rate
from dipbot.execution.trader import LiveTrader
from dipbot.market.chain import Chain, address
from dipbot.persistence.storage import Store
from dipbot.persistence.vault import Vault
from dipbot.ui.theme import STYLE
from dipbot.ui.window import Window


def reserve_cost_usd(previous, gas_wei, value_wei, fx):
    """Historical USD reservations cannot shrink with refunds or exchange rates."""
    previous, fx = D(previous), D(fx)
    if not previous.is_finite() or previous < 0 or not fx.is_finite() or fx <= 0:
        raise ValueError("Invalid audit budget/rate")
    if type(gas_wei) is not int or type(value_wei) is not int or min(gas_wei, value_wei) < 0:
        raise ValueError("Invalid audit reservation")
    total = previous + D(gas_wei + value_wei) / 10**18 * fx
    if total > D(".90"):
        raise ValueError("Audit cost ceiling: stop before signing")
    return total


def observe_finality(chain, receipt, timeout=15, received_ns=None):
    """Provider-reported finality after send returned; never change execution outcome."""
    started = time.monotonic()
    received_ns = time.perf_counter_ns() if received_ns is None else received_ns
    queue_ms = (time.perf_counter_ns() - received_ns) / 1e6
    errors = []
    while time.monotonic() - started < timeout:
        try:
            finalized = chain.w3.eth.get_block("finalized")
            if finalized["number"] >= receipt["blockNumber"]:
                chain.canonical_receipt(receipt)
                return {
                    "observed": True,
                    "post_send_return_to_finalized_ms": (time.perf_counter_ns() - received_ns) / 1e6,
                    "observer_queue_ms": queue_ms,
                    "finalized_block": finalized["number"],
                    "errors": errors,
                    "rule": "provider finalized number >= receipt block, canonical receipt hash rechecked",
                }
        except Exception as exc:
            errors.append(type(exc).__name__)
            break
        time.sleep(0.5)
    return {"observed": False, "errors": errors, "reason": "timeout or provider/canonical check failure"}


def lose_send_ack(sender, on_sent):
    """Inject one client-side lost ACK, and forbid every subsequent send in this scope."""
    attempted = False

    def send(raw):
        nonlocal attempted
        if attempted:
            raise RuntimeError("Repeated audit send forbidden")
        attempted = True
        sender(raw)
        on_sent()
        raise TimeoutError("Controlled lost broadcast acknowledgment")

    return send


def merge_settled_receipts(report, state):
    """Include reverted gas even when send raised before returning its receipt."""
    known = {r["hash"].removeprefix("0x").lower() for r in report["receipts"]}
    operations = list(state.get("history", []))
    if state.get("operation"):
        operations.append(state["operation"])
    for operation in operations:
        for tx in operation.get("transactions", []):
            key = tx["hash"].removeprefix("0x").lower()
            if key in known or tx.get("status") not in {"confirmed", "reverted"}:
                continue
            if not all(k in tx for k in ("block", "block_hash", "gas_fee_wei")):
                continue
            report["receipts"].append(
                {
                    "label": tx["label"],
                    "hash": tx["hash"],
                    "status": 1 if tx["status"] == "confirmed" else 0,
                    "block": tx["block"],
                    "block_hash": tx["block_hash"],
                    "gas_fee_wei": tx["gas_fee_wei"],
                    "receipt_source": "validated durable journal",
                }
            )
            known.add(key)


def run(
    key_path,
    directory,
    resume=False,
    sweep_only=False,
    sweep_multi=False,
    sweep_stop=False,
    exit_retry=False,
    sweep_route=False,
    inject_lost_ack=False,
    automatic_token=None,
    automatic_seconds=600,
    automatic_pool=None,
    cleanup_only=False,
):
    if cleanup_only and (not automatic_token or not automatic_pool):
        raise ValueError("Cleanup requires the original automatic token and pool")
    if automatic_pool and not automatic_token:
        raise ValueError("Explicit automatic pool requires a token")
    if automatic_token and (
        not 1 <= automatic_seconds <= 600
        or any((resume, sweep_only, sweep_multi, sweep_stop, sweep_route, exit_retry, inject_lost_ack))
    ):
        raise ValueError("Automatic audit requires a new journal and 1..600 seconds")
    audit_token = address(automatic_token) if automatic_token else USDT
    if inject_lost_ack and (resume or sweep_only or sweep_multi or sweep_stop or sweep_route or exit_retry):
        raise ValueError("Lost ACK injection requires a new audit")
    sweep_mode = sweep_only or sweep_multi or sweep_stop or sweep_route
    multi = sweep_multi or sweep_stop or sweep_route
    sweep_state = (
        "state-sweep-route.json"
        if sweep_route
        else "state-sweep-stop.json"
        if sweep_stop
        else "state-sweep-multi.json"
        if sweep_multi
        else "state-sweep.json"
    )
    directory.mkdir(parents=True, exist_ok=resume or sweep_mode or exit_retry or cleanup_only)
    key = key_path.read_text().strip()
    account = Account.from_key(key)
    chain = Chain("https://bsc-dataseed.binance.org")
    chain.check()
    assert not Store().data.get("operation"), "Existing application operation must be reconciled first"
    assert chain.w3.eth.get_transaction_count(
        account.address, "pending"
    ) == chain.w3.eth.get_transaction_count(account.address, "latest")
    expected_wrapped = 10**14 if resume else 0
    assert (
        chain.balance(WBNB, account.address) == expected_wrapped
        and chain.balance(audit_token, account.address) == 0
    ), "Unexpected holdings: do not continue"
    if not resume and not cleanup_only:
        assert all(not Store(p).data.get("operation") for p in directory.glob("state*.json")), (
            "Earlier audit needs reconciliation"
        )
    before = chain.w3.eth.get_balance(account.address)
    pools = (
        [chain.verify_pool(automatic_pool, audit_token)]
        if automatic_pool
        else chain.find_pools(audit_token, WBNB)
    )
    v2 = next(p for p in pools if p.router == "V2")
    audit_base = v2.quote
    assert cleanup_only or audit_base == address(WBNB) or chain.balance(audit_base, account.address) == 0, (
        "Existing base holdings"
    )
    v3s = [p for p in pools if p.router == "V3"]
    v3 = max(v3s, key=lambda p: chain.quote(p, 30000000000000, True)) if v3s else None
    report = {
        "mode": "LIVE",
        "target_token": audit_token,
        "base_token": audit_base,
        "automatic_max_buys": 2 if automatic_token else None,
        "scenarios": [],
        "receipts": [],
        "errors": [],
        "max_position_usd": "1",
        "max_total_cost_usd": "1",
        "gross_native_value": 0,
        "reserved_gas_wei": 0,
        "ui_mismatches": [],
    }
    if resume or sweep_mode or exit_retry or cleanup_only:
        report = json.loads((directory / "report.json").read_text())
        assert report.get("target_token") == audit_token, "Audit token changed"
        assert report.get("base_token", WBNB) == audit_base, "Audit base changed"
        if cleanup_only:
            report["automatic_scenario_passed"] = report.get("passed", False)
            report["automatic_failure_type"] = report.get("failure_type")
        if sweep_mode or exit_retry:
            assert report.get("passed") and not report.get("journal_locked")
            assert not Store(directory / "state.json").data.get("operation")
            assert not (directory / ("state-exit-retry.json" if exit_retry else sweep_state)).exists(), (
                "Audit already attempted; review it first"
            )
        report["earlier_errors"] = report.get("earlier_errors", []) + report["errors"]
        report["errors"] = []
        report.pop("failure_type", None)
        # The previous snapshot may precede inclusion after a lost ACK.
        # Never reconstruct the initial balance from that transient delta.
        before = int(report["initial_native_balance_wei"])
    report["initial_native_balance_wei"] = before
    report["passed"] = False
    report["status"] = "running"
    report["audit_finality_observer"] = "background independent Chain; queued time included"
    logs = []
    observer = ThreadPoolExecutor(max_workers=1, thread_name_prefix="audit-finality")
    observations = []

    def finality_read(receipt, received_ns):
        try:
            reader = Chain("https://bsc-dataseed.binance.org", request_timeout=3)
            reader.check()
            return observe_finality(reader, receipt, received_ns=received_ns)
        except Exception as exc:
            return {"observed": False, "errors": [type(exc).__name__]}

    def save():
        saved = Store(directory / "report.json")
        saved.data = report
        saved.save()

    original_send = LiveTrader.send

    def budgeted_send(trader, function, label, value=0):
        if automatic_token and label == "BUY" and sum(r["label"] == "BUY" for r in report["receipts"]) >= 2:
            raise ValueError("Automatic audit BUY limit reached before signing")

        class BudgetFunction:
            def estimate_gas(self, tx):
                estimate = function.estimate_gas(tx)
                gas = (estimate * 120 + 99) // 100
                budget_started = time.monotonic()
                response = requests.get("https://api.dexscreener.com/tokens/v1/bsc/" + WBNB, timeout=10)
                response.raise_for_status()
                fx = select_rate(response.json(), WBNB) * D("1.10")
                next_usd = reserve_cost_usd(
                    report.get("conservative_cost_bound_usd", "0"), gas * trader.gas_price, value, fx
                )
                assert D(value) / 10**18 * fx <= D("1"), "Position cap exceeded"
                report["reserved_gas_wei"] += gas * trader.gas_price
                report["gross_native_value"] += value
                report["conservative_cost_bound_usd"] = str(next_usd)
                report["bnb_usd_with_10pct_margin"] = str(fx)
                save()  # Retain the whole reservation even after an uncertain result.
                report.setdefault("budget_check_ms", []).append((time.monotonic() - budget_started) * 1000)
                return estimate

            def build_transaction(self, tx):
                built = function.build_transaction(tx)
                assert built["to"].lower() in {
                    a.lower() for a in [WBNB, audit_token, audit_base, V2_ROUTER, V3_ROUTER]
                }, "Recipient outside audit allowlist"
                return built

        def sent():
            report["lost_ack_injected"] = True
            save()

        if inject_lost_ack and not report.get("lost_ack_injected"):
            if label != "BNB → WBNB" or report["receipts"]:
                raise ValueError("Lost ACK injection is limited to the initial wrap")
            with patch.object(
                trader.chain.w3.eth,
                "send_raw_transaction",
                lose_send_ack(trader.chain.w3.eth.send_raw_transaction, sent),
            ):
                receipt = original_send(trader, BudgetFunction(), label, value)
        else:
            receipt = original_send(trader, BudgetFunction(), label, value)
        received_ns = time.perf_counter_ns()
        trace = getattr(trader, "cycle_trace", None)
        row = {
            "label": label,
            "hash": receipt["transactionHash"].hex(),
            "status": receipt["status"],
            "block": receipt["blockNumber"],
            "block_hash": receipt["blockHash"].hex(),
            "gas_fee_wei": receipt["gasUsed"] * receipt["effectiveGasPrice"],
            "finality": {"observed": False, "reason": "observer pending"},
            "audit_finality_mode": "background",
        }
        report["receipts"].append(row)
        if len(observations) < 64:
            observations.append(
                (
                    row,
                    observer.submit(finality_read, receipt, received_ns),
                    trace.data["cycle_id"] if trace else None,
                    (received_ns - trace.started_ns) / 1e6 if trace else None,
                )
            )
        else:
            row["finality"] = {"observed": False, "reason": "observer capacity"}
        save()
        print(
            json.dumps(
                {
                    "confirmed": label,
                    "transactions": len(report["receipts"]),
                    "cost_bound_usd": report["conservative_cost_bound_usd"],
                }
            ),
            flush=True,
        )
        return receipt

    app = QApplication([])
    app.setStyleSheet(STYLE)
    app.setQuitOnLastWindowClosed(False)

    def vault_get(_, name):
        if name == "wallet":
            return key
        raise RuntimeError("Only the authorized audit wallet is available")

    with (
        patch.object(Vault, "get", vault_get),
        patch.object(Vault, "save", side_effect=RuntimeError("No Keychain writes in audit")),
        patch.object(LiveTrader, "send", budgeted_send),
        patch.object(QMessageBox, "question", lambda *a: QMessageBox.Yes),
        patch.object(QMessageBox, "warning", lambda *a: report["errors"].append(a[2])),
        patch("dipbot.application.sweep.profiles", lambda: {"WBNB": WBNB}) if sweep_mode else nullcontext(),
        (
            patch("dipbot.persistence.dynamic.catalog", lambda store, router: {"WBNB": WBNB})
            if multi
            else nullcontext()
        ),
    ):
        w = Window(
            Store(
                directory
                / ("state-exit-retry.json" if exit_retry else sweep_state if sweep_mode else "state.json")
            )
        )
        if sweep_mode:
            assert not w.store.data.get("operation") and not w.store.data.get("positions"), (
                "Unfinished Sweep audit"
            )
        original_record = w.worker.record_market

        def record(kind, **data):
            # Capture manual/converter diagnostics even before the first recorder starts.
            if kind == "cycle_latency":
                report.setdefault("cycle_traces", []).append(dict(event=kind, **data))
            original_record(kind, **data)

        w.worker.record_market = record
        w.record_market.setChecked(True)
        w.setWindowTitle("DipBot · LIVE audit · $1 position / $1 total cost cap")
        w.mode.setCurrentText("LIVE")
        w.show()
        w.raise_()
        w.activateWindow()
        w.worker.log.connect(logs.append)

        def pump():
            app.processEvents()
            time.sleep(0.01)

        def wait():
            until = time.monotonic() + 180
            while w.busy or w.stop_pending:
                pump()
                if time.monotonic() > until:
                    raise TimeoutError("LIVE UI operation deadline")
            pump()
            assert not report["errors"], report["errors"]

        def click(label):
            b = next(b for b in w.findChildren(QPushButton) if b.text() == label)
            assert b.isEnabled(), label
            b.click()
            wait()

        def select(pool, token=audit_token):
            w.token.setText(token)
            w.pool_input.setText(pool.address)
            w.send("verify", token=token, pool=pool.address)
            wait()
            assert w.selection_ready
            w.params["amount"].setText("0.00003")
            w.params["slippage"].setText("0.5")
            w.params["dynamic"].setText("0")
            w.gas.setText("0.1")

        def capture(name):
            app.processEvents()
            w.tabs.setCurrentIndex(0)
            w.tabs.widget(0).verticalScrollBar().setValue(0)
            w.grab().save(str(directory / (name + ".png")))

        def inspect(kind, payload):
            if kind == "status" and payload["mode"] == "LIVE":
                if w.metrics["position"].text() != f"{float(payload['position']):.8g}":
                    report["ui_mismatches"].append("position")
                if payload["locked"] and w.buy.isEnabled():
                    report["ui_mismatches"].append("locked BUY")
            elif kind == "sweep_report":
                report.setdefault("sweep_reports", []).append(payload)
            elif kind == "trade_marker":
                if (w.display_position > 0) != (payload["side"] == "BUY"):
                    report["ui_mismatches"].append("trade marker precedes position display")

        w.worker.event.connect(inspect)
        try:
            w.rpc.setText("https://bsc-dataseed.binance.org")
            w.save_rpc.setChecked(False)
            click("Подключить")
            if cleanup_only:
                assert not w.store.data.get("positions") and chain.balance(audit_token, account.address) == 0
                click("Проверить receipts")
                operation = w.store.data.get("operation")
                if operation:
                    assert all(t["status"] in {"confirmed", "reverted"} for t in operation["transactions"])
                    known = {r["hash"].removeprefix("0x") for r in report["receipts"]}
                    for t in operation["transactions"]:
                        if t["hash"].removeprefix("0x") not in known:
                            receipt = chain.w3.eth.get_transaction_receipt(t["hash"])
                            chain.canonical_receipt(receipt)
                            report["receipts"].append(
                                {
                                    "label": t["label"],
                                    "hash": t["hash"],
                                    "status": receipt["status"],
                                    "block": receipt["blockNumber"],
                                    "block_hash": receipt["blockHash"].hex(),
                                    "gas_fee_wei": receipt["gasUsed"] * receipt["effectiveGasPrice"],
                                }
                            )
                    assert chain.balance(audit_token, account.address) == 0
                    click("Балансы сверены · снять блокировку")
                assert not w.store.data.get("operation") and not w.locked
                select(v2)
                w.params["slippage"].setText("5")
                if chain.balance(audit_base, account.address):
                    click("SELL ALL BASE → BNB")
                report["remaining_base_raw"] = chain.balance(audit_base, account.address)
                assert report["remaining_base_raw"] == 0 and chain.balance(WBNB, account.address) == 0
                report["cleanup_passed"] = True
                report["passed"] = not report["errors"] and not report["ui_mismatches"]
                report["scenarios"].append(
                    "Receipt reconciliation and base cleanup after failed automatic attempt; no repeated BUY"
                )
                capture("cleanup_completed")
                return
            if resume:
                op = w.store.data["operation"]
                assert (
                    chain.w3.eth.get_transaction_count(account.address, "latest")
                    == op["transactions"][-1]["nonce"] + 1
                )
                click("Проверить receipts")
                assert all(t["status"] == "confirmed" for t in w.store.data["operation"]["transactions"])
                assert (
                    chain.balance(WBNB, account.address) == 10**14
                    and chain.balance(audit_token, account.address) == 0
                )
                for t in w.store.data["operation"]["transactions"]:
                    receipt = chain.w3.eth.get_transaction_receipt(t["hash"])
                    report["receipts"].append(
                        {
                            "label": t["label"],
                            "hash": t["hash"],
                            "status": receipt["status"],
                            "block": receipt["blockNumber"],
                            "gas_fee_wei": receipt["gasUsed"] * receipt["effectiveGasPrice"],
                        }
                    )
                click("Балансы сверены · снять блокировку")
                assert not w.store.data.get("operation") and not w.locked
                report["scenarios"].append(
                    "LIVE recovery: RPC change -> receipt -> balance verification -> unlock; no resend"
                )
            select(v2)
            if automatic_token:
                w.convert_amount.setText("0.0001")
                click("BUY BASE")
                funded_base = chain.balance(audit_base, account.address)
                position_raw = funded_base // 5
                assert position_raw > 0, "No base funding"
                w.params["amount"].setText(str(D(position_raw) / D(10) ** v2.quote_decimals))
                for name, value in (
                    ("dip", "10"),
                    ("take_profit", "15"),
                    ("stop_loss", "15"),
                    ("slippage", "5"),
                    ("dynamic", "120"),
                ):
                    w.params[name].setText(value)
                w.interval.setValue(0.1)
                w.continue_after_exit.setChecked(True)
                w.exit_fields["cooldown_seconds"].setValue(3)
                w.exit_fields["trailing_pct"].setValue(3)
                w.start.click()
                wait()
                started = progress = time.monotonic()
                while time.monotonic() - started < automatic_seconds and w.running:
                    pump()
                    if (directory / "stop.request").exists():
                        report["automatic_stop_requested"] = True
                        break
                    if sum(r["label"] == "SELL" for r in report["receipts"]) >= 2:
                        break
                    if time.monotonic() - progress >= 30:
                        progress = time.monotonic()
                        capture("automatic")
                        print(
                            json.dumps(
                                {
                                    "elapsed": round(progress - started),
                                    "buys": sum(r["label"] == "BUY" for r in report["receipts"]),
                                    "status": w.strategy_status.text(),
                                },
                                ensure_ascii=False,
                            ),
                            flush=True,
                        )
                report["automatic_observed_seconds"] = time.monotonic() - started
                report["automatic_running_before_stop"] = w.running
                capture("automatic_end")
                w.stop.click()
                wait()
                assert chain.balance(audit_token, account.address) == 0
                assert not w.store.data.get("operation") and not w.worker.position()
                report["automatic_closed_trades"] = list(w.store.data.get("closed_trades", {}).values())
                report["automatic_settings"] = {name: field.text() for name, field in w.params.items()}
                report["automatic_exit_policy"] = w.exit_policy()
                report["scenarios"].append(
                    "LIVE bounded automatic market observation; STOP cleanup separately identified in archive"
                )
                click("SELL ALL BASE → BNB")
                report["remaining_base_raw"] = chain.balance(audit_base, account.address)
                assert report["remaining_base_raw"] == 0
                assert chain.balance(WBNB, account.address) == 0
                report["passed"] = not report["errors"] and not report["ui_mismatches"]
                capture("completed")
                return
            if exit_retry:
                # One real round trip. Only a pre-send read failure is injected.
                from requests import Response
                from requests.exceptions import HTTPError

                w.backup_rpc.setText("https://bsc-rpc.publicnode.com")
                click("Подключить")
                select(v2)
                deadline = time.monotonic() + 60
                while w.worker.rates.snapshot(WBNB) is None:
                    pump()
                    if time.monotonic() > deadline:
                        raise TimeoutError("USD rate unavailable before LIVE test")
                w.convert_amount.setText("0.00003")
                click("BUY BASE")
                w.params["amount"].setText("0.00002")
                click("BUY NOW")
                assert chain.balance(audit_token, account.address) > 0
                capture("exit_retry_position")
                fault = []
                retries = []
                original_quote = Chain.quote

                def quote(source, pool, amount, buy, **kwargs):
                    if not buy and source is w.worker.chain and not fault:
                        fault.append("controlled HTTP 503 before quote")
                        response = Response()
                        response.status_code = 503
                        raise HTTPError(response=response)
                    return original_quote(source, pool, amount, buy, **kwargs)

                def inspect_retry(kind, payload):
                    if kind == "exit_retry" and payload:
                        retries.append(dict(payload))
                        capture("exit_retry_wait")

                w.worker.event.connect(inspect_retry)
                with patch.object(Chain, "quote", quote):
                    w.stop.click()
                    wait()
                assert fault and retries and chain.balance(audit_token, account.address) == 0
                assert not w.store.data.get("operation") and not w.worker.position()
                report["exit_retry_detail"] = dict(w.worker.trade_detail or {})
                closed = list(w.store.data.get("closed_trades", {}).values())
                assert len(closed) == 1 and report["exit_retry_detail"].get("net_usd") is not None
                assert D(report["exit_retry_detail"]["net_usd"]) == D(closed[0]["net_usd"])
                report["exit_retry_usd_matches_ledger"] = True
                report["exit_retry_events"] = retries
                report["scenarios"].append(
                    "LIVE V2 BUY -> STOP: injected read-only HTTP 503, backup quote, SELL receipt; no resend"
                )
                capture("exit_retry_sold")
                click("SELL ALL BASE → BNB")
                assert chain.balance(WBNB, account.address) == 0
                report["passed"] = not report["ui_mismatches"]
                return
            if sweep_mode:
                if sweep_route:
                    assert v3 is not None, "Verified V3 route required before funding"
                w.convert_amount.setText("0.00005" if multi else "0.00002")
                click("BUY BASE")
                assert chain.balance(WBNB, account.address) == (50000000000000 if multi else 20000000000000)
                if multi:
                    w.params["amount"].setText("0.00002")
                    click("BUY NOW")
                    assert (
                        chain.balance(audit_token, account.address) > 0
                        and chain.balance(WBNB, account.address) > 0
                    )
                # Restrict the audit catalog, never sell other wallet holdings.
                # Actual Worker Sweep and LiveTrader conversion remain unchanged.
                if sweep_route:
                    original_quote = w.worker.chain.quote

                    def unavailable(pool, amount, buy):
                        if not buy and pool.token.lower() == audit_token.lower():
                            raise TimeoutError("Controlled preflight failure")
                        return original_quote(pool, amount, buy)

                    with patch.object(w.worker.chain, "quote", unavailable):
                        click("SELL WALLET → BNB")
                    partial = report["sweep_reports"][-1]
                    assert audit_token in partial["failed"] and partial["remaining"][audit_token] > 0
                    assert chain.balance(WBNB, account.address) == 0
                    assert w.worker.position() and not w.store.data.get("operation")
                    capture("sweep_route_partial")
                    from dipbot.market.autopair import Candidate
                    from dipbot.market.discovery import Resolution

                    candidate = Candidate(v3, "WBNB", True, 1)
                    with patch.object(
                        w.worker.chain,
                        "resolve_address",
                        return_value=Resolution("RESOLVED", (candidate,), candidate),
                    ):
                        click("SELL WALLET → BNB")
                    target_ops = [
                        op for op in w.store.data["history"] if op["description"].startswith("SWEEP TARGET")
                    ]
                    assert len(target_ops) == 1
                    sells = [tx for tx in target_ops[0]["transactions"] if tx["label"] == "SELL"]
                    assert len(sells) == 1 and sells[0]["request"]["to"].lower() == V3_ROUTER.lower()
                    assert (
                        chain.balance(audit_token, account.address)
                        == chain.balance(WBNB, account.address)
                        == 0
                    )
                    assert not w.worker.position() and not w.store.data.get("operation")
                    report["scenarios"].append(
                        "LIVE partial Sweep: injected TARGET preflight timeout, WBNB unwrapped; controlled verified V2-to-V3 rediscovery, real SELL and unwrap"
                    )
                    report["passed"] = not report["ui_mismatches"]
                    capture("sweep_route_completed")
                    return
                if sweep_stop:
                    original_swap = LiveTrader.swap
                    stopped = []

                    def stop_after_swap(trader, pool, amount, buy, *args, **kwargs):
                        received = original_swap(trader, pool, amount, buy, *args, **kwargs)
                        if not buy and pool.token.lower() == audit_token.lower():
                            # Controlled STOP at a real receipt boundary, not an RPC fault.
                            stopped.append(pool.token)
                            w.worker.stop_event.set()
                        return received

                    with patch.object(LiveTrader, "swap", stop_after_swap):
                        click("SELL WALLET → BNB")
                    assert stopped == [audit_token]
                    assert (
                        chain.balance(audit_token, account.address) == 0
                        and chain.balance(WBNB, account.address) > 0
                    )
                    assert not w.store.data.get("operation") and not w.worker.position()
                    assert report["sweep_reports"][-1]["status"] == "stopped"
                    report["scenarios"].append(
                        "LIVE controlled STOP after confirmed USDT Sweep; WBNB retained, journal clear"
                    )
                    capture("sweep_stopped")
                click("SELL WALLET → BNB")
                assert chain.balance(WBNB, account.address) == 0
                assert chain.balance(audit_token, account.address) == 0
                assert not w.store.data.get("operation")
                report["scenarios"].append(
                    "LIVE resume stopped Sweep: only remaining WBNB unwrapped"
                    if sweep_stop
                    else "LIVE scoped Sweep: tracked USDT target then remaining WBNB base; restricted catalog"
                    if multi
                    else "LIVE scoped Sweep: WBNB unwrap; catalog restricted to WBNB, zero USDT target"
                )
                report["passed"] = not report["ui_mismatches"]
                capture("sweep_completed")
                return
            if not resume:
                w.convert_amount.setText("0.0001")
                click("BUY BASE")
            assert chain.balance(WBNB, account.address) == 10**14
            report["scenarios"].append("LIVE converter BNB -> WBNB")
            click("BUY NOW")
            assert D(w.metrics["position"].toolTip()) > 0
            capture("v2_position")
            report["scenarios"].append("LIVE V2 BUY via UI")
            w.stop.click()
            wait()
            assert chain.balance(audit_token, account.address) == 0
            report["scenarios"].append("LIVE STOP closes V2 position")
            if v3:
                select(v3)
                click("BUY NOW")
                capture("v3_position")
                click("SELL POSITION")
                assert chain.balance(audit_token, account.address) == 0
                report["scenarios"].append("LIVE V3 BUY -> SELL via UI")
            click("SELL ALL BASE → BNB")
            assert chain.balance(WBNB, account.address) == 0
            report["scenarios"].append("LIVE converter WBNB -> BNB")
            # Reverse the same verified pool so USDT is the converter base.
            select(v2, WBNB)
            w.convert_amount.setText("0.00005")
            click("BUY BASE")
            assert chain.balance(audit_token, account.address) > 0
            report["scenarios"].append("LIVE converter BNB -> USDT")
            click("SELL ALL BASE → BNB")
            assert (
                chain.balance(audit_token, account.address) == 0 and chain.balance(WBNB, account.address) == 0
            )
            report["scenarios"].append("LIVE converter USDT -> BNB")
            # A real no-operation receipt check is safe; faults are exercised offline.
            click("Проверить receipts")
            report["scenarios"].append("LIVE reconcile with no pending operation")
            capture("completed")
            report["passed"] = not report["ui_mismatches"]
        except Exception as exc:
            # Do not automatically clear latches, retry, sell, or Sweep after an error.
            report["failure_type"] = type(exc).__name__
            report["passed"] = False
        finally:
            w.worker.quit_event.set()
            w.worker.wait()
            observer.shutdown(wait=True)
            for row, future, cycle_id, offset in observations:
                finality = future.result()
                if finality["observed"] and cycle_id is not None:
                    finality["cycle_id"] = cycle_id
                    finality["cycle_start_to_finalized_observed_ms"] = (
                        offset + finality["post_send_return_to_finalized_ms"]
                    )
                row["finality"] = finality
            report["status"] = "completed" if report.get("passed") else "failed"
            report["journal_locked"] = bool(w.store.data.get("operation"))
            report["remaining_wbnb_raw"] = chain.balance(WBNB, account.address)
            report["remaining_target_raw"] = chain.balance(audit_token, account.address)
            report["remaining_usdt_raw"] = chain.balance(USDT, account.address)
            after = chain.w3.eth.get_balance(account.address)
            report["native_balance_delta_wei"] = before - after
            merge_settled_receipts(report, w.store.data)
            report["gas_fee_wei"] = sum(x["gas_fee_wei"] for x in report["receipts"])
            report["logs"] = logs
            save()
            print(
                json.dumps(
                    {k: v for k, v in report.items() if k not in {"logs", "receipts"}}, ensure_ascii=False
                ),
                flush=True,
            )
            w.hide()
            w.deleteLater()
            app.processEvents()


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--key-file", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--execute", action="store_true")
    p.add_argument(
        "--automatic-token", help="Explicitly selected token; requires separate prior sellability review"
    )
    p.add_argument("--automatic-seconds", type=int, default=600)
    p.add_argument(
        "--cleanup-only",
        action="store_true",
        help="Reconcile existing automatic audit and redeem only its base; never BUY",
    )
    p.add_argument("--automatic-pool", help="Canonical V2 pool for the explicit automatic token")
    p.add_argument(
        "--inject-lost-ack",
        action="store_true",
        help="Initial wrap only; leave durable latch for a separate --resume process",
    )
    p.add_argument(
        "--resume",
        action="store_true",
        help="Only recover the single initial wrap, preserving the same budget",
    )
    p.add_argument(
        "--sweep-only", action="store_true", help="Continue existing budget with a WBNB-only Sweep audit"
    )
    p.add_argument(
        "--sweep-multi",
        action="store_true",
        help="Continue existing budget: USDT target and WBNB base Sweep only",
    )
    p.add_argument(
        "--exit-retry",
        action="store_true",
        help="Continue existing budget: one small LIVE BUY/STOP with controlled quote failure",
    )
    p.add_argument(
        "--sweep-stop",
        action="store_true",
        help="Continue existing budget: controlled STOP after target receipt, then resume remaining base",
    )
    p.add_argument(
        "--sweep-route",
        action="store_true",
        help="Continue budget: partial preflight failure then verified V3 reroute",
    )
    a = p.parse_args()
    if (
        sum(
            [
                a.resume,
                a.sweep_only,
                a.sweep_multi,
                a.sweep_stop,
                a.exit_retry,
                a.sweep_route,
                a.inject_lost_ack,
            ]
        )
        > 1
    ):
        p.error("Choose one continuation mode")
    if not a.execute:
        p.error("Explicit --execute and user authorization required")
    run(
        a.key_file,
        a.output,
        a.resume,
        a.sweep_only,
        a.sweep_multi,
        a.sweep_stop,
        a.exit_retry,
        a.sweep_route,
        a.inject_lost_ack,
        automatic_token=a.automatic_token,
        automatic_seconds=a.automatic_seconds,
        automatic_pool=a.automatic_pool,
        cleanup_only=a.cleanup_only,
    )
