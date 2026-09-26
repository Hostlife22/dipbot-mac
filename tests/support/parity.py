"""Shared parity fixtures/builders."""

import json
from pathlib import Path

from dipbot.market.chain import Pool, address


def pool(token, quote, version="V3", fee=500, ident="12"):
    return Pool(address("0x" + ident * 20), version, address(token), address(quote), 18, 18, True, fee)


VECTORS = json.loads((Path(__file__).parent.parent / "fixtures/parity/strategy.json").read_text())
