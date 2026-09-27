from types import SimpleNamespace as NS

import pytest

from dipbot.execution.errors import UncertainTransaction
from dipbot.observability.cycle_trace import CycleTrace, signal_cycle
from tests.support.execution import Function


def test_signal_trace_correlates_signature_durable_intent_and_receipt(trader):
    events = []
    worker = NS(mode="LIVE", live=trader, record_market=lambda event, **kw: events.append((event, kw)))
    with signal_cycle(worker, "BUY", {"number": 42}):
        trader.begin("test")
        trader.send(Function(), "test")
        trader.finish()
    row = events[0][1]
    assert [r["stage"] for r in row["stages"]] == [
        "signal",
        "transaction_started",
        "nonce_ready",
        "gas_estimated",
        "transaction_built",
        "signed",
        "journal_started",
        "intent_persisted",
        "broadcast_started",
        "broadcast_ack",
        "receipt_observed",
        "receipt_validated",
        "completed",
    ]
    assert [r["ms"] for r in row["stages"]] == sorted(r["ms"] for r in row["stages"])
    assert row["signal_block"] == 42 and row["block_to_signal_ms"] is None
    assert row["error_type"] is None and trader.cycle_trace is None
    assert trader.owner not in str(events)


def test_uncertain_send_keeps_failure_and_does_not_invent_receipt(trader):
    events = []
    trader.chain.w3.eth.fail_send = True
    worker = NS(mode="LIVE", live=trader, record_market=lambda event, **kw: events.append(kw))
    with pytest.raises(UncertainTransaction):
        with signal_cycle(worker, "BUY", None):
            trader.begin("test")
            trader.send(Function(), "test")
    row = events[0]
    assert row["error_type"] == "UncertainTransaction"
    assert row["stages"][-1]["stage"] == "failed"
    assert "receipt_validated" not in [s["stage"] for s in row["stages"]]
    assert "private RPC" not in str(row)
    assert trader.store.data["operation"]


def test_diagnostic_failure_does_not_change_execution_exception():
    def fail(*a, **kw):
        raise OSError("diagnostic failure")

    worker = NS(mode="PAPER", live=None, record_market=fail)
    with pytest.raises(ValueError, match="original"):
        with signal_cycle(worker, "BUY", None):
            raise ValueError("original")
    assert worker.cycle_trace is None
    trace = CycleTrace("BUY", "PAPER")
    for _ in range(1000):
        trace.mark("quote")
    assert len(trace.stages) == 64 and trace.truncated


def test_report_separates_paper_and_live_and_marks_incomplete(tmp_path):
    import json

    from tools.cycle_latency_report import summarize

    path = tmp_path / "synthetic.jsonl"
    rows = [
        {
            "event": "cycle_latency",
            "mode": "PAPER",
            "stages": [{"stage": "quote", "ms": 20}, {"stage": "completed", "ms": 30}],
            "block_to_signal_ms": 100,
        },
        {
            "event": "cycle_latency",
            "mode": "LIVE",
            "stages": [
                {"stage": "signed", "ms": 20},
                {"stage": "receipt_validated", "ms": 300},
                {"stage": "completed", "ms": 350},
            ],
        },
        {"event": "cycle_latency", "mode": "LIVE", "error_type": "TimeoutError"},
        {"event": "end", "dropped": 1},
    ]
    path.write_text("\n".join(json.dumps(row) for row in rows))
    report = summarize([path])
    assert report["cycles"] == 3 and report["failed_cycles"] == {"LIVE": 1}
    assert report["metrics"]["LIVE.signal_to_last_receipt_validated_ms"]["p50"] == 300
    assert not any("PAPER" in key and "receipt" in key for key in report["metrics"])
    assert report["incomplete_recordings"] == [path.name]


