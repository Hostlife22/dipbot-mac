from tests.support.markets import BASE, TARGET, POOL, candidate, route
from dataclasses import replace
from types import SimpleNamespace
import pytest
from dipbot.market.autopair import Candidate, choose
from dipbot.market.chain import Chain, Pool, WBNB, USDT, ETH, address, ZERO
from dipbot.persistence.storage import Store
from dipbot.application.worker import Worker
from dipbot.market.routes import conversion_specs
from dipbot.persistence import dynamic





@pytest.mark.parametrize('items,status,fee', [
    ([], 'NOT_FOUND', None),
    ([candidate(False, 0)], 'PENDING', 500),
    ([candidate(False, 0), candidate(True, 5, fee=2500)], 'RESOLVED', 2500),
    ([candidate(True, 5), candidate(True, 8, fee=2500)], 'RESOLVED', 2500),
    ([candidate(True, 8), candidate(True, 8, fee=2500)], 'RESOLVED', 500),
    ([candidate(), candidate(name='OTHER')], 'AMBIGUOUS', None),
    ([candidate(), candidate(router='V2')], 'AMBIGUOUS', None),
    ([candidate(False, 0), candidate(False, 0, name='OTHER')], 'AMBIGUOUS', None),
    ([candidate(False, 0, name='OTHER'), candidate(True, 5)], 'RESOLVED', 500),
])
def test_native_selection_vectors(items, status, fee):
    # 0x140858fd0: ready subset or all; route key; strict >; status branches.
    actual, selected = choose(items)
    assert actual == status
    assert (selected.pool.fee if selected else None) == fee


@pytest.mark.parametrize('router,score', [('V2', 21), ('V3', 123), ('V3', 0)])
def test_discovery_includes_pending_and_uses_raw_liquidity(router, score):
    chain = object.__new__(Chain)
    chain.check = lambda: 42
    pool = replace(POOL, router=router, fee=500 if router=='V3' else 0)
    checks = []
    def verify(*args, **kw):
        checks.append(kw)
        return pool
    chain.verify_pool = verify
    chain.call = lambda addr, abi, method, *args, **kw: {
        'getPair': POOL.address, 'getPool': POOL.address,
        'getReserves': (3,7,0), 'liquidity': score}[method]
    rows = chain.discover_candidates(TARGET, BASE, router, 'BASE')
    assert len(rows)==1  # repeated factory addresses deduplicated
    assert rows[0].liquidity_score==score and rows[0].ready==(score>0)
    assert checks == [{'require_liquidity': False}]


@pytest.mark.parametrize('outcome', ['timeout', 'stop', 'pending', 'resolved'])
def test_worker_invalidates_previous_selection_and_obeys_resolution(tmp_path, outcome):
    worker=Worker(Store(tmp_path/'state.json'));worker.pool=POOL
    events=[];worker.event.connect(lambda name,value: events.append((name,value)))
    def discover(*_):
        if outcome=='timeout': raise TimeoutError()
        if outcome=='stop': worker.stop_event.set()
        return [candidate(outcome!='pending', 0 if outcome=='pending' else 100)]
    from dipbot.market.discovery import Resolution
    def resolve(raw,catalogs):
        rows=discover()
        state,selected=choose(rows)
        return Resolution(state,tuple(rows),selected,raw)
    worker.chain=SimpleNamespace(resolve_address=resolve)
    chosen=[];worker.select_pool=lambda p,**kwargs: chosen.append(p)
    data={'token':TARGET, 'router':'V3', 'quote':'WBNB'}
    if outcome=='timeout':
        with pytest.raises(TimeoutError): worker.command('discover',data)
    else: worker.command('discover',data)
    assert worker.pool is None
    assert bool(chosen)==(outcome=='resolved')
    assert events[0]==('pools',[])




