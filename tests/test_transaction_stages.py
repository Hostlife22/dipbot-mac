from copy import deepcopy
import pytest
from dipbot.storage import Store
from dipbot.trader import UncertainTransaction
from test_execution import trader, Function


@pytest.mark.parametrize('key,value',[('chainId',1),('nonce',99),('value',1),('gasPrice',1),('gas',1)])
def test_builder_cannot_change_authorized_envelope(trader,key,value):
    class Tampered(Function):
        def build_transaction(self,tx):
            return super().build_transaction(tx) | {key:value}
    trader.begin('test')
    with pytest.raises(ValueError,match='транзакция изменила'):
        trader.send(Tampered(),'test')
    assert trader.operation['transactions']==[]


def test_acknowledged_send_is_durable_before_receipt_wait(trader):
    trader.begin('test')
    original=trader.chain.w3.eth.wait_for_transaction_receipt
    def wait(tx_hash,**kw):
        row=Store(trader.store.path).data['operation']['transactions'][-1]
        assert row['stage']=='submitted' and row['request']['chainId']==56
        assert row['submitted_at']>=row['prepared_at']
        return original(tx_hash,**kw)
    trader.chain.w3.eth.wait_for_transaction_receipt=wait
    trader.send(Function(),'test')
    assert Store(trader.store.path).data['operation']['transactions'][-1]['stage']=='receipt_validated'


def test_ack_save_failure_preserves_latch_and_never_resends(trader):
    trader.begin('test')
    save=trader.store.save
    def fail():
        rows=trader.operation['transactions']
        if rows and rows[-1]['stage']=='submitted':
            raise OSError('synthetic disk failure after send')
        save()
    trader.store.save=fail
    with pytest.raises(UncertainTransaction):
        trader.send(Function(),'test')
    row=Store(trader.store.path).data['operation']['transactions'][0]
    assert row['stage']=='prepared' and row['status']=='pending'
    with pytest.raises(UncertainTransaction):
        trader.begin('duplicate')
