"""Read-only RPC timing; record method timings without params or credentials."""

import argparse
import json
import statistics
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
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
            "max_ms": round(max(values) * 1000, 2),
        }
        if values
        else {}
    )


def probe(endpoint, samples):
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
        for _ in range(samples):
            for router, pool in pools.items():
                started = time.monotonic()
                assert chain.price(pool) > 0
                prices[router].append(time.monotonic() - started)
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


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--samples", type=int, default=5)
    args = parser.parse_args()
    with ThreadPoolExecutor(max_workers=3) as executor:
        rows = list(executor.map(lambda endpoint: probe(endpoint, args.samples), ENDPOINTS))
    report = {"utc": datetime.now(timezone.utc).isoformat(), "endpoints": rows, "transactions_sent": 0}
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report))
