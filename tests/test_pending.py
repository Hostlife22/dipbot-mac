from types import SimpleNamespace as NS
import pytest
from web3.exceptions import TransactionNotFound
from dipbot.pending import inspect_missing
from dipbot.trader import reconcile_receipts, UncertainTransaction
from dipbot.storage import Store

OWNER = '0x'+'12'*20
HASH = '0x'+'34'*32


def chain(latest=4,pending=4,tx=None):
    def get_tx(_):
        if tx is None: raise TransactionNotFound('synthetic missing')
        return tx
    eth=NS(get_transaction=get_tx,get_transaction_count=lambda owner,tag: latest if tag=='latest' else pending)
    return NS(check=lambda:1,w3=NS(eth=eth))


@pytest.mark.parametrize('latest,pending,state',[(4,4,'not_visible'),(4,5,'pending_nonce_advanced'),(5,5,'nonce_consumed')])
def test_nonce_evidence_is_not_a_dropped_declaration(latest,pending,state):
    result=inspect_missing(chain(latest,pending),OWNER,{'hash':HASH,'nonce':4})
    assert result['state']==state
    assert 'dropped' not in result.values()


def test_visible_pending_and_inconsistent_identity():
    tx={'hash':bytes.fromhex(HASH[2:]),'nonce':4,'from':OWNER,'blockNumber':None}
    assert inspect_missing(chain(tx=tx),OWNER,{'hash':HASH,'nonce':4})['state']=='pending_visible'
    tx['nonce']=5
    assert inspect_missing(chain(tx=tx),OWNER,{'hash':HASH,'nonce':4})['state']=='unavailable'


def test_reconcile_saves_evidence_and_keeps_latch(tmp_path):
    store=Store(tmp_path/'state.json')
    store.data['operation']={'wallet':OWNER,'transactions':[{'hash':HASH,'nonce':4,'status':'pending'}]}
    c=chain(5,5)
    def missing(_):raise TransactionNotFound('synthetic missing')
    c.w3.eth.get_transaction_receipt=missing
    with pytest.raises(UncertainTransaction,match='Nonce'):
        reconcile_receipts(c,store,OWNER)
    saved=Store(store.path).data['operation']
    assert saved['transactions'][0]['status']=='pending'
    assert saved['transactions'][0]['receipt_review']['state']=='nonce_consumed'
