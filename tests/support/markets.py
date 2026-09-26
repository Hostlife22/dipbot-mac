"""Shared markets fixtures/builders."""
from dataclasses import replace
from types import SimpleNamespace
import pytest
from dipbot.market.autopair import Candidate, choose
from dipbot.market.chain import Chain, Pool, WBNB, USDT, ETH, address, ZERO
from dipbot.persistence.storage import Store
from dipbot.application.worker import Worker
from dipbot.market.routes import conversion_specs
from dipbot.persistence import dynamic


BASE = address('0x'+'ab'*20)


TARGET = address('0x'+'cd'*20)


POOL = Pool(address('0x'+'12'*20), 'V3', TARGET, BASE, 18, 18, True, 500)


def candidate(ready=True, score=100, name='BASE', router='V3', fee=500):
    return Candidate(replace(POOL, router=router, fee=fee), name, ready, score)


def route(mode='direct_v3', fee=2500):
    if mode=='direct_v2': return [replace(POOL, router='V2', token=BASE, quote=address(WBNB), fee=0)]
    if mode=='direct_v3': return [replace(POOL, token=BASE, quote=address(WBNB), fee=fee)]
    bridge=address(ETH if mode=='via_eth_v3' else USDT)
    return [replace(POOL, token=bridge, quote=address(WBNB), fee=100),
            replace(POOL, address=address('0x'+'13'*20), token=BASE, quote=bridge, fee=fee)]