@pytest.mark.parametrize('mode', ['direct_v2','direct_v3','via_usdt_v3','via_eth_v3'])
def test_verified_preferences_survive_restart_and_reverse_path(tmp_path, mode):
    store=Store(tmp_path/'state.json')
    record=dynamic.upsert(store, POOL, route(mode), 100)
    loaded=Store(store.path)
    pref=dynamic.preference(loaded, BASE, 'V3')
    expected_fee=500 if mode=='direct_v2' else 2500
    assert pref=={'converter_mode':mode,'converter_fee':expected_fee}
    buy=conversion_specs(WBNB,BASE,pref)[0]
    sell=conversion_specs(BASE,WBNB,pref)[0]
    assert sell==(buy[0],buy[1][::-1],buy[2][::-1])
    assert record['name'] in dynamic.catalog(loaded,'V3')
    assert record['name'] not in dynamic.catalog(loaded,'V2')


def test_legacy_migration_recheck_two_routers_update_remove(tmp_path):
    store=Store(tmp_path/'state.json');store.data['dynamic_profiles']={'OLD':BASE}
    assert dynamic.catalog(store,'V2')['OLD']==BASE
    a=dynamic.upsert(store,POOL,route(),100)
    b=dynamic.upsert(store,replace(POOL,router='V2',fee=0),route('direct_v2'),200)
    assert a['name']!=b['name'] and 'OLD' not in store.data['dynamic_profiles']
    again=dynamic.upsert(store,POOL,route('via_eth_v3'),50)
    assert again['created_at']==a['created_at'] and again['name']==a['name']
    with pytest.raises(ValueError,match='router'): dynamic.preference(store,BASE)
    dynamic.remove(store,a['name'])
    loaded=Store(store.path)
    assert len(dynamic.records(loaded))==1 and b['name'] in dynamic.catalog(loaded,'V2')
    assert dynamic.preference(loaded,BASE,'V3') is None


@pytest.mark.parametrize('action',['upsert','remove'])
def test_registry_disk_failure_rolls_back_memory_and_preserves_old_disk(tmp_path,action):
    store=Store(tmp_path/'state.json');record=dynamic.upsert(store,POOL,route(),100)
    before=store.path.read_bytes()
    def fail(): raise OSError('disk full')
    store.save=fail
    with pytest.raises(OSError):
        if action=='upsert': dynamic.upsert(store,POOL,route('via_eth_v3'),20)
        else: dynamic.remove(store,record['name'])
    assert store.path.read_bytes()==before and store.data==Store(store.path).data


@pytest.mark.parametrize('bad',[None,[],{'version':2,'records':{}},{'version':1,'records':{'bad':{}}}])
def test_invalid_registry_blocks_use(tmp_path,bad):
    store=Store(tmp_path/'state.json');store.data['dynamic_registry']=bad
    with pytest.raises(ValueError): dynamic.catalog(store,'V3')


def test_preferred_dynamic_bridge_reaches_live_route_builder(tmp_path):
    from dipbot.execution.trader import LiveTrader
    store=Store(tmp_path/'state.json');path=route('via_eth_v3')
    dynamic.upsert(store,POOL,path,100)
    helper=object.__new__(LiveTrader);helper.store=store;helper.trade_router='V3'
    helper.chain=SimpleNamespace(find_pools=lambda target,quote: [p for p in path if p.token==target and p.quote==quote],
                                 quote_route=lambda path,amount,**kw: amount)
    assert helper.conversion_route(WBNB,BASE,10000)==path


def test_original_sort_breaks_equal_liquidity_ties_by_fee_then_address():
    from dipbot.market.autopair import ordered
    a=candidate(score=100,fee=2500)
    b=candidate(score=100,fee=500)
    c=replace(b,pool=replace(b.pool,address=address('0x'+'01'*20)))
    assert ordered([a,b,c])==[c,b,a]
    assert choose(ordered([a,b,c]))==('RESOLVED',c)


