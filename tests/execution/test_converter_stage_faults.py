"""Offline V3 swap/unwrap boundaries with real signing and durable transaction records."""
from types import SimpleNamespace
import pytest
from dipbot.market.chain import ETH, WBNB, address
from dipbot.domain.strategy import D
from dipbot.persistence.storage import Store
from dipbot.execution.errors import UncertainTransaction
from tests.support.execution import Function
from tests.support.parity import pool


def setup_conversion(trader, delta=200, fault=None):
    route = [pool(WBNB, ETH, 'V3')]
    trader.conversion_route = lambda *args: route
    trader.verify_router = lambda _: (address('0x'+'56'*20), [])
    trader.approve = lambda *args: None  # Sufficient allowance before this scenario.
    chain, eth = trader.chain, trader.chain.w3.eth
    chain.verify_pool = lambda *args: route[0]
    chain.quote_route = lambda *args, reverse=False: 95 if reverse else 200
    reads = []
    withdrawals, broadcasts = [], []

    def balance(token, owner):
        if address(token) == address(ETH): return 100
        reads.append(token)
        if len(reads) == 1: return 1000
        if fault == 'balance_read': raise OSError('synthetic post-swap RPC failure')
        return 1000+delta

    class UnwrapFunction(Function):
        def estimate_gas(self, tx):
            if fault == 'unwrap_estimate': raise OSError('synthetic unwrap preflight failure')
            return super().estimate_gas(tx)

    def withdraw(amount):
        withdrawals.append(amount)
        return UnwrapFunction()

    chain.balance = balance
    chain.contract = lambda *args: SimpleNamespace(functions=SimpleNamespace(
        exactInput=lambda args: Function(), withdraw=withdraw))
    send, wait = eth.send_raw_transaction, eth.wait_for_transaction_receipt
    eth.get_transaction_count = lambda *args: len(broadcasts)

    def broadcast(raw):
        broadcasts.append(raw)
        result = send(raw)
        if len(broadcasts) == 2 and fault == 'unwrap_send':
            raise TimeoutError('synthetic lost unwrap send response')
        return result

    def receipt(*args, **kwargs):
        if len(broadcasts) == 2 and fault == 'unwrap_receipt':
            raise TimeoutError('synthetic lost unwrap receipt')
        return wait(*args, **kwargs)

    eth.send_raw_transaction, eth.wait_for_transaction_receipt = broadcast, receipt
    trader.begin('V3 conversion boundary')
    return withdrawals, broadcasts


@pytest.mark.parametrize('delta', [-1, 0, 1, 195, 196, 200])
def test_post_swap_delta_preserves_minimum_and_preexisting_wbnb(trader, delta):
    withdrawals, broadcasts = setup_conversion(trader, delta)
    if delta < 196:
        with pytest.raises(UncertainTransaction): trader.convert(ETH, 100, False, D(2))
        assert withdrawals == []
        assert len(broadcasts) == 1
    else:
        assert trader.convert(ETH, 100, False, D(2)) == delta
        assert withdrawals == [delta]  # Never include the pre-existing 1000 raw WBNB.
        assert len(broadcasts) == 2
    assert all(r['status'] == 'confirmed' for r in Store(trader.store.path).data['operation']['transactions'])
    trader.store = Store(trader.store.path)
    with pytest.raises(UncertainTransaction): trader.begin('unreviewed restart')


@pytest.mark.parametrize('fault', ['balance_read', 'unwrap_estimate', 'unwrap_send', 'unwrap_receipt'])
def test_failure_between_swap_and_unwrap_remains_recoverable(trader, fault):
    withdrawals, broadcasts = setup_conversion(trader, fault=fault)
    with pytest.raises((OSError, UncertainTransaction)):
        trader.convert(ETH, 100, False, D(2))
    records = Store(trader.store.path).data['operation']['transactions']
    assert records[0]['status'] == 'confirmed'
    ambiguous = fault in ('unwrap_send', 'unwrap_receipt')
    assert len(records) == len(broadcasts) == (2 if ambiguous else 1)
    if ambiguous: assert records[1]['status'] == 'pending'
    trader.store = Store(trader.store.path)
    with pytest.raises(UncertainTransaction): trader.begin('duplicate conversion')
    assert len(broadcasts) == (2 if ambiguous else 1)