def test_failed_after_ack_keeps_individual_transaction_metrics(tmp_path):
    import json

    from tools.cycle_latency_report import summarize

    stages = [{"stage": "signal", "ms": 5}]
    for transaction, kind, offset in [(1, "APPROVE", 10), (2, "BUY", 50)]:
        for stage, elapsed in [
            ("transaction_started", 0),
            ("signed", 1),
            ("journal_started", 2),
            ("intent_persisted", 4),
            ("broadcast_started", 5),
            ("broadcast_ack", 8),
        ]:
            stages.append({"stage": stage, "ms": offset + elapsed, "kind": kind, "transaction": transaction})
    stages.append({"stage": "failed", "ms": 100})
    path = tmp_path / "events.jsonl"
    path.write_text(
        json.dumps(
            {
                "event": "cycle_latency",
                "mode": "LIVE",
                "origin": "observation_start",
                "observation": {"raw_market_received": 2},
                "action": "BUY",
                "stages": stages,
                "error_type": "TimeoutError",
            }
        )
        + "\n"
        + json.dumps({"event": "end", "dropped": 0})
        + "\n"
    )
    result = summarize([path])
    for kind, offset in [("APPROVE", 10), ("BUY", 50)]:
        prefix = "LIVE.tx." + kind + ".failed_cycle."
        assert result["metrics"][prefix + "send_to_ack_ms"]["p50"] == 3
        assert result["metrics"][prefix + "observation_start_to_broadcast_ack_ms"]["p50"] == offset + 8
        assert result["metrics"][prefix + "raw_market_to_broadcast_ack_ms"]["p50"] == offset + 6
        assert result["unavailable"][prefix + "ack_to_receipt_observed_ms"] == 1
    assert not any("application_complete" in key for key in result["metrics"])


def test_observation_origin_rpc_bounds_and_context_reset():
    import time

    from dipbot.observability.cycle_trace import observation_mark, observation_timing, rpc_span

    events = []
    worker = NS(mode="PAPER", live=None, record_market=lambda event, **kw: events.append(kw))

    @observation_timing
    def observe():
        observation_mark("http_started")
        rpc_span("eth_call", time.perf_counter_ns(), failed=False)
        observation_mark("price_ready")
        observation_mark("strategy_completed")
        with signal_cycle(worker, "BUY", None):
            for _ in range(300):
                rpc_span("eth_call", time.perf_counter_ns(), failed=True)

    observe()
    row = events[0]
    assert row["origin"] == "observation_start"
    assert row["observation"]["http_started"] <= row["observation"]["price_ready"] <= row["stages"][0]["ms"]
    assert len(row["rpc"]) == 256 and row["rpc_dropped"] == 45
    with signal_cycle(worker, "BUY", None):
        pass
    assert events[1]["rpc"] == [] and events[1]["origin"] == "signal"


@pytest.mark.parametrize("same_hash,same_number", [(True, True), (False, True), (True, False)])
def test_head_reception_is_correlated_only_with_identical_block(monkeypatch, same_hash, same_number):
    from dipbot.observability.cycle_trace import head_context, observation_timing

    monkeypatch.setattr("dipbot.observability.cycle_trace.time.perf_counter_ns", lambda: 2_000_000)
    rows = []
    block_hash = "0x" + "11" * 32

    @observation_timing
    def observe():
        rows.append(
            CycleTrace(
                "BUY",
                "PAPER",
                {
                    "number": 9 if same_number else 10,
                    "hash": bytes.fromhex(("11" if same_hash else "22") * 32),
                },
            ).data
        )

    with head_context(9, block_hash, 1_000_000):
        observe()
    observe()
    if same_hash and same_number:
        assert rows[0]["observation"]["head_received"] == -1
        assert "head_received" not in rows[0]["unavailable"]
    else:
        assert "head_received" not in rows[0]["observation"]
        assert "head_received" in rows[0]["unavailable"]
    assert "head" not in rows[1]


def test_head_report_preserves_time_spent_before_http(tmp_path):
    import json

    from tools.cycle_latency_report import summarize

    path = tmp_path / "head.jsonl"
    path.write_text(
        json.dumps(
            {
                "event": "cycle_latency",
                "mode": "LIVE",
                "action": "BUY",
                "observation": {"head_received": -30, "price_ready": 200},
                "stages": [
                    {"stage": "signal", "ms": 201},
                    {"stage": "broadcast_ack", "ms": 300, "transaction": 1, "kind": "BUY"},
                    {"stage": "completed", "ms": 400},
                ],
            }
        )
        + "\n"
        + json.dumps({"event": "end", "dropped": 0})
    )
    result = summarize([path])
    assert result["metrics"]["LIVE.BUY.successful_cycle.head_to_price_ms"]["p50"] == 230
    assert result["metrics"]["LIVE.tx.BUY.successful_cycle.head_to_broadcast_ack_ms"]["p50"] == 330