def test_changed_router_cannot_execute_previous_selection(tmp_path):
    from dipbot.domain.strategy import Settings
    from dataclasses import asdict
    worker=Worker(Store(tmp_path/'state.json'));worker.pool=POOL
    worker.chain=SimpleNamespace()
    with pytest.raises(ValueError,match='Router'):
        worker.configure({'mode':'PAPER','settings':{k:str(v) for k,v in asdict(Settings()).items() if k!='max_gap'},
                          'interval':.103,'token':TARGET,'pool':POOL.address,'router':'V2'})


def test_sweep_uses_dynamic_base_router_and_restores_context(tmp_path):
    worker=Worker(Store(tmp_path/'state.json'));dynamic.upsert(worker.store,POOL,route('via_eth_v3'),10)
    worker.mode='LIVE';worker.live=SimpleNamespace(owner=address('0x'+'34'*20),trade_router='V2')
    worker.pool=replace(POOL,router='V2',fee=0)
    worker.chain=SimpleNamespace(balance=lambda token,owner: 100 if token==BASE else 0)
    contexts=[]
    worker.live.conversion_route=lambda *args: contexts.append(worker.live.trade_router)
    worker.live.begin=lambda *_: None
    worker.live.convert=lambda *args: contexts.append(worker.live.trade_router)
    worker.live.finish=lambda:None
    worker.command=lambda *args:None
    worker.sweep()
    assert contexts==['V3','V3'] and worker.live.trade_router=='V2'


def test_remove_profile_checks_saved_position_before_mutation(tmp_path):
    worker=Worker(Store(tmp_path/'state.json'));row=dynamic.upsert(worker.store,POOL,route(),100)
    owner=address('0x'+'34'*20)
    worker.store.data['positions']={owner.lower()+':'+POOL.address.lower():{'pool':{'quote':BASE}}}
    worker.chain=SimpleNamespace(balance=lambda *args:0)
    with pytest.raises(ValueError,match='сохранённую'):
        worker.command('remove_profile',{'symbol':row['name'],'wallet':owner})
    assert dynamic.records(worker.store)


def test_sweep_rpc_failure_restores_router_and_sends_nothing(tmp_path):
    worker=Worker(Store(tmp_path/'state.json'));dynamic.upsert(worker.store,POOL,route(),100)
    worker.live=SimpleNamespace(owner=address('0x'+'34'*20),trade_router='V2',
                                begin=lambda _:pytest.fail('RPC failure must not begin trade'))
    worker.chain=SimpleNamespace(balance=lambda token,owner:100 if token==BASE else 0)
    def fail(*_): raise TimeoutError()
    worker.live.conversion_route=fail
    reports=[]
    worker.event.connect(lambda name,payload: reports.append(payload) if name=='sweep_report' else None)
    worker.sweep()
    assert worker.live.trade_router=='V2'
    assert BASE in reports[0]['failed'] and BASE in reports[0]['remaining']


def test_wrong_quote_from_factory_is_rejected():
    chain=object.__new__(Chain);chain.check=lambda:1
    chain.call=lambda *args,**kw:POOL.address
    chain.verify_pool=lambda *args,**kw:replace(POOL,quote=address(WBNB))
    with pytest.raises(ValueError,match='другого маршрута'):
        chain.discover_candidates(TARGET,BASE,'V3','BASE')


def test_profile_route_cannot_point_to_another_base(tmp_path):
    store=Store(tmp_path/'state.json')
    with pytest.raises(ValueError,match='не соответствует'):
        dynamic.upsert(store,replace(POOL,quote=address(USDT)),route(),100)
    assert not store.path.exists()


def test_add_rechecks_trading_liquidity_before_registry_write(tmp_path):
    worker=Worker(Store(tmp_path/'state.json'));worker.pool=POOL
    def dry(*_): raise ValueError('WAITING: liquidity disappeared')
    worker.chain=SimpleNamespace(verify_pool=dry)
    with pytest.raises(ValueError,match='WAITING'): worker.command('add_profile',{})
    assert not worker.store.data.get('dynamic_registry')
