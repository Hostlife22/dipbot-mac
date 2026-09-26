"""Converter candidate order reconstructed from Trader._buy_route_candidates.

Static profiles are public release constants. Verified dynamic preferences may
be supplied by the router-specific Mac registry. Legacy unverified entries retain
the direct-V2 fallback until explicitly checked again.
"""

from __future__ import annotations

import json
from importlib.resources import files
from typing import Any

from dipbot.domain.assets import ETH, FEES, USDT, WBNB
from dipbot.market.chain import address


def seed_preference(token: str) -> dict[str, Any]:
    entries = json.loads(files("dipbot").joinpath("profiles.json").read_text())
    profile: dict[str, Any] | None = next(
        (p for p in entries if address(p["address"]) == address(token)), None
    )
    return (
        {"converter_mode": profile["converter_mode"], "converter_fee": profile.get("converter_fee") or 500}
        if profile
        else {"converter_mode": "via_usdt_v3", "converter_fee": 500}
    )


def conversion_specs(
    src: str, dest: str, preference: Any = None
) -> list[tuple[str, tuple[str, ...], tuple[int, ...]]]:
    src, dest = address(src), address(dest)
    wrapped = address(WBNB)
    if (src == wrapped) == (dest == wrapped):
        raise ValueError("Converter должен начинаться или заканчиваться WBNB")
    base = dest if src == wrapped else src
    entries = json.loads(files("dipbot").joinpath("profiles.json").read_text())
    profile: dict[str, Any] = next((p for p in entries if address(p["address"]) == base), {})
    if preference is not None:
        profile = preference
    mode = profile.get("converter_mode", "direct_v2")
    fee = profile.get("converter_fee") or 500
    direct = (wrapped, base)
    via_usdt = (wrapped, address(USDT), base)
    via_eth = (wrapped, address(ETH), base)
    preferred = {
        "direct_v2": ("V2", direct, ()),
        "direct_v3": ("V3", direct, (fee,)),
        "via_usdt_v3": ("V3", via_usdt, (100, fee)),
        "via_eth_v3": ("V3", via_eth, (100, fee)),
    }[mode]
    candidates = [preferred, ("V2", direct, ()), ("V2", via_usdt, ())]
    for tier in FEES:
        candidates.extend([("V3", direct, (tier,)), ("V3", via_usdt, (100, tier))])
        if mode == "via_eth_v3":
            candidates.append(("V3", via_eth, (100, tier)))
    seen = set()
    result: list[tuple[str, tuple[str, ...], tuple[int, ...]]] = []
    for kind, tokens, fees in candidates:
        if len(set(tokens)) != len(tokens):
            continue
        if dest == wrapped:
            tokens, fees = tokens[::-1], fees[::-1]
        spec = (kind, tokens, fees)
        if spec not in seen:
            seen.add(spec)
            result.append(spec)
    return result
