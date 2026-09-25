from types import SimpleNamespace
import time
import pytest
from requests import Response
from requests.exceptions import HTTPError
from dipbot.exit_reads import retry_read, ExitReadCancelled
from dipbot.worker import Worker
from dipbot.storage import Store
from dipbot.strategy import D
from dipbot.trader import UncertainTransaction
from test_autopair_dynamic import POOL
from test_app_autopair_flow import window
from test_audit_ui_modes import status


def http(code):
    response = Response(); response.status_code = code
    return HTTPError('https://secret.invalid', response=response)


def test_recovery_delays_and_redaction():
    calls=[]; waits=[]; states=[]
    def read(attempt):
        calls.append(attempt)
        if attempt < 2: raise http(503)
        return 42
    assert retry_read(read, cancelled=lambda:False, wait=lambda s:waits.append(s), notify=states.append)==42
    assert calls==[0,1,2] and waits==[.5,1.]
    assert states[-1] is None and states[0]['error']=='HTTPError'
    assert 'secret' not in str(states)


@pytest.mark.parametrize('error, count', [(http(503),4),(http(401),1),(ValueError('minOut'),1),(UncertainTransaction('pending'),1)])
def test_bounded_outage_and_nonretryable(error,count):
    calls=[]
    def read(i): calls.append(i); raise error
    with pytest.raises(type(error)):
        retry_read(read,cancelled=lambda:False,wait=lambda _:False,notify=lambda _:None)
    assert len(calls)==count


def test_stop_interrupts_wait():
    calls=[]
    def read(i): calls.append(i); raise TimeoutError()
    with pytest.raises(ExitReadCancelled):
        retry_read(read,cancelled=lambda:False,wait=lambda _:True,notify=lambda _:None)
    assert calls==[0]


def test_worker_backup_quote_preserves_position_until_success(tmp_path):
    w=Worker(Store(tmp_path/'state.json'));w.mode='PAPER';w.pool=POOL
    w.paper.buy(D(1),D(1));w.strategy.bought(D(1));w.read_price=lambda:D(1)
    w.stop_event=SimpleNamespace(is_set=lambda:False,wait=lambda _:False)
    calls=[]
    def quote(*args): calls.append('primary');raise http(503)
    w.chain=SimpleNamespace(quote=quote)
    w.backup_chain=SimpleNamespace(quote=lambda *args:10**18)
    def verify(): calls.append('verified backup');return D(1)
    w.backup_price=verify
    recorded=[]
    w.record_quote=lambda source,*args:recorded.append(source)
    w.close_position('TAKE_PROFIT')
    assert recorded==[w.backup_chain]
    assert calls==['primary','verified backup']
    assert w.paper.position==0 and w.exit_retry is None


def test_worker_exhaustion_preserves_position(tmp_path):
    w=Worker(Store(tmp_path/'state.json'));w.mode='PAPER';w.pool=POOL
    w.paper.buy(D(1),D(1));w.strategy.bought(D(1));w.read_price=lambda:D(1)
    w.stop_event=SimpleNamespace(is_set=lambda:False,wait=lambda _:False)
    def quote(*args):raise http(503)
    w.chain=SimpleNamespace(quote=quote)
    before=w.paper.position
    with pytest.raises(HTTPError):w.close_position('TIME_EXIT')
    assert w.paper.position==before and w.strategy.entry==1 and w.exit_retry is None


@pytest.mark.parametrize('state',['pending','prepared','reverted'])
def test_unknown_transaction_prevents_even_quote_retry(tmp_path,state):
    w=Worker(Store(tmp_path/'state.json'))
    w.store.data['operation']={'transactions':[{'status':state}]}
    with pytest.raises(UncertainTransaction):
        w.exit_read(lambda source:pytest.fail('Must not read or submit'))


def test_ui_exit_wait_overrides_execution_and_counts_down(window):
    w=window
    status(w,running=True,position='1',exit_retry={'error':'HTTPError','attempt':1,'limit':3,'retry_at':time.monotonic()+2})
    assert w.metrics['state'].text()=='EXIT RPC'
    assert 'выход ожидает RPC' in w.strategy_status.text()
    assert 'HTTPError' in w.strategy_status.text() and 'через' in w.strategy_status.text()


