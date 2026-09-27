"""Summarize observed stages and individual transactions, including failed cycles."""

import argparse
import json
import math
import random
import statistics
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
                operation = row.get("operation_outcome") or {}
                failed = bool(row.get("error_type")) or bool(
                    operation
                    and (
                        operation.get("status") != "completed"
                        or operation.get("failed")
                        or operation.get("unknown")
                        or operation.get("needs_reconciliation")
                    )
                )
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
                    "SWEEP": "SWEEP",
                    "CONVERT_BUY": "CONVERT_BUY",
                    "CONVERT_SELL": "CONVERT_SELL",
                    "TAKE_PROFIT": "SELL",
                    "STOP_LOSS": "SELL",
                    "TRAILING_STOP": "SELL",
                    "TIME_EXIT": "SELL",
                    "MANUAL_BUY": "MANUAL_BUY",
                    "MANUAL_SELL": "MANUAL_SELL",
                    "STOP": "STOP",
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
                observation = row.get("observation", {})
                for scope in scopes:
                    outcome = scope + (".failed_cycle" if failed else ".successful_cycle")
                    for before, after, label in [
                        ("head_received", "price_ready", "head_to_price_ms"),
                        ("http_started", "raw_market_received", "http_to_raw_market_ms"),
                        ("raw_market_received", "price_ready", "raw_market_to_price_ms"),
                        ("price_ready", "strategy_completed", "price_to_strategy_ms"),
                    ]:
                        if before in observation and after in observation:
                            add(outcome + "." + label, observation[after] - observation[before])
                        else:
                            unavailable[outcome + "." + label] += 1
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
                            if "head_received" in observation:
                                add(
                                    scope + ".head_to_" + name + "_ms",
                                    tx[name]["ms"] - observation["head_received"],
                                )
                            else:
                                unavailable[scope + ".head_to_" + name + "_ms"] += 1
                            if "raw_market_received" in observation:
                                add(
                                    scope + ".raw_market_to_" + name + "_ms",
                                    tx[name]["ms"] - observation["raw_market_received"],
                                )
                            else:
                                unavailable[scope + ".raw_market_to_" + name + "_ms"] += 1
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


def paired_fork_summary(report):
    """Compare matched snapshots; never claim event delivery or BSC inclusion."""
    if not report.get("passed") or report.get("mainnet_transactions_sent") != 0:
        raise ValueError("Expected a successful isolated fork report")
    pairs = defaultdict(dict)
    scope = set()
    for row in report.get("paired_identity_cycles", []):
        enabled = row.get("multicall")
        repeat = row.get("repeat")
        if type(enabled) is not bool or type(repeat) is not int or enabled in pairs[repeat]:
            raise ValueError("Duplicate or invalid paired observation")
        pairs[repeat][enabled] = row
        scope.add(row.get("identity_scope"))
    if not pairs or len(scope) != 1:
        raise ValueError("Missing pairs or mixed optimizations")
    metrics = defaultdict(lambda: {False: [], True: []})
    for variants in pairs.values():
        if set(variants) != {False, True}:
            raise ValueError("Incomplete pair")
        if any(variants[False][k] != variants[True][k] for k in ("received", "returned")):
            raise ValueError("Financial outputs differ")
        extracted = {}
        for enabled, row in variants.items():
            values = {}
            for trace in row["traces"]:
                if trace.get("error_type") or trace.get("truncated") or trace.get("rpc_dropped"):
                    raise ValueError("Failed or truncated trace")
                action = trace["action"]
                if action not in {"BUY", "STOP_LOSS"}:
                    raise ValueError("Unexpected paired action")
                side = "BUY" if action == "BUY" else "SELL"
                signal = next(x["ms"] for x in trace["stages"] if x["stage"] == "signal")
                for stage in trace["stages"]:
                    if stage.get("kind") == side and stage["stage"] in {"broadcast_ack", "receipt_observed"}:
                        key = side + ".signal_to_" + stage["stage"] + "_ms"
                        value = stage["ms"] - signal
                        if key in values or not math.isfinite(value) or value < 0:
                            raise ValueError("Invalid or duplicated target swap timing")
                        values[key] = value
                values[side + ".rpc_calls"] = len(trace["rpc"])
            expected = {
                side + suffix
                for side in ("BUY", "SELL")
                for suffix in (".signal_to_broadcast_ack_ms", ".signal_to_receipt_observed_ms", ".rpc_calls")
            }
            if set(values) != expected:
                raise ValueError("Missing BUY/SELL stages")
            extracted[enabled] = values
        for key in extracted[False]:
            for enabled in (False, True):
                metrics[key][enabled].append(extracted[enabled][key])

    def distribution(values):
        ordered = sorted(values)
        return {
            "count": len(values),
            "mean": statistics.mean(values),
            "max": max(values),
            **{
                name: ordered[math.ceil(len(values) * q) - 1]
                for name, q in (("p50", 0.5), ("p95", 0.95), ("p99", 0.99))
            },
            "p99_preliminary": True,
        }

    results = {}
    for key, variants in metrics.items():
        delta = [before - after for before, after in zip(variants[False], variants[True])]
        rng = random.Random(0)
        means = sorted(statistics.mean(rng.choices(delta, k=len(delta))) for _ in range(2000))
        results[key] = {
            "direct": distribution(variants[False]),
            "multicall": distribution(variants[True]),
            "paired_mean_saving": statistics.mean(delta),
            "paired_mean_saving_bootstrap95": [means[49], means[1949]] if len(delta) >= 2 else None,
            "positive_saving_pairs": sum(x > 0 for x in delta),
        }
    return {
        "environment": "FORK",
        "pairs": len(pairs),
        "identity_scope": next(iter(scope)),
        "fork_block": report.get("fork_block"),
        "fork_block_hash": report.get("fork_block_hash"),
        "metrics": results,
        "mainnet_transactions_sent": 0,
        "limits": "Same-snapshot local execution, not natural events or BSC consensus. Receipt observation is not exact inclusion. Repeats are correlated; bootstrap intervals are descriptive, not production confidence. No stable p99 claim.",
    }


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("recordings", type=Path, nargs="*")
    p.add_argument("--paired-fork", type=Path)
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    if bool(a.recordings) == bool(a.paired_fork):
        p.error("Provide recordings or --paired-fork, exclusively")
    report = (
        paired_fork_summary(json.loads(a.paired_fork.read_text()))
        if a.paired_fork
        else summarize(a.recordings)
    )
    a.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report))
