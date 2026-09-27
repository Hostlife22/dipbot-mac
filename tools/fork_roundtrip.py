"""Local Anvil fork execution through a strictly read-only upstream proxy."""

import argparse
import json
import platform
import re
import socket
import subprocess
import tempfile
import threading
import time
from collections import Counter
from decimal import Decimal as D
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import requests
from eth_account import Account

from dipbot.domain.assets import USDT, WBNB
from dipbot.execution.trader import LiveTrader
from dipbot.market.chain import Chain, address
from dipbot.observability.cycle_trace import signal_cycle
from dipbot.persistence.storage import Store

READ_METHODS = frozenset(
    {
        "eth_chainId",
        "eth_gasPrice",
        "net_version",
        "eth_blockNumber",
        "eth_getBlockByNumber",
        "eth_getBlockByHash",
        "eth_getBalance",
        "eth_getTransactionCount",
        "eth_getStorageAt",
        "eth_getCode",
        "eth_getProof",
        "eth_getTransactionByHash",
        "eth_getTransactionReceipt",
        "eth_getLogs",
        "eth_call",
    }
)


def validate_read_request(payload):
    rows = payload if isinstance(payload, list) else [payload]
    if (
        not rows
        or len(rows) > 100
        or any(not isinstance(row, dict) or row.get("method") not in READ_METHODS for row in rows)
    ):
        raise ValueError("Upstream write or unknown RPC method forbidden")
    return rows


