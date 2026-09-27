from types import SimpleNamespace

import pytest

from tools.revert_probe import inspect_revert


@pytest.mark.parametrize("scenario", ["trace", "unavailable", "reorg", "success"])
def test_revert_probe_only_reads_and_distinguishes_unavailable(monkeypatch, scenario):
    calls = []

    def post(url, *, json, timeout):
        method = json["method"]
        calls.append(method)
        if method == "eth_getTransactionReceipt":
            result = {
                "status": "0x1" if scenario == "success" else "0x0",
                "blockNumber": "0x2",
                "blockHash": "0xabc",
            }
        elif method == "eth_getBlockByNumber":
            result = {"hash": "0xdef" if scenario == "reorg" else "0xabc"}
        elif method == "debug_traceTransaction":
            if scenario == "unavailable":
                return SimpleNamespace(
                    raise_for_status=lambda: None,
                    json=lambda: {
                        "error": {"code": -1, "message": "missing trie node https://private/SECRET"}
                    },
                )
            result = {
                "error": "execution reverted",
                "revertReason": "INSUFFICIENT_OUTPUT_AMOUNT",
                "output": "0x",
                "to": "0xrouter",
            }
        else:
            raise AssertionError("Unexpected RPC")
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"result": result})

    class Session:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    Session.post = staticmethod(post)
    monkeypatch.setattr("tools.revert_probe.requests.Session", Session)
    r = inspect_revert("https://private/SECRET", "0x" + "1" * 64)
    assert r["transactions_sent"] == 0 and "SECRET" not in str(r)
    if scenario == "trace":
        assert r["failed_calls"][0]["revert_reason"] == "INSUFFICIENT_OUTPUT_AMOUNT"
    elif scenario == "unavailable":
        assert r["trace_error"]["category"] == "missing trie node"
    else:
        assert "debug_traceTransaction" not in calls
