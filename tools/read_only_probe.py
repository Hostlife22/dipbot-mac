"""Explicit read-only BSC integration probe; no wallet, signing or local Store."""

import argparse
import hashlib
import json
import platform
import subprocess
import time
from datetime import datetime, timezone
from decimal import Decimal as D
from pathlib import Path
from urllib.parse import urlsplit

import requests

from dipbot.checks.read_only import guard_provider
from dipbot.domain.assets import USDT, WBNB
from dipbot.market.chain import POOL_ABI, Chain, address
from dipbot.market.discovery import MULTICALL, batch, discover, request, resolve


def probe(endpoint):
    chain = Chain(endpoint)
    calls = guard_provider(chain.w3.provider)
    block = chain.check()
    assert chain.w3.eth.get_code(address(MULTICALL))
    catalogs = {"V2": {"WBNB": WBNB}, "V3": {"WBNB": WBNB}}
    candidates = discover(chain, address(USDT), catalogs, block)
    # Compare batch and individual reads at the SAME block for every candidate.
    requests = [
        request(c.pool.address, POOL_ABI, name)
        for c in candidates
        for name in ("factory", "token0", "token1")
    ]
    batched = batch(chain, requests, block)
    direct = [chain.call(addr, abi, name, *args, block=block) for addr, abi, name, args in requests]
    assert [address(v) for v in batched] == [address(v) for v in direct]
    checked = []
    for router in ("V2", "V3"):
        candidate = next(c for c in candidates if c.ready and c.pool.router == router)
        pool = chain.verify_pool(candidate.pool.address, address(USDT))
        by_pool = resolve(chain, pool.address, catalogs)
        assert by_pool.state == "RESOLVED" and by_pool.target == address(USDT)
        buy = chain.quote(pool, 10**15, True)
        sell = chain.quote(pool, 10**18, False)
        assert buy > 0 and sell > 0
        checked.append(
            {
                "router": router,
                "pool": pool.address,
                "fee": pool.fee,
                "buy_0_001_wbnb_raw": buy,
                "sell_1_usdt_raw": sell,
            }
        )
    assert resolve(chain, WBNB, catalogs).state == "CATALOG_TOKEN"
    return {
        "utc": datetime.now(timezone.utc).isoformat(),
        "discovery_block": block,
        "endpoint_host": urlsplit(endpoint).hostname,
        "candidates": len(candidates),
        "checked": checked,
        "multicall_metadata_reads": len(requests),
        "multicall_matches_direct": True,
        "rpc_methods": dict(calls),
        "transactions_sent": 0,
    }