def test_stop_during_paper_retry_keeps_position_for_stop_handler(tmp_path):
    w=Worker(Store(tmp_path/'state.json'));w.mode='PAPER';w.pool=POOL
    w.paper.buy(D(1),D(1));w.strategy.bought(D(1));w.read_price=lambda:D(1)
    before=w.paper.position
    calls=[]
    def quote(*args):calls.append('quote');raise http(503)
    w.chain=SimpleNamespace(quote=quote)
    def wait(delay):
        if delay:
            w.stop_event.set();return True
        return False
    w.stop_event.wait=wait
    with pytest.raises(ExitReadCancelled):w.close_position('TIME_EXIT')
    assert calls==['quote'] and w.paper.position==before
    # The STOP loop clears the request and performs its own bounded close.
    w.stop_event.clear();w.chain.quote=lambda *args:10**18
    w.close_position('STOP')
    assert w.paper.position==0 and w.strategy.stopped


def test_pending_live_send_is_not_retried_by_worker(tmp_path):
    w=Worker(Store(tmp_path/'state.json'));w.mode='LIVE';w.pool=POOL
    calls=[]
    w.chain=SimpleNamespace(balance=lambda *args:10**18)
    def begin(_):w.store.data['operation']={'transactions':[]}
    def swap(*args,**kwargs):
        calls.append('send')
        w.store.data['operation']['transactions'].append({'status':'pending'})
        raise UncertainTransaction('unknown broadcast result')
    w.live=SimpleNamespace(owner='0x'+'34'*20,begin=begin,swap=swap)
    w.set_position(100,D(1));w.strategy.bought(D(1))
    with pytest.raises(UncertainTransaction):w.close_position('STOP_LOSS')
    assert calls==['send'] and w.position()['amount']==100
    with pytest.raises(UncertainTransaction):w.observe()
    assert calls==['send']


def test_backup_validation_failure_does_not_quote(tmp_path):
    w=Worker(Store(tmp_path/'state.json'));w.chain=object();w.backup_chain=object()
    w.stop_event.wait=lambda _:False
    def read(source):
        assert source is w.chain
        raise http(503)
    def verify():raise ValueError('Резервный RPC вернул другой пул')
    w.backup_price=verify
    with pytest.raises(ValueError,match='другой пул'):w.exit_read(read)


def test_live_becomes_uncertain_during_wait_no_second_read(tmp_path):
    w=Worker(Store(tmp_path/'state.json'));calls=[]
    w.store.data['operation']={'transactions':[]}
    def read(source):calls.append(1);raise http(503)
    def wait(_):
        w.store.data['operation']['transactions'].append({'status':'pending'})
        return False
    w.stop_event.wait=wait
    with pytest.raises(UncertainTransaction):w.exit_read(read)
    assert calls==[1]


def test_stop_wakes_actual_retry_wait():
    import threading
    waiting=threading.Event();stop=threading.Event();outcomes=[];reads=[]
    def read(i):reads.append(i);raise TimeoutError()
    def run():
        try:
            retry_read(read,cancelled=stop.is_set,wait=stop.wait,
                       notify=lambda state:waiting.set() if state else None,delays=(10,))
        except ExitReadCancelled:outcomes.append('cancelled')
    thread=threading.Thread(target=run);thread.start()
    try:
        assert waiting.wait(2)
        stop.set();thread.join(2)
        assert not thread.is_alive() and outcomes==['cancelled'] and reads==[0]
    finally:
        stop.set();thread.join(2)


def test_retry_ui_event_does_not_change_live_lock(window):
    w=window
    status(w,running=True,position='1')
    before=w.locked
    w.on_event('exit_retry',{'error':'Timeout','attempt':1,'limit':3,'retry_at':time.monotonic()+2})
    assert w.metrics['state'].text()=='EXIT RPC' and w.locked==before
    assert 'выход ожидает RPC' in w.strategy_status.text()
    w.on_event('exit_retry',None)
    assert w.exit_retry is None and w.locked==before


def test_stop_retains_rpc_reason_and_countdown(window):
    w=window;w.stop_pending=True
    w.on_event('exit_retry',{'error':'HTTPError','attempt':1,'limit':3,'retry_at':time.monotonic()+2})
    assert w.metrics['state'].text()=='STOPPING'
    assert 'Останавливается' in w.strategy_status.text()
    assert 'HTTPError' in w.strategy_status.text() and 'через' in w.strategy_status.text()
