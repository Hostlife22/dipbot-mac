"""Converter candidate order reconstructed from Trader._buy_route_candidates.

Static profiles are public release constants. Dynamic profile preferences remain
unrecovered; use the direct-V2 preference for unknown bases, with the same fallback
list. This does not claim to reproduce AutoPair's separate pool selection.
"""
import json
from importlib.resources import files
from .chain import address, WBNB, USDT, ETH, FEES


def conversion_specs(src, dest):
    src, dest = address(src), address(dest)
    wrapped = address(WBNB)
    if (src == wrapped) == (dest == wrapped):
        raise ValueError('Converter должен начинаться или заканчиваться WBNB')
    base = dest if src == wrapped else src
    entries = json.loads(files('dipbot').joinpath('profiles.json').read_text())
    profile = next((p for p in entries if address(p['address']) == base), {})
    mode = profile.get('converter_mode', 'direct_v2')
    fee = profile.get('converter_fee') or 500
    direct = (wrapped, base)
    via_usdt = (wrapped, address(USDT), base)
    via_eth = (wrapped, address(ETH), base)
    preferred = {
        'direct_v2': ('V2', direct, ()),
        'direct_v3': ('V3', direct, (fee,)),
        'via_usdt_v3': ('V3', via_usdt, (100, fee)),
        'via_eth_v3': ('V3', via_eth, (100, fee)),
    }[mode]
    candidates = [preferred, ('V2', direct, ()), ('V2', via_usdt, ())]
    for tier in FEES:
        candidates.extend([('V3', direct, (tier,)), ('V3', via_usdt, (100, tier))])
        if mode == 'via_eth_v3':
            candidates.append(('V3', via_eth, (100, tier)))
    seen = set()
    result = []
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
