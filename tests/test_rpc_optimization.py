import json
import queue
from types import SimpleNamespace
from dataclasses import replace
import pytest
from web3 import HTTPProvider
from dipbot.rpc import BscHTTPProvider
from dipbot.chain import Chain, WBNB, USDT, Pool, address
from dipbot.worker import Worker
from dipbot.storage import Store


@pytest.fixture
def provider(monkeypatch):
    clock = {'now': 100., 'network': '0x38', 'error': False}
    calls = []
    monkeypatch.setattr('dipbot.rpc.time.monotonic', lambda: clock['now'])
    def wire(self, method, payload):
        calls.append(method)
        request = json.loads(payload)
        result = {'jsonrpc':'2.0', 'id':request['id']}
        if clock['error']:
            result['error']={'code':-32000,'message':'synthetic'}
        else:
            result['result']=clock['network'] if method=='eth_chainId' else '0x00'
        return json.dumps(result).encode()
    monkeypatch.setattr(HTTPProvider, '_make_request', wire)
    return BscHTTPProvider('https://example.test', exception_retry_configuration=None), clock, calls


def test_only_network_identity_is_cached_and_expires(provider):
    p, clock, calls = provider
    for _ in range(3):p.make_request('eth_chainId', [])
    assert calls==['eth_chainId']
    for _ in range(2):
        p.make_request('eth_call', [])
        p.make_request('eth_getBlockByNumber', ['latest',False])
    assert calls.count('eth_call')==calls.count('eth_getBlockByNumber')==2
    clock['now']+=30
    clock['network']='0x1'
    assert p.make_request('eth_chainId', [])['result']=='0x1'
    p.make_request('eth_chainId', [])
    assert calls.count('eth_chainId')==3  # wrong network is never cached


def test_endpoint_change_and_error_invalidate_identity(provider):
    p, clock, calls = provider
    p.make_request('eth_chainId', [])
    p.endpoint_uri='https://second.example.test'
    p.make_request('eth_chainId', [])
    assert calls.count('eth_chainId')==2
    clock['error']=True
    p.make_request('eth_call', [])
    clock['error']=False
    p.make_request('eth_chainId', [])
    assert calls.count('eth_chainId')==3
    p.invalidate_network()
    p.make_request('eth_chainId', [])
    assert calls.count('eth_chainId')==4


@pytest.mark.parametrize('age', [31, -16])
def test_block_freshness_still_checked_on_fast_path(monkeypatch, age):
    c=object.__new__(Chain)
    c.w3=SimpleNamespace(provider=None, eth=SimpleNamespace(chain_id=56,
        get_block=lambda _: {'number':10,'timestamp':100-age}))
    monkeypatch.setattr('dipbot.chain.time.time',lambda:100)
    with pytest.raises(ValueError,match='устаревший'):
        c.check(force_network=False)


@pytest.mark.parametrize('liquidity,slot,message', [(None,None,'Multicall'),(0,(1,),'WAITING'),(1,(0,),'Пустая')])
def test_v3_failed_multicall_never_produces_price(monkeypatch,liquidity,slot,message):
    c=object.__new__(Chain);c.check=lambda **kw: 123
    def batch(chain, requests, block):
        assert block==123 and [r[2] for r in requests]==['liquidity','slot0']
        return [liquidity,slot]
    monkeypatch.setattr('dipbot.discovery.batch',batch)
    pool=Pool(address('0x'+'12'*20),'V3',address(USDT),address(WBNB),18,18,True,100)
    with pytest.raises(ValueError,match=message):c.price(pool)


@pytest.mark.parametrize('duration,expected',[(.03,[0,.1,.2]),(.2,[0,.2,.4])])
def test_scheduler_includes_request_time_without_backlog(tmp_path,monkeypatch,duration,expected):
    clock={'now':0.};starts=[]
    monkeypatch.setattr('dipbot.worker.time.monotonic',lambda:clock['now'])
    w=Worker(Store(tmp_path/'state.json'));w.running=True;w.interval=.1
    class Commands:
        def get(self,timeout):
            clock['now']+=timeout
            raise queue.Empty
    w.commands=Commands();w.status=lambda:None
    def observe():
        starts.append(clock['now']);clock['now']+=duration
        if len(starts)==3:w.quit_event.set()
    w.observe=observe
    w.run()
    assert starts==pytest.approx(expected)


def test_strict_check_forces_identity_but_price_checks_reuse_it(provider,monkeypatch):
    p,clock,calls=provider
    blocks=[]
    class Eth:
        @property
        def chain_id(self):return int(p.make_request('eth_chainId',[])['result'],16)
        def get_block(self, tag):
            blocks.append(tag)
            return {'timestamp':100,'number':123}
    c=object.__new__(Chain);c.w3=SimpleNamespace(provider=p,eth=Eth())
    monkeypatch.setattr('dipbot.chain.time.time',lambda:100)
    assert c.check()==c.check(force_network=False)==c.check()==123
    assert calls.count('eth_chainId')==2 and len(blocks)==3
    clock['network']='0x1'
    with pytest.raises(ValueError,match='не к BSC'):c.check()
