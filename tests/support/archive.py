"""Shared archive fixtures/builders."""

import json
from pathlib import Path

from dipbot.research.market_tape import MarketTape


def read(path):
    return [json.loads(line) for line in Path(path).read_text().splitlines()]


def rotated(directory, **kwargs):
    tape = MarketTape(directory, {"mode": "DEMO"}, max_bytes=2048, capacity=2048, **kwargs)
    for i in range(100):
        tape.record("price", price=str(i + 1), block=i)
    assert tape.close()
    return tape
