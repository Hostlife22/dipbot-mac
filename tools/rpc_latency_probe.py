"""Read-only RPC timing; record method timings without params or credentials."""

import argparse
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


def probe(endpoint, samples, max_seconds=300, interval=0.1):
    result = {"host": urlsplit(endpoint).hostname, "samples_per_router": samples}
    chain = Chain(endpoint)
    provider = chain.w3.provider
    guard_provider(provider)
    original = provider._make_request
    timings = defaultdict(list)

    def measured(method, *args, **kwargs):
        started = time.monotonic()
        try:
            return original(method, *args, **kwargs)
        finally:
            timings[method].append(time.monotonic() - started)

    provider._make_request = measured
    try:
        pools = {router: chain.verify_pool(addr, USDT) for router, addr in POOLS.items()}
        timings.clear()
        prices = defaultdict(list)
        observations = []
        deadline = time.monotonic() + max_seconds
        for _ in range(samples):
            if time.monotonic() >= deadline:
                break
            for router, pool in pools.items():
                started = time.monotonic()
                assert chain.price(pool) > 0
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
    return result


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


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--samples", type=int, default=5)
    parser.add_argument("--max-seconds", type=float, default=300)
    parser.add_argument("--interval", type=float, default=0.1)
    parser.add_argument("--head-seconds", type=float, default=0)
    args = parser.parse_args()
    if args.samples < 1 or args.max_seconds <= 0 or args.interval < 0:
        parser.error("invalid sampling limits")
    with ThreadPoolExecutor(max_workers=3) as executor:
        rows = list(
            executor.map(
                lambda endpoint: probe(endpoint, args.samples, args.max_seconds, args.interval), ENDPOINTS
            )
        )
    report = {
        "utc": datetime.now(timezone.utc).isoformat(),
        "endpoints": rows,
        "transactions_sent": 0,
        "commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
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
    print(json.dumps(report))
