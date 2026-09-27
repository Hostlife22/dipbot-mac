"""Offline CPU/instrumentation baseline. Never connects, signs or sends transactions."""

import argparse
import cProfile
import hashlib
import json
import math
import platform
import statistics
import subprocess
import time
from importlib.metadata import version
from pathlib import Path
from types import SimpleNamespace

from dipbot.domain.assets import WBNB
from dipbot.domain.strategy import D, Settings, Strategy
from dipbot.market.chain import POOL_ABI, Chain
from dipbot.observability.cycle_trace import observation_mark, observation_timing, signal_cycle


def distribution(values):
    ordered = sorted(values)
    return {
        "n": len(values),
        "mean_ms": statistics.mean(values),
        "max_ms": max(values),
        **{
            label: ordered[max(0, math.ceil(len(values) * q) - 1)]
            for label, q in [("p50_ms", 0.5), ("p95_ms", 0.95), ("p99_ms", 0.99)]
        },
        "p99_preliminary": len(values) < 1000,
    }


def measure(function, count):
    samples = []
    for _ in range(count):
        started = time.perf_counter_ns()
        function()
        samples.append((time.perf_counter_ns() - started) / 1e6)
    return distribution(samples)


def run(count=1000, repeats=3, profile=None, uncached=False):
    chain = Chain("http://127.0.0.1:1")  # Contract construction/encoding only, no requests.
    chain.w3.provider.make_request = lambda *a, **kw: (_ for _ in ()).throw(
        AssertionError("Network forbidden")
    )
    chain.contract_cache_enabled = not uncached
    events = []
    worker = SimpleNamespace(mode="PAPER", live=None, record_market=lambda event, **kw: None)

    def decision():
        strategy = Strategy(Settings())
        return [strategy.observe(D(p), 1 + i / 10) for i, p in enumerate(("1", ".89", ".9", "1.1"))]

    @observation_timing
    def traced():
        observation_mark("http_started")
        observation_mark("price_ready")
        result = decision()
        observation_mark("strategy_completed")
        with signal_cycle(worker, "BUY", None):
            pass
        return result

    def abi():
        return chain.contract(WBNB, POOL_ABI).functions.getReserves()._encode_transaction_data()

    expected = decision()
    assert traced() == expected
    for repeat in range(repeats):
        # Alternate order to expose thermal/warmup effects.
        functions = [("decision", decision), ("decision_with_trace", traced), ("abi_encode", abi)]
        if repeat % 2:
            functions.reverse()
        events.append({name: measure(function, count) for name, function in functions})
    if profile:
        profiler = cProfile.Profile()
        profiler.enable()
        for _ in range(count):
            abi()
        profiler.disable()
        profiler.dump_stats(str(profile))
    return {
        "kind": "offline_cpu_not_broadcast",
        "contract_factory_cache": not uncached,
        "commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "dirty": bool(subprocess.check_output(["git", "status", "--porcelain"], text=True)),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "web3": version("web3"),
        "samples": count,
        "repeats": events,
        "decision_digest": hashlib.sha256(json.dumps(expected).encode()).hexdigest(),
        "network_requests": 0,
        "transactions_sent": 0,
        "limits": "Synthetic deterministic strategy and ABI workloads, not end-to-end mainnet latency. Trace overhead includes UUID, bounded capture and no-op archive enqueue.",
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=int, default=1000)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--profile", type=Path)
    parser.add_argument("--uncached", action="store_true")
    args = parser.parse_args()
    if args.samples < 1 or args.repeats < 1:
        parser.error("positive samples/repeats required")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(run(args.samples, args.repeats, args.profile, args.uncached), indent=2) + "\n"
    )
