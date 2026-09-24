from dataclasses import asdict, replace
from types import SimpleNamespace
import json
import pytest
from web3 import Web3
from dipbot import discovery, preferences, dynamic, wallet_registry
from dipbot.chain import Chain, Pool, WBNB, USDT, ETH, V2_FACTORY, POOL_ABI, address
from dipbot.autopair import Candidate
from dipbot.storage import Store
from dipbot.worker import Worker
from dipbot.trader import LiveTrader, UncertainTransaction
from tools.audit_native import Decoder

OWNER=address('0x'+'34'*20)
TOKEN=address('0x'+'ab'*20)
BASE=address('0x'+'cd'*20)
POOL=Pool(address('0x'+'12'*20),'V2',TOKEN,address(WBNB),18,18,True)


def test_large_constants_decode_exactly():
    # MSB-first base 2**31 limbs, independent fixture 2**62 + 2**31 + 3.
    assert Decoder(b'g\x03\x01\x01\x03').read()==2**62+2**31+3
    assert Decoder(b'G\x02\x01\x00').read()==-2**31
    with pytest.raises(ValueError): Decoder(b'g\x04\x01').read()


def test_multicall_encodes_pins_and_distinguishes_failed_reads():
    web3=Web3();calls=[]
    raw=[(True,web3.codec.encode(['uint128'],[123])),(False,b'')]
    def aggregate(items):
        calls.extend(items)
        def call(**kw):
            assert kw=={'block_identifier':42}
            return raw
        return SimpleNamespace(call=call)
    def contract(addr,abi):
        if addr==discovery.MULTICALL: return SimpleNamespace(functions=SimpleNamespace(aggregate3=aggregate))
        return web3.eth.contract(address=address(addr),abi=abi)
    chain=SimpleNamespace(w3=web3,contract=contract)
    req=[discovery.request(POOL.address,POOL_ABI,'liquidity')]*2
    assert discovery.batch(chain,req,42)==[123,None]
    assert all(c[1] for c in calls)
    raw.pop()
    with pytest.raises(ValueError,match='неполный'): discovery.batch(chain,req,42)


@pytest.mark.parametrize('kind', ['token','pool','empty_pool','catalog','no_code','unsupported'])
def test_resolver_address_kinds(monkeypatch,kind):
    catalog={'V2':{'WBNB':address(WBNB)}}
    chain=SimpleNamespace(check=lambda:42,decimals=lambda token:18,
        verify_pool=lambda *args,**kw:POOL,
        w3=SimpleNamespace(eth=SimpleNamespace(get_code=lambda *args,**kw:b'' if kind=='no_code' else b'code')))
    if kind in ('pool','empty_pool','unsupported'):
        answers=[[V2_FACTORY,TOKEN,address(WBNB)],[(0,1,0) if kind=='empty_pool' else (10,20,0)]]
        if kind=='unsupported': catalog={'V2':{}}
        raw=POOL.address
    elif kind=='token':
        answers=[[None]*3,[POOL.address],[V2_FACTORY,TOKEN,address(WBNB),(10,20,0)]];raw=TOKEN
    else:
        answers=[[None]*3];raw=WBNB if kind=='catalog' else TOKEN
    monkeypatch.setattr(discovery,'batch',lambda *args: answers.pop(0))
    result=discovery.resolve(chain,raw,catalog)
    expected={'token':'RESOLVED','pool':'RESOLVED','empty_pool':'PENDING','catalog':'CATALOG_TOKEN',
              'no_code':'INVALID_CONTRACT','unsupported':'UNSUPPORTED_POOL'}[kind]
    assert result.state==expected
    if result.selected: assert result.target==TOKEN


def test_bad_canonical_pool_cannot_resolve(monkeypatch):
    chain=SimpleNamespace(check=lambda:1,w3=SimpleNamespace(eth=SimpleNamespace(get_code=lambda *a,**k:b'x')))
    def reject(*a,**k): raise ValueError('forged')
    chain.verify_pool=reject
    monkeypatch.setattr(discovery,'batch',lambda *a:[V2_FACTORY,TOKEN,WBNB])
    assert discovery.resolve(chain,POOL.address,{'V2':{'WBNB':WBNB}}).state=='UNSUPPORTED_POOL'


def test_late_discovery_cannot_select_after_new_input(tmp_path):
    worker=Worker(Store(tmp_path/'state.json'));worker.discovery_generation=1
    def resolve(*_):
        worker.discovery_generation=2
        c=Candidate(POOL,'WBNB',True,100)
        return discovery.Resolution('RESOLVED',(c,),c,TOKEN)
    worker.chain=SimpleNamespace(resolve_address=resolve)
    worker.command('discover',{'token':TOKEN,'router':'V2','quote':'WBNB','generation':1})
    assert worker.pool is None and not worker.store.data.get('last_pool')


def test_late_verification_cannot_select_after_new_input(tmp_path):
    worker=Worker(Store(tmp_path/'state.json'));worker.discovery_generation=1
    def verify(*_): worker.discovery_generation=2; return POOL
    worker.chain=SimpleNamespace(verify_pool=verify)
    worker.select_pool(POOL,generation=1)
    assert worker.pool is None


