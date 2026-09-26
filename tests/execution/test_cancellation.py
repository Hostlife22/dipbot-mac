from tests.support.cancellation import H, original, make
from decimal import Decimal as D
from types import SimpleNamespace as NS
from eth_account import Account
from web3 import Web3
from web3.exceptions import TransactionNotFound
import pytest
from dipbot.execution.cancellation import cancellation_plan, cancel_pending
from dipbot.persistence.storage import Store
from dipbot.execution.trader import LiveTrader
from dipbot.execution.errors import UncertainTransaction
from dipbot.execution.reconciliation import reconcile_receipts







def test_cancel_writes_before_send_and_never_unlocks(tmp_path):
    trader,sent,receipts=make(tmp_path)
    result=cancel_pending(trader,expected_hash=H,expected_gas_price=125000000)
    assert 'заблокирована' in result and len(sent)==1
    record=trader.store.data['operation']['transactions'][-1]
    assert record['replaces']==H and record['request']['value']==0
    assert record['request']['to']==trader.owner and record['request']['data']=='0x'
    reconcile_receipts(trader.chain,trader.store,trader.owner)
    records=trader.store.data['operation']['transactions']
    assert [r['status'] for r in records]==['superseded','confirmed']
    assert len(trader.store.data['gas_ledger'])==1


def test_lost_ack_preserves_both_hashes_and_prohibits_retry(tmp_path):
    trader,sent,receipts=make(tmp_path,timeout=True)
    with pytest.raises(UncertainTransaction):cancel_pending(trader,expected_hash=H,expected_gas_price=125000000)
    records=Store(trader.store.path).data['operation']['transactions']
    assert len(sent)==1 and len(records)==2 and records[-1]['stage']=='prepared'
    with pytest.raises(ValueError,match='уже записана'):
        cancellation_plan(trader.store.data['operation'],D('.1'))


def test_changed_plan_or_failed_save_never_broadcasts(tmp_path):
    trader,sent,receipts=make(tmp_path)
    with pytest.raises(ValueError,match='изменился'):
        cancel_pending(trader,expected_hash=H,expected_gas_price=1)
    def fail():raise OSError('disk full')
    trader.store.save=fail
    with pytest.raises(OSError):cancel_pending(trader,expected_hash=H,expected_gas_price=125000000)
    assert not sent


@pytest.mark.parametrize('kind',['wrong_nonce','contract','already_mined','custom_missing'])
def test_cancel_guards_before_sign_and_send(tmp_path,kind):
    trader,sent,receipts=make(tmp_path)
    if kind=='wrong_nonce':trader.chain.w3.eth.get_transaction_count=lambda *a:2
    if kind=='contract':trader.chain.w3.eth.get_code=lambda *a:b'code'
    if kind=='already_mined':receipts[H]={}
    if kind=='custom_missing':trader.store.data['operation']['transactions'][0]['broadcast_route']='custom'
    with pytest.raises(ValueError):cancel_pending(trader,expected_hash=H,expected_gas_price=125000000)
    assert not sent


def test_original_wins_cancel_cannot_fake_a_second_fee(tmp_path):
    trader,sent,receipts=make(tmp_path)
    cancel_pending(trader,expected_hash=H,expected_gas_price=125000000)
    records=trader.store.data['operation']['transactions']
    replacement=records[-1]
    proof=receipts.pop(replacement['hash'])
    replacement['status']='pending';replacement.pop('block_hash')
    trader.store.data['gas_ledger'].clear()
    receipts[H]={**proof,'transactionHash':bytes.fromhex(H[2:])}
    reconcile_receipts(trader.chain,trader.store,trader.owner)
    assert [r['status'] for r in records]==['confirmed','superseded']
    assert replacement['gas_fee_wei']==0 and len(trader.store.data['gas_ledger'])==1


