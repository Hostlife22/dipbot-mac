from dataclasses import replace
from decimal import Decimal as D
from types import SimpleNamespace as NS
import pytest
from dipbot.route_comparison import compare
from dipbot.cost_policy import CostPolicy
from dipbot.accounting import RateBook
from dipbot.entry_guard import EntryRejected
from test_autopair_dynamic import POOL


def run(chain, pools, **kwargs):
    return compare(chain,pools,POOL,D(1),D(3),CostPolicy(),D('.1'),RateBook(),**kwargs)


def test_same_block_amount_comparison_ignores_raw_liquidity():
    other=replace(POOL,address='0x'+'34'*20,router='V2',fee=2500)
    foreign=replace(POOL,address='0x'+'56'*20,quote='0x'+'ab'*19+'01')
    calls=[];canonical=[]
    def quote(pool,amount,buy,**kw):
        calls.append((pool.address,amount,buy,kw['block']))
        if buy:return 2*10**18
        return int((D('.99') if pool==other else D('.98'))*10**18)
    chain=NS(check=lambda **kw:42,checked_header={'hash':b'a'*32},quote=quote,
             canonical_receipt=canonical.append)
    result=run(chain,[POOL,other,foreign])
    assert result['rows'][0]['pool']==other
    assert result['excluded_other_pairs']==1
    assert len(calls)==4 and {c[3] for c in calls}=={42}
    assert calls[1][1]==2*10**18
    assert canonical==[{'blockNumber':42,'blockHash':b'a'*32}]


def test_failed_quote_is_not_zero_or_secret_error():
    def quote(*a,**kw):raise TimeoutError('https://private.example/secret')
    chain=NS(check=lambda **kw:42,checked_header={'hash':b'a'*32},quote=quote,canonical_receipt=lambda _:None)
    row=run(chain,[POOL])['rows'][0]
    assert row['error']=='Котировка недоступна: TimeoutError'
    assert 'target_out' not in row


def test_reorg_invalidates_entire_comparison():
    def canonical(_):raise ValueError('reorg')
    chain=NS(check=lambda **kw:42,checked_header={'hash':b'a'*32},quote=lambda p,a,b,**kw:a,canonical_receipt=canonical)
    with pytest.raises(ValueError,match='reorg'):run(chain,[POOL])


def test_stale_generation_cancels_comparison():
    chain=NS(check=lambda **kw:42,checked_header={'hash':b'a'*32})
    with pytest.raises(EntryRejected,match='отменено'):run(chain,[POOL],cancelled=lambda:True)
