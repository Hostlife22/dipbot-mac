from types import SimpleNamespace as NS
import pytest
from web3 import Web3
from dipbot.execution.errors import UncertainTransaction
from tests.support.execution import Function


def test_custom_broadcast_keeps_primary_receipts(trader):
    calls=[]
    primary=trader.chain.w3.eth
    original=primary.send_raw_transaction
    def forbidden(raw):pytest.fail('Public fallback forbidden')
    def private(raw):
        calls.append('custom')
        return original(raw)
    primary.send_raw_transaction=forbidden
    trader.broadcast_chain=NS(check=lambda:calls.append('check'),w3=NS(eth=NS(send_raw_transaction=private)))
    trader.begin('BUY')
    trader.send(Function(),'BUY')
    assert calls==['check','custom']
    record=trader.operation['transactions'][0]
    assert record['broadcast_route']=='custom' and record['status']=='confirmed'


def test_custom_timeout_keeps_latch_and_never_falls_back(trader):
    def forbidden(raw):pytest.fail('Public fallback forbidden')
    def fail(raw):raise TimeoutError()
    trader.chain.w3.eth.send_raw_transaction=forbidden
    trader.broadcast_chain=NS(check=lambda:None,w3=NS(eth=NS(send_raw_transaction=fail)))
    trader.begin('BUY')
    with pytest.raises(UncertainTransaction):trader.send(Function(),'BUY')
    assert trader.operation['transactions'][0]['status']=='pending'
    assert trader.store.data['operation']


def test_custom_wrong_network_stops_before_sign_or_journal_transaction(trader):
    def bad():raise ValueError('wrong chain')
    trader.broadcast_chain=NS(check=bad)
    trader.begin('BUY')
    with pytest.raises(ValueError):trader.send(Function(),'BUY')
    assert trader.operation['transactions']==[]
