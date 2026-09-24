"""Multiple targets, process death at final accounting, and malformed preferences."""
from dataclasses import replace
import json
import subprocess
import sys
from types import SimpleNamespace
import pytest
from dipbot.chain import address
from dipbot.storage import Store
from dipbot.strategy import D
from dipbot.trader import LiveTrader, UncertainTransaction
from dipbot import preferences
from test_recovery_audit import setup_worker, POOL, OWNER
from test_process_recovery import SCRIPT


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


def test_multi_target_preflight_error_does_not_skip_other_target(tmp_path):
    worker,other,balances,sent,reports=multi_worker(tmp_path)
    def quote(pool,*args):
        if pool.token==POOL.token:raise TimeoutError('synthetic')
        return 190
    worker.chain.quote=quote;worker.sweep()
    assert len(sent)==1 and other.token in sent[0]
    assert reports[0]['failed']==[POOL.token] and reports[0]['remaining'][POOL.token]==200
    positions=Store(worker.store.path).data['positions']
    assert len(positions)==1 and next(iter(positions.values()))['pool']['token']==POOL.token


def test_stop_after_first_receipt_accounts_first_and_keeps_second(tmp_path):
    worker,other,balances,sent,reports=multi_worker(tmp_path)
    def swap(pool,*a,**k):
        balances[pool.token]=0;worker.stop_event.set()
    worker.live.swap=swap;worker.sweep()
    assert len(sent)==1 and len(reports)==1
    assert reports[0]['status']=='stopped' and reports[0]['sold']==[POOL.token]
    assert other.token in reports[0]['unknown']
    assert worker.strategy.entry is None
    positions=Store(worker.store.path).data['positions']
    assert len(positions)==1 and next(iter(positions.values()))['pool']['token']==other.token


def test_uncertain_first_target_prevents_second_send(tmp_path):
    worker,other,balances,sent,reports=multi_worker(tmp_path)
    def swap(*a,**k):raise UncertainTransaction('synthetic lost response')
    worker.live.swap=swap
    with pytest.raises(UncertainTransaction):worker.sweep()
    assert len(sent)==1 and len(Store(worker.store.path).data['positions'])==2
    assert len(reports)==1 and reports[0]['status']=='interrupted'
    assert reports[0]['sold']==[]
    assert {POOL.token,other.token} <= set(reports[0]['unknown'])


def test_accounting_write_failure_blocks_next_target_and_restart(tmp_path):
    worker,other,balances,sent,reports=multi_worker(tmp_path)
    trader=object.__new__(LiveTrader)
    trader.store=worker.store;trader.owner=OWNER;trader.operation=None
    def swap(pool,*args,**kwargs):
        sent.append(pool.token)
        balances[pool.token]=0
        trader.operation['transactions'].append({'status':'confirmed','hash':'synthetic'})
        worker.store.save()
        def fail():raise OSError('synthetic final accounting write failure')
        worker.store.save=fail
    trader.swap=swap;worker.live=trader
    with pytest.raises(OSError):worker.sweep()
    assert sent==[POOL.token] and len(reports)==1
    assert reports[0]['status']=='interrupted'
    assert reports[0]['needs_reconciliation'] is True
    assert reports[0]['sold']==[]
    assert worker.store.data['operation']['transactions'][0]['status']=='confirmed'
    restarted=Store(worker.store.path)
    assert len(restarted.data['positions'])==2
    assert restarted.data['operation']['transactions'][0]['status']=='confirmed'
    with pytest.raises(UncertainTransaction):trader.begin('duplicate')


@pytest.mark.parametrize('boundary,code,committed',[('before_commit',80,False),('after_commit',81,True)])
def test_process_death_at_final_accounting_is_atomic(tmp_path,boundary,code,committed):
    script=SCRIPT.replace("t.begin('synthetic process crash')", "store.data['positions']={'synthetic':{'amount':200}}\nstore.save()\nt.begin('synthetic process crash')")
    script=script.replace('os._exit(73)',r'''
original_replace=os.replace
def replace(src,dst):
 if boundary=='before_commit':os._exit(80)
 original_replace(src,dst)
 os._exit(81)
store.data['positions']={}
os.replace=replace
t.finish()
''')
    path=tmp_path/'state.json'
    result=subprocess.run([sys.executable,'-c',script,str(path),boundary],capture_output=True,timeout=20)
    assert result.returncode==code,result.stderr.decode()
    store=Store(path)
    if committed:
        assert not store.data.get('operation') and not store.data['positions']
        assert store.data['history'][0]['transactions'][0]['status']=='confirmed'
    else:
        assert store.data['positions'] and store.data['operation']['transactions'][0]['status']=='confirmed'
        trader=object.__new__(LiveTrader);trader.store=store
        with pytest.raises(UncertainTransaction):trader.begin('duplicate')


@pytest.mark.parametrize('change',[
    {'version':2}, {'gas':'NaN'}, {'interval':'Infinity'},
    {'selection':{'router':'V3','pair':'USDT'},'pair_amounts':{'V3:USDT':'NaN'}},
    {'selection':{'router':'V3','pair':'USDT'},'pair_amounts':{'V3:USDT':'-1'}},
])
def test_invalid_preferences_save_keeps_existing_file(tmp_path,change):
    store=Store(tmp_path/'state.json');valid=preferences.from_windows_ui({})
    preferences.save(store,valid);before=store.path.read_bytes()
    with pytest.raises(ValueError):preferences.save(store,{**valid,**change})
    assert store.path.read_bytes()==before and store.data['ui_preferences']==valid


def test_truncated_store_blocks_restart_without_overwriting(tmp_path):
    path=tmp_path/'state.json';path.write_text('{"operation":')
    with pytest.raises(json.JSONDecodeError):Store(path)
    assert path.read_text()=='{"operation":'
