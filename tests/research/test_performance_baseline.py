from tools.performance_baseline import run
from tools.storage_benchmark import benchmark


def test_offline_baseline_has_matching_decisions_and_no_network():
    result = run(count=5, repeats=1)
    assert result["transactions_sent"] == result["network_requests"] == 0
    assert len(result["decision_digest"]) == 64
    assert result["repeats"][0]["abi_encode"]["n"] == 5
    assert result["repeats"][0]["abi_encode"]["p99_preliminary"]


def test_storage_benchmark_uses_current_production_financial_schema():
    result = benchmark(sizes=(3,), repeats=2)
    assert result["sizes"][0]["closed_trades"] == 3
    assert result["sizes"][0]["gas_receipts"] == 6
    assert result["includes_fsync"]


def test_finality_probe_requires_canonical_hash_and_does_not_submit(monkeypatch):
    from types import SimpleNamespace as NS

    from tools import rpc_latency_probe as probe

    calls = []
    target = {"number": 42, "hash": bytes.fromhex("11" * 32)}

    def block(tag):
        calls.append(tag)
        return target

    chain = NS(
        w3=NS(eth=NS(get_block=block), provider=NS()), canonical_receipt=lambda receipt: calls.append(receipt)
    )
    monkeypatch.setattr(probe, "Chain", lambda *a, **kw: chain)
    monkeypatch.setattr(probe, "guard_provider", lambda provider: None)
    result = probe.finality_probe(0.1)
    assert result["observed"] and result["transactions_sent"] == 0
    assert calls == ["latest", "finalized", {"blockNumber": 42, "blockHash": target["hash"]}]

    def reorg(receipt):
        raise ValueError("changed canonical hash")

    chain.canonical_receipt = reorg
    result = probe.finality_probe(0.01)
    assert not result["observed"] and result["errors"] == ["ValueError"]
    assert "block_received_to_finality_observed_ms" not in result


def test_market_snapshot_keeps_quote_block_and_rejects_reorg(tmp_path, monkeypatch):
    import json
    from types import SimpleNamespace as NS

    from dipbot.domain.assets import USDT, WBNB
    from tools import read_only_probe as probe

    clock = [0.0]
    monkeypatch.setattr(probe.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(probe.time, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds))
    monkeypatch.setattr(probe, "guard_provider", lambda _: {})
    pool = NS(address=USDT, token=USDT, quote=WBNB, quote_decimals=18, router="V2")
    calls = []

    def quote(pool, amount, buy, *, block):
        calls.append(block)
        return amount * 2 if buy else amount // 2

    chain = NS(
        w3=NS(provider=None),
        verify_pool=lambda *a: pool,
        price=lambda p: probe.D(1),
        price_block={"number": 42, "hash": bytes.fromhex("11" * 32)},
        price_state={"reserve0": "123"},
        quote=quote,
        canonical_receipt=lambda r: None,
    )
    monkeypatch.setattr(probe, "Chain", lambda *a, **kw: chain)
    monkeypatch.setattr("dipbot.market.activity.read_swaps", lambda *a: [])
    monkeypatch.setattr(
        probe.requests,
        "get",
        lambda *a, **kw: NS(
            raise_for_status=lambda: None,
            json=lambda: [
                {
                    "chainId": "bsc",
                    "baseToken": {"address": WBNB},
                    "priceUsd": "500",
                    "liquidity": {"usd": 1000},
                },
            ],
        ),
    )
    path = tmp_path / "snapshots.jsonl"
    result = probe.market_snapshots("public", USDT, USDT, 1, probe.D(20), path)
    rows = [json.loads(s) for s in path.read_text().splitlines()]
    assert result == {"snapshots": 1, "errors": 0, "activity_errors": 0}
    assert calls == [42, 42] and rows[1]["canonical"]
    assert rows[1]["amount_in_raw"] == str(4 * 10**16)

    def reorg(receipt):
        raise ValueError("changed hash")

    chain.canonical_receipt = reorg
    path = tmp_path / "reorg.jsonl"
    result = probe.market_snapshots("public", USDT, USDT, 1, probe.D(20), path)
    rows = [json.loads(s) for s in path.read_text().splitlines()]
    assert result == {"snapshots": 0, "errors": 1, "activity_errors": 0}
    assert rows[1]["canonical"] is False and rows[1]["error_type"] == "ValueError"


def test_connection_probe_reuses_only_warm_client_and_closes_sessions(monkeypatch):
    import requests

    from tools.rpc_latency_probe import connection_probe

    sessions, providers, calls = [], [], []

    class Session:
        closed = False

        def __init__(self):
            sessions.append(self)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.close()

        def close(self):
            self.closed = True

    class Provider:
        def __init__(self, *args, **kwargs):
            providers.append(self)
            assert kwargs["exception_retry_configuration"] is None

        def make_request(self, method, params):
            assert method == "eth_getBlockByNumber" and params == ["latest", False]
            calls.append(self)
            return {"result": {"number": "0x1", "hash": "0x" + "11" * 32, "timestamp": "0x1"}}

    monkeypatch.setattr(requests, "Session", Session)
    monkeypatch.setattr("dipbot.market.rpc.BscHTTPProvider", Provider)
    monkeypatch.setattr("tools.rpc_latency_probe.time.sleep", lambda _: None)
    report = connection_probe("unused", samples=2, repeats=1)
    assert report["passed"] and len(report["rows"]) == 4
    assert len(providers) == len(sessions) == 3 and all(s.closed for s in sessions)
    assert calls.count(providers[0]) == 3


def test_rpc_endpoint_selection_does_not_expose_credentials(monkeypatch):
    from tools.rpc_latency_probe import ENDPOINTS, endpoint_selection

    assert endpoint_selection([]) == ENDPOINTS
    monkeypatch.setenv("AUDIT_TEST_RPC", "https://example.test/private-key-in-path")
    assert endpoint_selection(["AUDIT_TEST_RPC"]) == ["https://example.test/private-key-in-path"]
    monkeypatch.setenv("AUDIT_TEST_RPC", "http://user:secret@example.test/private-key-in-path")
    try:
        endpoint_selection(["AUDIT_TEST_RPC"])
    except ValueError as error:
        assert "AUDIT_TEST_RPC" in str(error) and "secret" not in str(error)
        assert "private-key-in-path" not in str(error)
    else:
        raise AssertionError("Unsafe endpoint accepted")
    monkeypatch.delenv("AUDIT_TEST_RPC")
    try:
        endpoint_selection(["AUDIT_TEST_RPC"])
    except ValueError:
        pass
    else:
        raise AssertionError("Missing endpoint accepted")
