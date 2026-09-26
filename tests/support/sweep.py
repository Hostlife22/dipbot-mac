"""Shared sweep fixtures/builders."""
from dataclasses import replace
import json
import subprocess
import sys
from types import SimpleNamespace
import pytest
from dipbot.market.chain import address
from dipbot.persistence.storage import Store
from dipbot.domain.strategy import D
from dipbot.execution.trader import LiveTrader
from dipbot.execution.errors import UncertainTransaction
from dipbot.persistence import preferences
from tests.support.recovery import setup_worker, POOL, OWNER
from tests.support.crash import SCRIPT


def multi_worker(tmp_path):
    worker=setup_worker(tmp_path)
    other=replace(POOL,address=address('0x'+'ac'*20),token=address('0x'+'bc'*20))
    worker.pool=other;worker.set_position(300,D('2'));worker.pool=POOL
    balances={POOL.token:200,other.token:300};sent=[];reports=[]
    pools={p.address:p for p in (POOL,other)}
    worker.chain=SimpleNamespace(balance=lambda token,owner:balances.get(token,0),
        verify_pool=lambda addr,*_:pools[addr],quote=lambda *_:190)
    worker.live.begin=lambda description:sent.append(description)
    worker.live.finish=worker.store.save
    worker.live.swap=lambda pool,*a,**k:balances.update({pool.token:0})
    worker.command=lambda *a:None
    worker.event.connect(lambda name,value:reports.append(value) if name=='sweep_report' else None)
    return worker,other,balances,sent,reports
