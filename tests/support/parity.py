"""Shared parity fixtures/builders."""
from types import SimpleNamespace
import pytest
from dipbot.market.chain import Chain, Pool, WBNB, USDT, ETH, address
from dipbot.execution.trader import LiveTrader
from dipbot.execution.errors import UncertainTransaction
from dipbot.persistence.storage import Store
from dipbot.domain.strategy import D
import json
from pathlib import Path
from dipbot.domain.strategy import Strategy, Settings


def pool(token, quote, version='V3', fee=500, ident='12'):
    return Pool(address('0x' + ident*20), version, address(token), address(quote), 18, 18, True, fee)


VECTORS = json.loads((Path(__file__).parent.parent / 'fixtures/parity/strategy.json').read_text())
