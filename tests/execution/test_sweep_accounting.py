from dataclasses import asdict,replace
from decimal import Decimal as D
from types import SimpleNamespace as NS
from dipbot.execution.accounting import record_sweep_exit,closed_summary
from dipbot.persistence.storage import Store
from dipbot.application.worker import Worker
from tests.support.markets import POOL

OWNER='0x'+'34'*20


def positions(store):
    pools=[POOL,replace(POOL,address='0x'+'56'*20)]
    store.data['positions']={OWNER+':'+p.address.lower():
        {'pool':asdict(p),'amount':10,'entry':'1','entry_cost_usd':'2','cost_quote':'1'} for p in pools}


def close(store,*,sold=20,residual=0,rate=None):
    record_sweep_exit(store,OWNER,POOL.token,sold,residual,3*10**18,POOL.quote,18,
        {'transactions':[{'hash':'synthetic_sweep','gas_fee_wei':1,'gas_usd':'.1'}]},
        {'usd':'2'} if rate is None else rate,pool_address=POOL.address)


def test_aggregate_sweep_close_accounts_all_lots_once(tmp_path):
    store=Store(tmp_path/'state.json');positions(store)
    close(store);close(store)
    assert closed_summary(store,OWNER)['value']=='1.9'
    assert len(store.data['closed_trades'])==1
    assert store.data['closed_trades']['synthetic_sweep']['tracked_raw']==20


def test_external_inventory_or_residual_cannot_fabricate_pnl(tmp_path):
    store=Store(tmp_path/'state.json');positions(store)
    close(store,sold=21)
    assert closed_summary(store,OWNER)['value'] is None
    store.data.pop('closed_trades')
    close(store,residual=1)
    assert closed_summary(store,OWNER)['missing']==1


def test_residual_is_not_duplicated_across_pools_and_invalidates_basis(tmp_path):
    store=Store(tmp_path/'state.json');positions(store)
    worker=Worker(store);worker.live=NS(owner=OWNER)
    assert worker.sweep_service()._account_sweep_token(POOL.token,12)
    rows=list(store.data['positions'].values())
    assert sum(r['amount'] for r in rows)==12
    assert all(r['entry_cost_usd'] is None and 'cost_quote' not in r for r in rows)
    assert worker.sweep_service()._account_sweep_token(POOL.token,0)
    assert not store.data['positions']