def run(
    anvil,
    endpoint,
    token,
    pool_address,
    amount,
    output,
    sweep_audit=False,
    fork_block=None,
    paired_performance=False,
    cohort_audit=False,
):
    if sweep_audit and paired_performance:
        raise ValueError("Run Sweep and performance as separate cohorts")
    # Upstream is constructed only as a Chain for validation/read calls.
    remote = Chain(endpoint, request_timeout=8)
    head = remote.check()
    block = head if fork_block is None else fork_block
    if not 0 < block <= head:
        raise ValueError("Invalid fork block")
    remote_pool = remote.verify_pool(pool_address, token)
    if remote_pool.quote.lower() != WBNB.lower():
        raise ValueError("Локальный тест пока требует базу WBNB")
    if not 0 < amount <= 10**16:
        raise ValueError("Размер локальной симуляции должен быть <=0.01 WBNB")
    counts = Counter()
    proxy_errors = Counter()

    class Proxy(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            try:
                size = int(self.headers.get("Content-Length", 0))
                if not 0 < size <= 100000:
                    raise ValueError("request size")
                payload = json.loads(self.rfile.read(size))
                try:
                    rows = validate_read_request(payload)
                except ValueError:
                    # Anvil probes optional RPC extensions. A protocol-level
                    # method-not-found permits its normal fallback to basic reads.
                    rows = payload if isinstance(payload, list) else [payload]
                    denied = [
                        {
                            "jsonrpc": "2.0",
                            "id": row.get("id"),
                            "error": {
                                "code": -32601,
                                "message": "Method not allowed by read-only fork proxy",
                            },
                        }
                        for row in rows
                        if isinstance(row, dict)
                    ]
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps(denied if isinstance(payload, list) else denied[0]).encode())
                    return
                if all(row["method"] == "eth_getTransactionReceipt" for row in rows):
                    # Unknown local pending hashes can make Anvil consult the
                    # upstream. The fork does not require historical receipts;
                    # report absent there and let local mining complete.
                    result = [{"jsonrpc": "2.0", "id": row.get("id"), "result": None} for row in rows]
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps(result if isinstance(payload, list) else result[0]).encode())
                    return
                for row in rows:
                    counts[row["method"]] += 1
                response = requests.post(endpoint, json=payload, timeout=12)
                response.raise_for_status()
                result = response.content
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(result)
            except Exception as exc:
                method = (
                    payload.get("method", "batch") if isinstance(locals().get("payload"), dict) else "unknown"
                )
                status = getattr(getattr(exc, "response", None), "status_code", None)
                proxy_errors[f"{method}:{type(exc).__name__}:{status}"] += 1
                self.send_response(502)
                self.end_headers()

    proxy = ThreadingHTTPServer(("127.0.0.1", 0), Proxy)
    thread = threading.Thread(target=proxy.serve_forever, daemon=True)
    thread.start()
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    local = f"http://127.0.0.1:{port}"
    process = None
    report = {
        "commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "python": platform.python_version(),
        "fork_block_hash": remote.w3.eth.get_block(block)["hash"].hex(),
        "cycle_traces": [],
        "environment": "local Anvil fork",
        "fork_block": block,
        "pool": remote_pool.address,
        "token": remote_pool.token,
        "input_wei": amount,
        "mainnet_transactions_sent": 0,
        "passed": False,
        "limits": "Local EVM test, not a sellability guarantee; time/owner-dependent taxes, MEV and BSC consensus are not reproduced.",
    }
    try:
        process = subprocess.Popen(
            [
                str(anvil),
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--accounts",
                "0",
                "--fork-url",
                f"http://127.0.0.1:{proxy.server_port}",
                "--fork-block-number",
                str(block),
                "--chain-id",
                "56",
                "--fork-chain-id",
                "56",
                "--no-storage-caching",
                "--base-fee",
                "0",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        until = time.monotonic() + 45
        while True:
            if process.poll() is not None:
                raise RuntimeError("Anvil exited during startup")
            try:
                info = requests.post(
                    local,
                    json={"jsonrpc": "2.0", "id": 1, "method": "web3_clientVersion", "params": []},
                    timeout=1,
                ).json()
                if "anvil" in info.get("result", "").lower():
                    break
            except requests.RequestException:
                pass
            if time.monotonic() > until:
                raise TimeoutError("Anvil startup timeout")
            time.sleep(0.1)
        report["phase"] = "local_chain"
        chain = Chain(local, request_timeout=15)
        account = Account.create()  # New random local-only account, never read from the user's wallet.
        response = chain.w3.provider.make_request("anvil_setBalance", [account.address, hex(10**18)])
        if "error" in response:
            raise RuntimeError("Local balance setup failed")
        # Fork setup may spend time fetching state. Advance only this local clock.
        current = chain.w3.eth.get_block("latest")["timestamp"]
        chain.w3.provider.make_request("evm_setNextBlockTimestamp", [max(int(time.time()) + 1, current + 1)])
        chain.w3.provider.make_request("evm_mine", [])
        report["phase"] = "receipt_probe"
        # A local receipt proves this local node supports receipts; fork history
        # need not expose receipts for transactions preceding the fork boundary.
        signed = account.sign_transaction(
            {
                "chainId": 56,
                "nonce": 0,
                "to": account.address,
                "value": 0,
                "gas": 21000,
                "gasPrice": 100000000,
            }
        )
        bootstrap = chain.w3.eth.send_raw_transaction(signed.raw_transaction)
        chain.w3.eth.wait_for_transaction_receipt(bootstrap, timeout=15)

        def local_receipt_access():
            receipt = chain.w3.eth.get_transaction_receipt(bootstrap)
            LiveTrader.validate_receipt(receipt, bootstrap.hex())
            chain.canonical_receipt(receipt)

        chain.check_receipt_access = local_receipt_access
        original_check = chain.check

        def local_check(**kwargs):
            latest = chain.w3.eth.get_block("latest")
            if time.time() - latest["timestamp"] > 2:
                chain.w3.provider.make_request("evm_setNextBlockTimestamp", [int(time.time()) + 1])
                chain.w3.provider.make_request("evm_mine", [])
            return original_check(**kwargs)

        chain.check = local_check
        report["phase"] = "trader_init"
        with tempfile.TemporaryDirectory(prefix="dipbot-fork-") as directory:
            store = Store(Path(directory) / "state.json")
            trader = LiveTrader(chain, account.key, store, D(".1"), lambda _: None)
            pool = chain.verify_pool(pool_address, token)
            trace_worker = SimpleNamespace(
                mode="LIVE",
                live=trader,
                record_market=lambda event, **kw: report["cycle_traces"].append(
                    {"event": event, "environment": "FORK", **kw}
                ),
            )
            report["phase"] = "wrap"
            trader.begin("LOCAL WRAP")
            trader.wrap(amount)
            trader.finish()
            quoted_buy = chain.quote(pool, amount, True)
            report["phase"] = "buy"
            trader.begin("BUY local fork")
            with signal_cycle(trace_worker, "BUY", None):
                received = trader.swap(pool, amount, True, D(3), simulate=True)
            trader.finish()
            quoted_sell = chain.quote(pool, received, False)
            report["phase"] = "sell"
            trader.begin("SELL local fork")
            with signal_cycle(trace_worker, "STOP_LOSS", None):
                returned = trader.swap(pool, received, False, D(3), simulate=True)
            trader.finish()
            if paired_performance:
                report["phase"] = "paired_performance"
                report["paired_receipt_mode"] = (
                    "explicit local evm_mine before receipt read; not BSC inclusion timing"
                )
                report["paired_identity_cycles"] = paired_identity(chain, account, pool, amount, directory)
                report["benchmark_local_submissions"] = sum(
                    r["local_submissions"] for r in report["paired_identity_cycles"]
                )
            if cohort_audit:
                report["phase"] = "cohort_audit"
                report["cohorts"] = []
                audit_cohorts(chain, account, pool, amount, directory, report["cohorts"])
                report["benchmark_local_submissions"] = report.get("benchmark_local_submissions", 0) + sum(
                    r["local_submissions"] for r in report["cohorts"]
                )
            if sweep_audit:
                report["phase"] = "sweep_audit"
                from tools.fork_sweep_audit import audit

                report["sweep_scenarios"] = []
                audit(chain, account, directory, pool, amount, report["sweep_scenarios"])
            receipts = [r for op in store.data["history"] for r in op["transactions"]]
            report.update(
                passed=True,
                quoted_buy=str(quoted_buy),
                received=str(received),
                quoted_sell=str(quoted_sell),
                returned_wei=str(returned),
                roundtrip_loss_pct=str((D(amount) - D(returned)) * 100 / D(amount)),
                asset_flows=[flow for op in store.data["history"] for flow in op.get("asset_flows", [])],
                local_transactions=len(receipts) + 1 + report.get("benchmark_local_submissions", 0),
                bootstrap_local_transactions=1,
                local_gas_wei=str(sum(r.get("gas_fee_wei", 0) for r in receipts)),
            )
            if sweep_audit:
                report["roundtrip_local_transactions"] = report.pop("local_transactions")
    except Exception as exc:
        report["failure_type"] = type(exc).__name__
        from dipbot.application.errors import safe_error

        report["failure_detail"] = safe_error(exc)
        response = getattr(exc, "rpc_response", None)
        if isinstance(response, dict) and isinstance(response.get("error"), dict):
            report["rpc_error_code"] = response["error"].get("code")
            text = str(response["error"].get("message", ""))
            text = re.sub(r"https?://[^\s]+", "<endpoint>", text)
            text = re.sub(r"0x[0-9a-fA-F]{40,}", "<hex>", text)
            report["local_rpc_message"] = text[:300]
    finally:
        if process is not None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        proxy.shutdown()
        proxy.server_close()
        thread.join(timeout=1)
        report["upstream_methods"] = dict(counts)
        report["upstream_errors"] = dict(proxy_errors)
        output.write_text(json.dumps(report, indent=2) + "\n")
    return report


def paired_identity(chain, account, pool, amount, directory):
    """Warm same-fork snapshot A/B; all writes stay behind the local Anvil guard."""
    rows = []
    original_wait = chain.w3.eth.wait_for_transaction_receipt

    def mined_receipt(*args, **kwargs):
        # Only this benchmark controls local inclusion. Keep ACK measured before
        # mining and make dependent approve receipt timing comparable across A/B.
        response = chain.w3.provider.make_request("evm_mine", [])
        if "error" in response:
            raise RuntimeError("Local deterministic mining failed")
        return original_wait(*args, **kwargs)

    with patch.object(chain.w3.eth, "wait_for_transaction_receipt", side_effect=mined_receipt):
        for repeat in range(3):
            for enabled in [True, False] if repeat % 2 else [False, True]:
                snapshot = chain.w3.provider.make_request("evm_snapshot", [])["result"]
                traces = []
                try:
                    chain.identity_multicall_enabled = enabled
                    # Read immutable metadata caches are shared; mutable state is reset.
                    store = Store(Path(directory) / f"paired-{repeat}-{enabled}.json")
                    trader = LiveTrader(chain, account.key, store, D(".1"), lambda _: None)
                    worker = SimpleNamespace(
                        mode="LIVE",
                        live=trader,
                        record_market=lambda event, **kw: traces.append(
                            dict(event=event, environment="FORK", **kw)
                        ),
                    )
                    trader.begin("LOCAL WRAP")
                    trader.wrap(amount)
                    trader.finish()
                    trader.begin("BUY local fork")
                    with signal_cycle(worker, "BUY", None):
                        bought = trader.swap(pool, amount, True, D(3), simulate=True)
                    trader.finish()
                    trader.begin("SELL local fork")
                    with signal_cycle(worker, "STOP_LOSS", None):
                        sold = trader.swap(pool, bought, False, D(3), simulate=True)
                    trader.finish()
                    rows.append(
                        {
                            "repeat": repeat,
                            "multicall": enabled,
                            "traces": traces,
                            "received": str(bought),
                            "returned": str(sold),
                            "local_submissions": sum(len(op["transactions"]) for op in store.data["history"]),
                        }
                    )
                finally:
                    result = chain.w3.provider.make_request("evm_revert", [snapshot])
                    if result.get("result") is not True:
                        raise RuntimeError("Fork snapshot restoration failed")
                    chain.identity_multicall_enabled = True
    if len({(r["received"], r["returned"]) for r in rows}) != 1:
        raise AssertionError("Paired fork financial outputs differ")
    return rows


def audit_cohorts(chain, account, pool, amount, directory, rows=None):
    """Fixed fork, exact approval variants; setup and converter separately identified."""
    rows = [] if rows is None else rows
    for repeat in range(3):
        for ready in (False, True):
            for cold in (False, True):
                snapshot = chain.w3.provider.make_request("evm_snapshot", [])["result"]
                traces = []
                try:
                    store = Store(Path(directory) / f"cohort-{repeat}-{ready}-{cold}.json")
                    trader = LiveTrader(chain, account.key, store, D(".1"), lambda _: None)
                    worker = SimpleNamespace(
                        mode="LIVE",
                        live=trader,
                        record_market=lambda event, **kw: traces.append(
                            dict(event=event, environment="FORK", **kw)
                        ),
                    )
                    trader.begin("LOCAL WRAP")
                    trader.wrap(amount)
                    trader.finish()
                    bought = sold = 0
                    for buy in (True, False):
                        size = amount if buy else bought
                        if ready:
                            router, _ = trader.verify_router(pool)
                            trader.begin("LOCAL PREAPPROVE")
                            trader.approve(pool.quote if buy else pool.token, router, size)
                            trader.finish()
                        if cold:
                            chain._contract_factories.clear()
                        with signal_cycle(worker, "BUY" if buy else "STOP_LOSS", None):
                            trader.begin("BUY cohort" if buy else "SELL cohort")
                            output = trader.swap(pool, size, buy, D(3), simulate=True)
                            trader.finish()
                        if buy:
                            bought = output
                        else:
                            sold = output
                    approvals = sum(
                        s.get("kind") == "APPROVE" and s["stage"] == "broadcast_ack"
                        for t in traces
                        for s in t["stages"]
                    )
                    if approvals != (0 if ready else 2):
                        raise AssertionError("Unexpected measured approval cohort")
                    rows.append(
                        {
                            "repeat": repeat,
                            "router": pool.router,
                            "allowance": "ready" if ready else "needed",
                            "abi_cache": "cleared" if cold else "warm",
                            "traces": traces,
                            "received": str(bought),
                            "returned": str(sold),
                            "local_submissions": sum(len(op["transactions"]) for op in store.data["history"]),
                        }
                    )
                finally:
                    if chain.w3.provider.make_request("evm_revert", [snapshot]).get("result") is not True:
                        raise RuntimeError("Cohort restore failed")
    if len({(r["received"], r["returned"]) for r in rows}) != 1:
        raise AssertionError("Cohort financial outputs differ")
    snapshot = chain.w3.provider.make_request("evm_snapshot", [])["result"]
    traces = []
    try:
        store = Store(Path(directory) / "converter-cohort.json")
        trader = LiveTrader(chain, account.key, store, D(".1"), lambda _: None)
        worker = SimpleNamespace(
            mode="LIVE",
            live=trader,
            record_market=lambda event, **kw: traces.append(dict(event=event, environment="FORK", **kw)),
        )
        converted = 0
        for buy in (True, False):
            with signal_cycle(worker, "CONVERT_BUY" if buy else "CONVERT_SELL", None):
                trader.begin("Converter cohort")
                output = trader.convert(USDT, amount if buy else converted, buy, D(3))
                trader.finish()
            if buy:
                converted = output
        rows.append(
            {
                "operation": "converter",
                "route": "selected by production conversion_route",
                "traces": traces,
                "received": str(converted),
                "returned": str(output),
                "local_submissions": sum(len(op["transactions"]) for op in store.data["history"]),
            }
        )
    finally:
        if chain.w3.provider.make_request("evm_revert", [snapshot]).get("result") is not True:
            raise RuntimeError("Converter restore failed")
    return rows


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--anvil", type=Path, required=True)
    p.add_argument("--rpc", default="https://bsc-rpc.publicnode.com")
    p.add_argument("--token", required=True)
    p.add_argument("--pool", required=True)
    p.add_argument("--amount-wei", type=int, default=10**14)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--sweep-audit", action="store_true")
    p.add_argument("--block", type=int)
    p.add_argument("--paired-performance", action="store_true")
    p.add_argument("--cohort-audit", action="store_true")
    a = p.parse_args()
    report = run(
        a.anvil,
        a.rpc,
        address(a.token),
        address(a.pool),
        a.amount_wei,
        a.output,
        sweep_audit=a.sweep_audit,
        fork_block=a.block,
        paired_performance=a.paired_performance,
        cohort_audit=a.cohort_audit,
    )
    print(json.dumps(report))
    raise SystemExit(0 if report["passed"] else 1)


if __name__ == "__main__":
    main()
