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
