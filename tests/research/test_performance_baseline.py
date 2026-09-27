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
