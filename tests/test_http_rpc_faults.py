"""Real localhost HTTP transport faults; no upstream network or transaction signing."""
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json
import socket
import threading
import time
from decimal import Decimal as D
import pytest
import requests

ORIGINAL_REQUEST = requests.Session.request
from eth_abi import encode
from dipbot.chain import Chain,Pool,WBNB,USDT,address
from dipbot.worker import Worker
from dipbot.storage import Store


@pytest.fixture
def node(monkeypatch):
    state={'fault':None,'chain_id':56,'price':100,'height':100,'calls':[]}
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_POST(self):
            request=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            method=request['method'];state['calls'].append(method)
            if method not in ('eth_chainId','eth_getBlockByNumber','eth_call','eth_getCode'):
                self.send_response(405);self.end_headers();return
            fault=state['fault']
            if fault in (429,503):
                self.send_response(fault);self.end_headers();return
            if fault=='disconnect':
                self.connection.shutdown(socket.SHUT_RDWR);self.connection.close();return
            if fault=='timeout':time.sleep(.12)
            if method=='eth_chainId':value=hex(state['chain_id'])
            elif method=='eth_getBlockByNumber':
                height=state['height']
                value={'number':hex(height),'hash':'0x'+format(height,'064x'),
                       'parentHash':'0x'+format(height-1,'064x'),'transactions':[],
                       'timestamp':hex(int(time.time())-(120 if fault=='stale' else 0)),
                       'extraData':'0x'}
            elif method=='eth_getCode':value='0x6000'
            else:value='0x'+encode(['uint112','uint112','uint32'],[10**18,state['price']*10**18,0]).hex()
            payload=json.dumps({'jsonrpc':'2.0','id':request['id'],'result':value}).encode()
            try:
                self.send_response(200);self.send_header('Content-Length',str(len(payload)))
                self.end_headers();self.wfile.write(payload)
            except (BrokenPipeError,ConnectionResetError):pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    endpoint=f'http://127.0.0.1:{server.server_port}'
    def local_only(session,method,url,*args,**kwargs):
        if url!=endpoint or method.upper()!='POST':
            raise AssertionError('Only this test loopback server is permitted')
        session.trust_env=False
        kwargs['allow_redirects']=False
        return ORIGINAL_REQUEST(session,method,url,*args,**kwargs)
    monkeypatch.setattr(requests.Session,'request',local_only)
    try:yield state,endpoint
    finally:server.shutdown();server.server_close();thread.join(2)


def worker_at(tmp_path,endpoint):
    worker=Worker(Store(tmp_path/'state.json'));worker.mode='PAPER';worker.running=True
    worker.chain=Chain(endpoint,request_timeout=.05)
    worker.pool=Pool(address('0x'+'12'*20),'V2',address(USDT),address(WBNB),18,18,True)
    return worker


@pytest.mark.parametrize('fault',[429,503,'timeout','disconnect','stale'])
def test_persistent_http_fault_and_recovery_cannot_buy_old_dip(tmp_path,node,fault):
    state,endpoint=node;w=worker_at(tmp_path,endpoint)
    w.open_position=lambda:pytest.fail('No entry from the pre-outage baseline')
    w.observe();assert w.strategy.base==100
    state['fault']=fault
    for _ in range(12):w.observe()
    assert w.quote_unavailable and w.quote_failures==12 and not w.paper.position
    # Model a long absence without waiting hours. HTTP failures above are real.
    w.strategy.last_time-=3600
    state.update(fault=None,price=80,height=101)
    w.observe()
    assert not w.quote_unavailable and w.quote_failures==0 and w.strategy.base==80
    assert not any('send' in method.lower() for method in state['calls'])


def test_open_position_survives_disconnect_then_rechecks_exit(tmp_path,node):
    state,endpoint=node;w=worker_at(tmp_path,endpoint)
    w.paper.buy_quoted(D(100),D(1));w.strategy.bought(D(100))
    exits=[];w.close_position=exits.append
    state['fault']='disconnect'
    for _ in range(12):w.observe()
    assert w.paper.position==1 and not exits
    state.update(fault=None,price=110,height=101)
    w.observe()
    assert exits==['TAKE_PROFIT'] and not w.quote_unavailable


def test_network_identity_change_after_fault_blocks_recovery(tmp_path,node):
    state,endpoint=node;w=worker_at(tmp_path,endpoint)
    w.observe();state['fault']=503;w.observe()
    state.update(fault=None,chain_id=1)
    with pytest.raises(ValueError,match='не к BSC'):w.observe()
    assert not w.paper.position


def test_actual_worker_backoff_and_stop_during_rate_limit(tmp_path,node):
    state,endpoint=node;w=worker_at(tmp_path,endpoint)
    state['fault']=429;w.start()
    try:
        time.sleep(2.2)
        assert 1<=len(state['calls'])<=4
        w.stop_event.set()
        deadline=time.monotonic()+2
        while w.running and time.monotonic()<deadline:time.sleep(.02)
        assert not w.running and not w.paper.position
    finally:
        w.quit_event.set();assert w.wait(5000)
