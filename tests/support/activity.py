"""Shared activity fixtures/builders."""
from dataclasses import replace
from types import SimpleNamespace as NS
from decimal import Decimal as D
import pytest
from web3 import Web3
from dipbot.market.activity import swap_count, SIGNATURES
from dipbot.domain.strategy import Settings
from tests.support.markets import POOL


def setup(router='V2'):
    pool = replace(POOL, router=router)
    row = dict(address=pool.address, blockNumber=100, blockHash=b'b'*32,
               transactionHash=b't'*32, logIndex=0, removed=False,
               topics=[Web3.keccak(text=SIGNATURES[router]),b'a'*32,b'c'*32],
               data=bytes(128 if router == 'V2' else 224))
    calls = []
    logs = [row, row]
    c = NS(check=lambda **kw:123, checked_header={'hash':b'h'*32},
           canonical_receipt=lambda r:calls.append(r),
           w3=NS(eth=NS(get_logs=lambda params:logs)))
    return c, pool, logs, calls
