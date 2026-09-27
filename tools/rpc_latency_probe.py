"""Read-only RPC timing; record method timings without params or credentials."""

import argparse
import hashlib
import json
import math
import platform
import statistics
import subprocess
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path
from urllib.parse import urlsplit

from dipbot.checks.read_only import guard_provider
from dipbot.domain.assets import USDT
from dipbot.market.chain import Chain

ENDPOINTS = [
    "https://bsc-dataseed.binance.org",
    "https://bsc-rpc.publicnode.com",
    "https://bsc-dataseed-public.bnbchain.org",
]
POOLS = {
    "V2": "0x16b9a82891338f9bA80E2D6970FddA79D1eb0daE",
    "V3": "0x172fcD41E0913e95784454622d1c3724f546f849",
}


def summary(values):
    return (
        {
            "count": len(values),
            "median_ms": round(statistics.median(values) * 1000, 2),
            "mean_ms": round(statistics.mean(values) * 1000, 2),
            "p95_ms": round(sorted(values)[math.ceil(len(values) * 0.95) - 1] * 1000, 2),
            "p99_ms": round(sorted(values)[math.ceil(len(values) * 0.99) - 1] * 1000, 2),
            "p99_preliminary": len(values) < 1000,
            "max_ms": round(max(values) * 1000, 2),
        }
        if values
        else {}
    )


def probe(endpoint, samples, max_seconds=300, interval=0.1, uncached=False):
    result = {"host": urlsplit(endpoint).hostname, "samples_per_router": samples}
    chain = Chain(endpoint)
    chain.contract_cache_enabled = not uncached
    provider = chain.w3.provider
    guard_provider(provider)
    original = provider._make_request
    timings = defaultdict(list)
    observations = []
    prices = defaultdict(list)
    errors = []

    def measured(method, *args, **kwargs):
        started = time.monotonic()
        try:
            return original(method, *args, **kwargs)
        finally:
            timings[method].append(time.monotonic() - started)

    provider._make_request = measured
    try:
        pools = {
            router: chain.verify_pool(addr, USDT, require_liquidity=False) for router, addr in POOLS.items()
        }
        timings.clear()
        prices = defaultdict(list)
        observations = []
        deadline = time.monotonic() + max_seconds
        for _ in range(samples):
            if time.monotonic() >= deadline:
                break
            for router, pool in pools.items():
                started = time.monotonic()
                try:
                    assert chain.price(pool) > 0
                except Exception as exc:
                    response = getattr(exc, "rpc_response", None)
                    code = response.get("error", {}).get("code") if isinstance(response, dict) else None
                    errors.append(
                        {
                            "router": router,
                            "rpc_error_code": code if type(code) is int else None,
                            "error_type": type(exc).__name__,
                            "ms": (time.monotonic() - started) * 1000,
                        }
                    )
                    continue
                elapsed = time.monotonic() - started
                cached = bool(getattr(chain, "price_cache_hit", False))
                prices[router + (".cached" if cached else ".fresh")].append(elapsed)
                header = chain.price_block
                observations.append(
                    {
                        "router": router,
                        "block": header["number"],
                        "hash": bytes(header["hash"]).hex(),
                        "cache_hit": cached,
                        "ms": elapsed * 1000,
                        "block_age_s": time.time() - header["timestamp"],
                    }
                )
            time.sleep(interval)
        result["observations"] = observations
        result["independent_blocks"] = {
            router: len({r["hash"] for r in observations if r["router"] == router and not r["cache_hit"]})
            for router in POOLS
        }
        result["price"] = {k: summary(v) for k, v in prices.items()}
        result["wire_requests"] = {k: summary(v) for k, v in timings.items()}
        block = chain.w3.eth.get_block("latest")
        result.update(
            block_number=block["number"],
            block_age_seconds=round(time.time() - block["timestamp"], 3),
            passed=True,
        )
    except Exception as exc:
        result.update(passed=False, error_type=type(exc).__name__)
    result["observations"] = observations
    result["errors"] = errors
    result["price"] = {k: summary(v) for k, v in prices.items()}
    result["wire_requests"] = {k: summary(v) for k, v in timings.items()}
    result["contract_factory_cache"] = not uncached
    if errors:
        result["passed"] = False
    return result


