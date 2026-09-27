"""Summarize observed stages and individual transactions, including failed cycles."""

import argparse
import json
import math
from collections import defaultdict, deque
from pathlib import Path


def summarize(paths):
    samples = defaultdict(lambda: deque(maxlen=10000))
    counts = defaultdict(int)
    failures = defaultdict(int)
    outcomes = defaultdict(int)
    unavailable = defaultdict(int)
    incomplete = []
    cycles = 0

    def add(key, value):
        if type(value) in (int, float) and math.isfinite(value) and value >= 0:
            samples[key].append(value)
            counts[key] += 1

    for path in paths:
        end = None
        with Path(path).open() as stream:
            for line in stream:
                row = json.loads(line)
                if row.get("event") == "end":
                    end = row
                if row.get("event") != "cycle_latency":
                    continue
                cycles += 1
                mode = "FORK" if row.get("environment") == "FORK" else row["mode"]
                failed = bool(row.get("error_type"))
                outcomes[mode + (".failed" if failed else ".successful")] += 1
                if failed:
                    failures[mode] += 1
                if row.get("truncated"):
                    unavailable[mode + ".truncated"] += 1
                    continue
                stages = row.get("stages", [])
                if not stages:
                    continue
                signal = next((s["ms"] for s in stages if s["stage"] == "signal"), 0)
                action = {
                    "BUY": "BUY",
                    "TAKE_PROFIT": "SELL",
                    "STOP_LOSS": "SELL",
                    "TRAILING_STOP": "SELL",
                    "TIME_EXIT": "SELL",
                    "CONTROLLED_BUY": "CONTROLLED_BUY",
                    "CONTROLLED_SELL": "CONTROLLED_SELL",
                }.get(row.get("action"))
                scopes = [mode, mode + "." + action] if action else [mode]
                # Legacy successful-cycle fields remain signal-relative.
                if not failed and stages[-1]["stage"] == "completed":
                    metrics = {"signal_to_application_complete_ms": stages[-1]["ms"] - signal}
                    for stage in stages:
                        if stage["stage"] not in {"signal", "completed"}:
                            metrics["signal_to_last_" + stage["stage"] + "_ms"] = stage["ms"] - signal
                    for before, after in zip(stages, stages[1:]):
                        metrics["phase_" + before["stage"] + "_to_" + after["stage"] + "_ms"] = (
                            after["ms"] - before["ms"]
                        )
                    if row.get("block_to_signal_ms") is not None:
                        metrics["approx_block_timestamp_to_signal_ms"] = row["block_to_signal_ms"]
                    for scope in scopes:
                        for name, value in metrics.items():
                            add(scope + "." + name, value)
                # Group by transaction, never conflate approve and target swap.
                txs = defaultdict(dict)
                for stage in stages:
                    if stage.get("transaction") is not None:
                        txs[stage["transaction"]][stage["stage"]] = stage
                for tx in txs.values():
                    kind = next(iter(tx.values())).get("kind", "OTHER")
                    scope = mode + ".tx." + kind + (".failed_cycle" if failed else ".successful_cycle")
                    for before, after, label in [
                        ("transaction_started", "signed", "prepare_to_signed_ms"),
                        ("journal_started", "intent_persisted", "durable_save_ms"),
                        ("signed", "broadcast_started", "signed_to_send_ms"),
                        ("broadcast_started", "broadcast_ack", "send_to_ack_ms"),
                        ("broadcast_ack", "receipt_observed", "ack_to_receipt_observed_ms"),
                    ]:
                        if before in tx and after in tx:
                            add(scope + "." + label, tx[after]["ms"] - tx[before]["ms"])
                        else:
                            unavailable[scope + "." + label] += 1
                    for name in ("broadcast_started", "broadcast_ack", "receipt_observed"):
                        if name in tx:
                            add(scope + ".signal_to_" + name + "_ms", tx[name]["ms"] - signal)
                            if row.get("origin") == "observation_start":
                                add(scope + ".observation_start_to_" + name + "_ms", tx[name]["ms"])
                # Counts are provider calls (SDK retries disabled by Chain), not cache hits.
                for scope in scopes:
                    outcome = scope + (".failed_cycle" if failed else ".successful_cycle")
                    if "rpc" in row:
                        add(outcome + ".rpc_calls", len(row["rpc"]))
                    for rpc in row.get("rpc", []):
                        add(
                            outcome + ".rpc." + rpc["method"] + (".error_ms" if rpc["failed"] else ".ok_ms"),
                            rpc["duration_ms"],
                        )
                    if row.get("rpc_dropped"):
                        unavailable[outcome + ".rpc_dropped"] += row["rpc_dropped"]
                unavailable[mode + ".finality_not_observed"] += 1
        if not end or end.get("dropped"):
            incomplete.append(Path(path).name)
    result = {}
    for key, values in samples.items():
        values = sorted(values)
        result[key] = {
            "count": counts[key],
            "window": len(values),
            "mean": sum(values) / len(values),
            "max": max(values),
            "p99_preliminary": len(values) < 1000,
            **{
                label: values[max(0, math.ceil(len(values) * q) - 1)]
                for label, q in [("p50", 0.5), ("p95", 0.95), ("p99", 0.99)]
            },
        }
    return {
        "cycles": cycles,
        "failed_cycles": dict(failures),
        "outcomes": dict(outcomes),
        "metrics": result,
        "unavailable": dict(unavailable),
        "incomplete_recordings": incomplete,
        "limits": "Observation start is not raw event reception. Receipt is local observation, not exact inclusion or finality. PAPER has no signature/broadcast. Samples are bounded to the latest 10000; percentiles describe that window. Legacy mode aggregates mix routes; compare matched cohorts only.",
    }


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("recordings", type=Path, nargs="+")
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    report = summarize(a.recordings)
    a.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report))