def test_custom_cancellation_does_not_fall_back_to_public(tmp_path):
    trader,sent,receipts=make(tmp_path)
    trader.store.data['operation']['transactions'][0]['broadcast_route']='custom'
    private=[]
    def failed(raw):private.append(raw);raise TimeoutError('private timeout')
    trader.broadcast_chain=NS(check=lambda:None,w3=NS(eth=NS(send_raw_transaction=failed)))
    with pytest.raises(UncertainTransaction):cancel_pending(trader,expected_hash=H,expected_gas_price=125000000)
    assert len(private)==1 and not sent


def test_conflicting_receipts_and_disappeared_confirmation_keep_latch(tmp_path):
    trader,sent,receipts=make(tmp_path)
    cancel_pending(trader,expected_hash=H,expected_gas_price=125000000)
    rows=trader.store.data['operation']['transactions']
    receipts[H]={**receipts[rows[-1]['hash']],'transactionHash':bytes.fromhex(H[2:])}
    with pytest.raises(UncertainTransaction,match='одним nonce'):
        reconcile_receipts(trader.chain,trader.store,trader.owner)
    receipts.pop(H)
    with pytest.raises(UncertainTransaction,match='исчез'):
        reconcile_receipts(trader.chain,trader.store,trader.owner)
    assert trader.store.data['operation']


def test_retry_bumps_highest_fee_and_reconciles_all_siblings(tmp_path):
    trader,sent,receipts=make(tmp_path,timeout=True)
    with pytest.raises(UncertainTransaction):cancel_pending(trader,expected_hash=H,expected_gas_price=125000000)
    rows=trader.store.data['operation']['transactions'];rows[-1]['prepared_at']-=31
    plan=cancellation_plan(trader.store.data['operation'],D('.1'))
    assert plan['attempt']==2 and plan['gas_price']==156250000
    with pytest.raises(ValueError,match='изменился'):
        cancel_pending(trader,expected_hash=H,expected_gas_price=125000000)
    with pytest.raises(UncertainTransaction):cancel_pending(trader,expected_hash=H,expected_gas_price=156250000)
    assert len(sent)==2 and len(rows)==3
    winner=rows[-1]['hash']
    receipts[winner]={'transactionHash':bytes.fromhex(winner[2:]),'status':1,
        'blockNumber':42,'blockHash':b'b'*32,'gasUsed':21000,'effectiveGasPrice':156250000}
    reconcile_receipts(trader.chain,trader.store,trader.owner)
    assert [r['status'] for r in rows]==['superseded','superseded','confirmed']
    assert len(trader.store.data['gas_ledger'])==1 and trader.store.data['operation']


def test_retry_checks_previous_cancel_receipt_before_signing(tmp_path):
    trader,sent,receipts=make(tmp_path,timeout=True)
    with pytest.raises(UncertainTransaction):cancel_pending(trader,expected_hash=H,expected_gas_price=125000000)
    row=trader.store.data['operation']['transactions'][-1];row['prepared_at']-=31
    receipts[row['hash']]={}
    with pytest.raises(ValueError,match='Receipt'):
        cancel_pending(trader,expected_hash=H,expected_gas_price=156250000)
    assert len(sent)==1


def test_retry_limit_and_original_winning_after_two_cancels(tmp_path):
    trader,sent,receipts=make(tmp_path,timeout=True)
    for expected in (125000000,156250000,195312500):
        with pytest.raises(UncertainTransaction):cancel_pending(trader,expected_hash=H,expected_gas_price=expected)
        trader.store.data['operation']['transactions'][-1]['prepared_at']-=31
    with pytest.raises(ValueError,match='3 попытки'):
        cancellation_plan(trader.store.data['operation'],D('.1'))
    receipts[H]={'transactionHash':bytes.fromhex(H[2:]),'status':1,
        'blockNumber':42,'blockHash':b'b'*32,'gasUsed':21000,'effectiveGasPrice':100000000}
    reconcile_receipts(trader.chain,trader.store,trader.owner)
    rows=trader.store.data['operation']['transactions']
    assert [r['status'] for r in rows]==['confirmed','superseded','superseded','superseded']
    assert len(trader.store.data['gas_ledger'])==1