def identity_probe(endpoint, samples=5, repeats=3):
    """Alternating direct/Multicall identity reads with identical canonical guards."""
    chain = Chain(endpoint)
    guard_provider(chain.w3.provider)
    wire = []
    original = chain.w3.provider._make_request

    def measured(method, *args, **kwargs):
        wire.append(method)
        return original(method, *args, **kwargs)

    chain.w3.provider._make_request = measured
    rows = []
    for router, addr in POOLS.items():
        chain.verify_pool(addr, USDT)  # warm metadata/ABI, excluded explicitly
        for repeat in range(repeats):
            for index in range(samples):
                modes = [False, True] if (repeat + index) % 2 else [True, False]
                expected = None
                for enabled in modes:
                    chain.identity_multicall_enabled = enabled
                    wire.clear()
                    started = time.perf_counter()
                    error = None
                    try:
                        pool = chain.verify_pool(addr, USDT)
                        if expected is not None and pool != expected:
                            raise AssertionError("pool identity mismatch")
                        expected = pool
                    except Exception as exc:
                        error = type(exc).__name__
                    rows.append(
                        {
                            "router": router,
                            "repeat": repeat,
                            "multicall": enabled,
                            "ms": (time.perf_counter() - started) * 1000,
                            "wire_calls": len(wire),
                            "error_type": error,
                        }
                    )
                time.sleep(0.1)
    return {
        "rows": rows,
        "samples_per_mode_router": samples * repeats,
        "transactions_sent": 0,
        "limits": "Warm identity check, alternating wall-clock windows. Same guards but not identical head. Not signal-to-broadcast. Small-N tails preliminary.",
    }


def head_delivery(seconds=60):
    """Shadow comparison on equal hashes; never drive strategy from these hints."""
    from dipbot.market.head_feed import HeadFeed

    chain = Chain("https://bsc-rpc.publicnode.com", request_timeout=2)
    guard_provider(chain.w3.provider)
    feed = HeadFeed("wss://bsc-rpc.publicnode.com").start()
    rows, errors = [], []
    seen = set()
    deadline = time.monotonic() + seconds
    try:
        while time.monotonic() < deadline:
            started = time.monotonic()
            try:
                chain.check(force_network=False)
                finished = time.monotonic()
                header = chain.checked_header
                head = feed.snapshot()
                block_hash = "0x" + bytes(header["hash"]).hex()
                if head and head.hash == block_hash and block_hash not in seen:
                    seen.add(block_hash)
                    rows.append(
                        {
                            "block": head.number,
                            "hash": block_hash,
                            "wss_before_http_response_ms": (finished - head.received_at) * 1000,
                            "http_request_ms": (finished - started) * 1000,
                        }
                    )
            except Exception as exc:
                errors.append(type(exc).__name__)
            time.sleep(0.1)
    finally:
        feed.stop()
    return {
        "matched_hashes": rows,
        "errors": errors,
        "reconnects": feed.reconnects,
        "feed_error": feed.error_type,
        "mainnet_transactions_sent": 0,
        "limits": "Only equal observed hashes, selection bias and HTTP polling included. Not absolute propagation time or proof of faster trading. No decisions executed.",
    }


def shadow_processing(seconds=120):
    """Compare production scheduling concurrently, with independent read-only Chains."""
    from dipbot.market.head_feed import HeadFeed, HeadSchedule
    from dipbot.observability.cycle_trace import (
        CycleTrace,
        head_context,
        observation_mark,
        observation_timing,
    )

    def run_mode(use_heads):
        chain = Chain(ENDPOINTS[1], request_timeout=2)
        calls = guard_provider(chain.w3.provider)
        rows, errors = [], []
        feed = HeadFeed("wss://bsc-rpc.publicnode.com").start() if use_heads else None
        schedule = HeadSchedule()
        try:
            pool = chain.verify_pool(POOLS["V2"], USDT)
            deadline = time.monotonic() + seconds
            next_poll = 0

            @observation_timing
            def observe():
                observation_mark("http_started")
                price = chain.price(pool)
                observation_mark("price_ready")
                trace = CycleTrace("READ", "READ_ONLY", chain.price_block)
                rows.append(
                    {
                        "block": chain.price_block["number"],
                        "hash": bytes(chain.price_block["hash"]).hex(),
                        "price": str(price),
                        "cache_hit": chain.price_cache_hit,
                        "observation": trace.data["observation"],
                        "head": trace.data.get("head"),
                        "unavailable": trace.data["unavailable"],
                    }
                )

            while time.monotonic() < deadline:
                now = time.monotonic()
                head = feed.snapshot() if feed else None
                due = schedule.due(head, now, next_poll) if feed else now >= next_poll
                if not due:
                    time.sleep(0.01)
                    continue
                schedule.consume(head, now)
                try:
                    with head_context(
                        head.number if head else None,
                        head.hash if head else None,
                        head.received_ns if head else None,
                    ):
                        observe()
                except Exception as exc:
                    errors.append({"type": type(exc).__name__, "t": time.monotonic()})
                next_poll = max(now + 0.1, time.monotonic())
        except Exception as exc:
            errors.append({"type": type(exc).__name__})
        finally:
            if feed:
                feed.stop()
        matched = [r for r in rows if "head_received" in r["observation"]]
        return {
            "rows": rows,
            "errors": errors,
            "calls": dict(calls),
            "independent_blocks": len({r["hash"] for r in rows}),
            "head_to_price": summary(
                [
                    (r["observation"]["price_ready"] - r["observation"]["head_received"]) / 1000
                    for r in matched
                ]
            ),
            "head_unmatched": len(rows) - len(matched),
            "reconnects": feed.reconnects if feed else 0,
        }

    with ThreadPoolExecutor(max_workers=2) as executor:
        polling, heads = list(executor.map(run_mode, [False, True]))
    left = {r["hash"]: r["price"] for r in polling["rows"]}
    right = {r["hash"]: r["price"] for r in heads["rows"]}
    shared = left.keys() & right.keys()
    return {
        "polling": polling,
        "heads": heads,
        "common_blocks": len(shared),
        "price_mismatches": sum(left[h] != right[h] for h in shared),
        "transactions_sent": 0,
        "limits": "Concurrent read-only V2 spot observations, no strategy/signing. Head correlation requires equal block/hash; unmatched reads remain unknown. Different cadence can change strategy observations; this does not justify changing trading defaults.",
    }