def test_converter_and_wrap_are_not_reported_as_target_swaps():
    from dipbot.observability.cycle_trace import mark

    trace = CycleTrace("CONVERT_BUY", "LIVE")
    subject = NS(cycle_trace=trace)
    for label in ["CONVERTER BUY", "CONVERTER SELL", "BNB → WBNB", "WBNB → BNB"]:
        mark(subject, "transaction_started", label=label)
        mark(subject, "broadcast_ack", label=label)
    assert [s["kind"] for s in trace.stages if s["stage"] == "broadcast_ack"] == [
        "CONVERT_BUY",
        "CONVERT_SELL",
        "WRAP",
        "UNWRAP",
    ]


def test_sweep_trace_retains_partial_outcome(tmp_path):
    from tests.support.sweep import multi_worker

    worker, _, _, _, _ = multi_worker(tmp_path)
    rows = []
    worker.record_market = lambda event, **kw: rows.append(dict(event=event, **kw))
    worker.stop_event.set()
    worker.sweep()
    assert rows[-1]["action"] == "SWEEP"
    assert rows[-1]["operation_outcome"]["status"] == "stopped"
    assert rows[-1]["operation_outcome"]["unknown"] > 0


def test_durable_identifiers_link_recovery_after_lost_ack(trader):
    from dipbot.execution.trader import LiveTrader
    from dipbot.persistence.storage import Store

    rows = []
    worker = NS(mode="LIVE", live=trader, record_market=lambda event, **kw: rows.append(kw))
    trader.chain.w3.eth.fail_send = True
    with pytest.raises(UncertainTransaction):
        with signal_cycle(worker, "BUY", None):
            trader.begin("BUY")
            trader.send(Function(), "BUY")
    persisted = Store(trader.store.path)
    op = persisted.data["operation"]
    record = op["transactions"][0]
    signed = next(s for s in rows[0]["stages"] if s["stage"] == "signed")
    assert signed["operation_id"] == op["operation_id"]
    assert signed["transaction_id"] == record["transaction_id"]
    assert record["signal_cycle_id"] == rows[0]["cycle_id"]
    assert len(op["operation_id"]) == len(record["transaction_id"]) == 32
    recovered = object.__new__(LiveTrader)
    recovered.store, recovered.owner, recovered.chain = persisted, trader.owner, trader.chain
    recovered.chain.w3.eth.send_raw_transaction = lambda *a: pytest.fail("must not resend")
    recovered.chain.w3.eth.get_transaction_receipt = lambda h: {
        "status": 1,
        "blockNumber": 123,
        "transactionHash": h,
    }
    worker.live = recovered
    with signal_cycle(worker, "RECONCILE", None):
        recovered.reconcile()
    assert rows[1]["cycle_id"] != rows[0]["cycle_id"]
    assert rows[1]["recovery"][-1] == {
        "phase": "receipt_persisted",
        "operation_id": op["operation_id"],
        "transaction_id": record["transaction_id"],
        "signal_cycle_id": rows[0]["cycle_id"],
    }
    stored = Store(trader.store.path).data["operation"]
    assert stored["operation_id"] == op["operation_id"] and stored["transactions"][0]["status"] == "confirmed"
    with pytest.raises(UncertainTransaction):
        recovered.begin("blocked until balance review")
    assert trader.owner not in str(rows)


def test_legacy_recovery_ids_remain_unknown():
    from dipbot.observability.cycle_trace import recovery_mark

    rows = []
    worker = NS(mode="LIVE", live=None, record_market=lambda event, **kw: rows.append(kw))
    with signal_cycle(worker, "RECONCILE", None):
        recovery_mark({"wallet": "private"}, {"hash": "private"}, "review_started")
    assert rows[0]["recovery"] == [
        {"phase": "review_started", "operation_id": None, "transaction_id": None, "signal_cycle_id": None}
    ]
    assert "private" not in str(rows)
