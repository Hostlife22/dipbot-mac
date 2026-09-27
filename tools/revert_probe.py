"""Read-only receipt and transaction trace; never submit or read a wallet key."""

import argparse
import json
import re
from pathlib import Path
from urllib.parse import urlsplit

import requests

from tools.rpc_latency_probe import endpoint_selection


def inspect_revert(endpoint, tx_hash):
    if not re.fullmatch(r"0x[0-9a-fA-F]{64}", tx_hash):
        raise ValueError("Expected transaction hash")
    result = {"host": urlsplit(endpoint).hostname, "hash": tx_hash, "transactions_sent": 0}
    with requests.Session() as session:

        def read(method, params):
            try:
                response = session.post(
                    endpoint, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params}, timeout=20
                )
                response.raise_for_status()
                body = response.json()
                if "error" in body:
                    error = body["error"]
                    # RPC errors may echo credential URLs or raw requests; retain only category.
                    message = str(error.get("message", "")).lower()
                    category = next(
                        (
                            name
                            for name in (
                                "missing trie node",
                                "method not found",
                                "not available",
                                "rate limit",
                                "unauthorized",
                            )
                            if name in message
                        ),
                        "rpc_error",
                    )
                    return None, {"code": error.get("code"), "category": category}
                if "result" not in body:
                    return None, {"category": "missing_result"}
                return body["result"], None
            except (requests.RequestException, ValueError) as exc:
                return None, {"category": type(exc).__name__}

        receipt, error = read("eth_getTransactionReceipt", [tx_hash])
        if error or not receipt:
            result["receipt_error"] = error or {"category": "not_found"}
            return result
        result["status"] = int(receipt["status"], 16)
        result["block"] = int(receipt["blockNumber"], 16)
        result["block_hash"] = receipt["blockHash"]
        block, error = read("eth_getBlockByNumber", [receipt["blockNumber"], False])
        result["canonical"] = bool(block and block.get("hash") == receipt["blockHash"])
        if error or not result["canonical"]:
            result["canonical_error"] = error or {"category": "hash_mismatch"}
            return result
        if result["status"] != 0:
            result["trace_skipped"] = "Transaction did not revert"
            return result
        trace, error = read("debug_traceTransaction", [tx_hash, {"tracer": "callTracer", "timeout": "10s"}])
        if error or not isinstance(trace, dict):
            result["trace_error"] = error or {"category": "no_call_trace"}
            return result
        failures = []

        def visit(call, depth=0):
            if depth > 64 or len(failures) >= 100:
                return
            if call.get("error"):
                # Never retain calldata, raw transaction or credentials in provider text.
                reason = str(call.get("revertReason", ""))
                reason = re.sub(r"https?://\S+", "<endpoint>", reason)
                failures.append(
                    {
                        "depth": depth,
                        "to": call.get("to"),
                        "revert_reason": reason[:300],
                        "output": str(call.get("output", ""))[:1024],
                    }
                )
            for child in call.get("calls", []):
                visit(child, depth + 1)

        visit(trace)
        result["failed_calls"] = failures
        result["trace_observed"] = True
        result["limits"] = (
            "Nested reverts may be caught; a failed child alone does not prove the top-level cause."
        )
        return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--hash", required=True)
    p.add_argument("--endpoint-env", action="append", default=[])
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    rows = [inspect_revert(endpoint, a.hash) for endpoint in endpoint_selection(a.endpoint_env)]
    a.output.write_text(json.dumps({"results": rows, "transactions_sent": 0}, indent=2) + "\n")
    print(json.dumps(rows))
    return 0 if any(r.get("trace_observed") for r in rows) else 2


if __name__ == "__main__":
    raise SystemExit(main())