def test_add_sample_eth_fallback_and_no_signing(tmp_path,monkeypatch):
    worker=Worker(Store(tmp_path/'state.json'));worker.pool=replace(POOL,quote=BASE)
    seen=[]
    route=[replace(POOL,router='V3',quote=address(WBNB),token=address(ETH),fee=100),
           replace(POOL,router='V3',quote=address(ETH),token=BASE,fee=500)]
    def select(self,src,dest,amount):
        seen.append((amount,self.converter_preference['converter_mode']))
        if len(seen)==1: raise ValueError('no USDT route')
        return route
    monkeypatch.setattr(LiveTrader,'conversion_route',select)
    worker.chain=SimpleNamespace(verify_pool=lambda *args:worker.pool,
        quote_route=lambda route,amount,**kwargs:amount*99//100,symbol=lambda _:b'My Token\0')
    worker.command('add_profile',{})
    assert seen==[(10**15,'via_usdt_v3'),(10**15,'via_eth_v3')]
    row=next(iter(dynamic.records(Store(worker.store.path)).values()))
    assert row['name']=='MYTOKEN' and row['roundtrip_loss_bps']==199
    assert row['converter_mode']=='via_eth_v3'


def test_add_does_not_mask_rpc_timeout_with_eth_fallback(tmp_path,monkeypatch):
    worker=Worker(Store(tmp_path/'state.json'));worker.pool=replace(POOL,quote=BASE)
    worker.chain=SimpleNamespace(verify_pool=lambda *args:worker.pool)
    def fail(*args): raise TimeoutError()
    monkeypatch.setattr(LiveTrader,'conversion_route',fail)
    with pytest.raises(TimeoutError): worker.command('add_profile',{})
    assert not worker.store.data.get('dynamic_registry')


def test_windows_public_ui_migration_keeps_amounts_and_excludes_secrets():
    result=preferences.from_windows_ui({'trade_router':'v3','pair':'USDT',
        'trade':{'amount_wbnb':'0.05','dip_pct':'4'},'pair_amounts':{'V3:USDT':'7','V2:WBNB':'0.1'},
        'PRIVATE_KEY':'synthetic secret','rpc':'synthetic secret'})
    assert result['settings']['amount']=='7' and result['settings']['dip']=='4'
    assert result['selection']=={'router':'V3','pair':'USDT'}
    assert result['pair_amounts']['V2:WBNB']=='0.1'
    assert 'secret' not in json.dumps(result)
    with pytest.raises(ValueError): preferences.from_windows_ui({'pair_amounts':{'V2:WBNB':'NaN'}})


def test_wallet_registry_isolated_and_route_fallback(tmp_path):
    store=Store(tmp_path/'state.json')
    wallet_registry.register(store,OWNER,POOL,'WBNB')
    assert not wallet_registry.records(store,address('0x'+'56'*20))
    row=wallet_registry.records(Store(store.path),OWNER)[TOKEN.lower()]
    a=Candidate(POOL,'WBNB',True,10);b=replace(a,liquidity_score=20)
    wrong=replace(b,pair_name='OTHER',liquidity_score=30)
    result=discovery.Resolution('AMBIGUOUS',(a,wrong,b))
    assert wallet_registry.choose_registered(result,row)==b
    assert wallet_registry.choose_registered(discovery.Resolution('PENDING',(replace(a,ready=False),)),row) is None


def test_sweep_registered_route_refresh_and_target_residuals(tmp_path):
    worker=Worker(Store(tmp_path/'state.json'));wallet_registry.register(worker.store,OWNER,POOL,'WBNB')
    newpool=replace(POOL,address=address('0x'+'78'*20));c=Candidate(newpool,'WBNB',True,100)
    balances={TOKEN:200};swaps=[]
    worker.live=SimpleNamespace(owner=OWNER,begin=lambda _:None,finish=worker.store.save)
    def swap(pool,*args,**kwargs): swaps.append(pool);balances[TOKEN]=1
    worker.live.swap=swap
    worker.chain=SimpleNamespace(balance=lambda token,owner:balances.get(token,0),
        resolve_address=lambda *args:discovery.Resolution('RESOLVED',(c,),c,TOKEN),
        verify_pool=lambda *args:newpool,quote=lambda *args:100)
    reports=[];worker.event.connect(lambda name,p:reports.append(p) if name=='sweep_report' else None)
    worker.sweep()
    assert swaps==[newpool] and reports[0]['remaining'][TOKEN]==1


def test_sweep_balance_error_is_unknown_not_zero(tmp_path):
    worker=Worker(Store(tmp_path/'state.json'));wallet_registry.register(worker.store,OWNER,POOL,'WBNB')
    worker.live=SimpleNamespace(owner=OWNER,begin=lambda _:pytest.fail('Must not submit'))
    def balance(token,owner):
        if token==TOKEN: raise TimeoutError()
        return 0
    worker.chain=SimpleNamespace(balance=balance)
    reports=[];worker.event.connect(lambda name,p:reports.append(p) if name=='sweep_report' else None)
    worker.sweep()
    assert TOKEN in reports[0]['failed'] and TOKEN in reports[0]['unknown']


def test_sweep_base_order_groups_aliases_and_unwraps_wbnb_last():
    rows = {'zeta':BASE, 'WBNB':WBNB, 'alpha':TOKEN, 'ALIAS':BASE}
    assert wallet_registry.ordered_bases(rows) == [
        ('alpha',TOKEN), ('zeta',BASE), ('WBNB',address(WBNB))]


@pytest.mark.parametrize('payload', [None, {'trade':None}, {'trade_router':42}, {'pair_amounts':[]}])
def test_windows_ui_migration_rejects_malformed_shapes(payload):
    with pytest.raises(ValueError): preferences.from_windows_ui(payload)
