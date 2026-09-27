import copy

import pytest

from tools.cycle_latency_report import paired_fork_summary


def report():
    rows = []
    for repeat in range(3):
        for enabled in (False, True):
            traces = []
            for side in ("BUY", "SELL"):
                traces.append(
                    {
                        "action": "BUY" if side == "BUY" else "STOP_LOSS",
                        "stages": [
                            {"stage": "signal", "ms": 10},
                            {"stage": "broadcast_ack", "kind": "APPROVE", "ms": 20},
                            {"stage": "broadcast_ack", "kind": side, "ms": 100 - 10 * enabled},
                            {"stage": "receipt_observed", "kind": side, "ms": 120 - 10 * enabled},
                        ],
                        "rpc": [None] * (4 - enabled),
                    }
                )
            rows.append(
                {
                    "repeat": repeat,
                    "multicall": enabled,
                    "identity_scope": "router",
                    "received": "123",
                    "returned": "99",
                    "traces": traces,
                }
            )
    return {"passed": True, "mainnet_transactions_sent": 0, "paired_identity_cycles": rows}


def test_paired_summary_separates_swap_from_approval_and_marks_limits():
    result = paired_fork_summary(report())
    metric = result["metrics"]["BUY.signal_to_broadcast_ack_ms"]
    assert metric["direct"]["p50"] == 90
    assert metric["multicall"]["p50"] == 80
    assert metric["paired_mean_saving_bootstrap95"] == [10, 10]
    assert metric["multicall"]["p99_preliminary"]
    assert result["pairs"] == 3 and result["environment"] == "FORK"


@pytest.mark.parametrize("fault", ["missing", "duplicate", "amount", "truncated", "failure", "stage"])
def test_paired_summary_rejects_incomparable_or_incomplete_data(fault):
    data = report()
    rows = data["paired_identity_cycles"]
    if fault == "missing":
        rows.pop()
    elif fault == "duplicate":
        rows.append(copy.deepcopy(rows[0]))
    elif fault == "amount":
        rows[0]["received"] = "1"
    elif fault == "truncated":
        rows[0]["traces"][0]["truncated"] = True
    elif fault == "failure":
        rows[0]["traces"][0]["error_type"] = "TimeoutError"
    elif fault == "stage":
        rows[0]["traces"][0]["stages"].pop()
    with pytest.raises(ValueError):
        paired_fork_summary(data)