def finality_probe(seconds=30):
    """Observe one public block reaching provider finalized tag; never a tx guarantee."""
    chain = Chain(ENDPOINTS[1], request_timeout=2)
    guard_provider(chain.w3.provider)
    started = time.perf_counter_ns()
    rows, errors = [], []
    result = {
        "observed": False,
        "transactions_sent": 0,
        "rule": "provider finalized.number >= target.number AND canonical target hash unchanged",
    }
    try:
        target = chain.w3.eth.get_block("latest")
        received = time.perf_counter_ns()
        result.update(target_block=target["number"], target_hash=bytes(target["hash"]).hex())
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            try:
                finalized = chain.w3.eth.get_block("finalized")
                rows.append({"number": finalized["number"], "ms": (time.perf_counter_ns() - received) / 1e6})
                if finalized["number"] >= target["number"]:
                    chain.canonical_receipt({"blockNumber": target["number"], "blockHash": target["hash"]})
                    result.update(
                        observed=True,
                        block_received_to_finality_observed_ms=(time.perf_counter_ns() - received) / 1e6,
                    )
                    break
            except Exception as exc:
                errors.append(type(exc).__name__)
            time.sleep(0.5)
    except Exception as exc:
        errors.append(type(exc).__name__)
    return {
        **result,
        "rows": rows,
        "errors": errors,
        "total_ms": (time.perf_counter_ns() - started) / 1e6,
        "limits": "Provider-tag observation of a block, not independent consensus verification or event-to-transaction finality. Does not alter execution receipt policy.",
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--samples", type=int, default=5)
    parser.add_argument("--max-seconds", type=float, default=300)
    parser.add_argument("--interval", type=float, default=0.1)
    parser.add_argument("--head-seconds", type=float, default=0)
    parser.add_argument("--shadow-seconds", type=float, default=0)
    parser.add_argument("--finality-seconds", type=float, default=0)
    parser.add_argument("--uncached", action="store_true")
    parser.add_argument("--identity", action="store_true")
    args = parser.parse_args()
    if args.samples < 1 or args.max_seconds <= 0 or args.interval < 0:
        parser.error("invalid sampling limits")
    start_commit = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    source_hashes = {
        str(p): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in [Path(__file__), Path("dipbot/market/chain.py"), Path("dipbot/market/rpc.py")]
    }
    if args.shadow_seconds > 0 or args.finality_seconds > 0:
        result = {"commit_at_start": start_commit, "source_sha256": source_hashes}
        if args.shadow_seconds > 0:
            result["shadow_processing"] = shadow_processing(args.shadow_seconds)
        if args.finality_seconds > 0:
            result["finality"] = finality_probe(args.finality_seconds)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2) + "\n")
        print(json.dumps({"output": str(args.output)}))
        raise SystemExit(0)
    if args.identity:
        result = identity_probe(ENDPOINTS[0], args.samples)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps({"commit_at_start": start_commit, "source_sha256": source_hashes, **result}, indent=2)
            + "\n"
        )
        print(json.dumps({"output": str(args.output), "rows": len(result["rows"])}))
        raise SystemExit(0)
    with ThreadPoolExecutor(max_workers=3) as executor:
        rows = list(
            executor.map(
                lambda endpoint: probe(
                    endpoint, args.samples, args.max_seconds, args.interval, args.uncached
                ),
                ENDPOINTS,
            )
        )
    report = {
        "utc": datetime.now(timezone.utc).isoformat(),
        "endpoints": rows,
        "transactions_sent": 0,
        "commit_at_start": start_commit,
        "source_sha256": source_hashes,
        "python": platform.python_version(),
        "platform": platform.platform(),
        "web3": version("web3"),
        "paid_rpc": "not provided",
        "limits": "Request latency, not broadcast or event propagation. Cache hits are separate. Distinct blocks, not loop count, bound independent sample size. No exact DNS/TLS breakdown.",
    }
    if args.head_seconds > 0:
        report["shadow_heads"] = head_delivery(args.head_seconds)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(
        json.dumps(
            {
                "output": str(args.output),
                "endpoints": [{k: v for k, v in r.items() if k != "observations"} for r in rows],
            }
        )
    )