def market_snapshots(endpoint, token, pool_address, seconds, usd, output):
    """Archive contemporaneous size quotes; no historical RPC or wallet required."""
    from dipbot.domain.usd import select_rate
    from dipbot.market.activity import read_swaps

    chain = Chain(endpoint, request_timeout=3)
    calls = guard_provider(chain.w3.provider)
    pool = chain.verify_pool(address(pool_address), address(token))
    deadline = time.monotonic() + seconds
    rate = None
    refreshed = float("-inf")
    count = errors = activity_errors = 0
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x") as stream:
        stream.write(
            json.dumps(
                {
                    "event": "header",
                    "dirty": bool(
                        subprocess.check_output(["git", "status", "--porcelain"], text=True).strip()
                    ),
                    "source_sha256": {
                        str(p): hashlib.sha256(p.read_bytes()).hexdigest()
                        for p in [
                            Path(__file__),
                            Path("dipbot/market/chain.py"),
                            Path("dipbot/checks/read_only.py"),
                        ]
                    },
                    "python": platform.python_version(),
                    "endpoint_host": urlsplit(endpoint).hostname,
                    "commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
                    "token": pool.token,
                    "pool": pool.address,
                    "quote": pool.quote,
                    "router": pool.router,
                    "usd_size": str(usd),
                    "usd_source": "DexScreener indicative quote-token rate",
                    "limits": "Reverse quote is at pretrade state; does not simulate sequential pool impact, token tax or MEV. Sampled intervals are not a complete price history.",
                }
            )
            + "\n"
        )
        while time.monotonic() < deadline:
            started = time.monotonic()
            row = {"event": "snapshot", "utc": datetime.now(timezone.utc).isoformat()}
            try:
                if started - refreshed >= 30:
                    try:
                        response = requests.get(
                            "https://api.dexscreener.com/tokens/v1/bsc/" + pool.quote, timeout=3
                        )
                        response.raise_for_status()
                        rate = select_rate(response.json(), pool.quote)
                        refreshed = time.monotonic()
                    except Exception as exc:
                        row["usd_refresh_error"] = type(exc).__name__
                if rate is None or time.monotonic() - refreshed > 90:
                    raise ValueError("USD rate unavailable or stale")
                price = chain.price(pool)
                header = dict(chain.price_block)
                block = header["number"]
                amount = int(usd / rate * D(10) ** pool.quote_decimals)
                bought = chain.quote(pool, amount, True, block=block)
                returned = chain.quote(pool, bought, False, block=block)
                row.update(
                    price=str(price),
                    pool_state=chain.price_state,
                    block=block,
                    block_hash=bytes(header["hash"]).hex(),
                    quote_usd=str(rate),
                    usd_age_seconds=time.monotonic() - refreshed,
                    amount_in_raw=str(amount),
                    buy_out_raw=str(bought),
                    reverse_pretrade_out_raw=str(returned),
                )
                try:
                    events = read_swaps(chain, pool, max(0, block - 99), block, header)
                    row["activity"] = {
                        "from_block": max(0, block - 99),
                        "to_block": block,
                        "count": len(events),
                    }
                except Exception as exc:
                    row["activity_error"] = type(exc).__name__
                    activity_errors += 1
                chain.canonical_receipt({"blockNumber": block, "blockHash": header["hash"]})
                row["canonical"] = True
                count += 1
            except Exception as exc:
                row["error_type"] = type(exc).__name__
                status = getattr(getattr(exc, "response", None), "status_code", None)
                if type(status) is int:
                    row["http_status"] = status
                row["canonical"] = False
                errors += 1
            row["elapsed_ms"] = (time.monotonic() - started) * 1000
            stream.write(json.dumps(row) + "\n")
            stream.flush()
            time.sleep(max(0, min(deadline - time.monotonic(), started + 5 - time.monotonic())))
        stream.write(
            json.dumps(
                {
                    "event": "end",
                    "snapshots": count,
                    "errors": errors,
                    "activity_errors": activity_errors,
                    "provider_calls_including_chain_id_cache": dict(calls),
                    "transactions_sent": 0,
                }
            )
            + "\n"
        )
    return {"snapshots": count, "errors": errors, "activity_errors": activity_errors}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--endpoint", default="https://bsc-dataseed-public.bnbchain.org")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--token")
    parser.add_argument("--pool")
    parser.add_argument("--seconds", type=float, default=600)
    parser.add_argument("--amount-usd", type=D, default=D(20))
    args = parser.parse_args()
    if args.token or args.pool:
        if (
            not args.token
            or not args.pool
            or not 0 < args.seconds <= 3600
            or not args.amount_usd.is_finite()
            or args.amount_usd <= 0
        ):
            parser.error("token, pool, positive USD amount and seconds <=3600 required")
        result = market_snapshots(
            args.endpoint, args.token, args.pool, args.seconds, args.amount_usd, args.output
        )
        print(json.dumps(result))
        raise SystemExit(
            0 if result["snapshots"] and not result["errors"] and not result["activity_errors"] else 1
        )
    args.output.write_text(json.dumps(probe(args.endpoint), indent=2) + "\n")
    print("Read-only probe passed:", args.output)
