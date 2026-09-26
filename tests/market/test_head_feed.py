import json
import threading
import time
from types import SimpleNamespace
import pytest
from dipbot.market.head_feed import HeadFeed, HeadSchedule


def head(n, h=None, parent=None):
    return {'number': hex(n), 'hash': '0x'+format(n if h is None else h, '064x'),
            'parentHash': '0x'+format(n-1 if parent is None else parent, '064x')}


def test_duplicate_gap_and_reorg_detection_are_bounded():
    feed = HeadFeed('wss://example.invalid')
    feed.connected = True
    assert feed.accept(head(1), now=0)
    assert not feed.accept(head(1), now=1)
    assert feed.snapshot().received_at == 0
    feed.accept(head(3), now=2)
    assert feed.snapshot().discontinuity == 1
    feed.accept(head(3, h=9), now=3)
    assert feed.snapshot().discontinuity == 2
    for n in range(4, 1000):
        feed.accept(head(n))
    assert feed.snapshot().number == 999 and not hasattr(feed, 'queue')


def test_schedule_falls_back_on_silence_and_never_defers_over_300ms():
    feed = HeadFeed('wss://example.invalid'); feed.connected = True
    feed.accept(head(1), now=10)
    h = feed.snapshot(); schedule = HeadSchedule()
    assert schedule.due(h, 10, 11)
    assert not schedule.consume(h, 10)
    assert not schedule.due(h, 10.1, 10.1)
    assert schedule.due(h, 10.3, 10.1)
    assert schedule.due(None, 10.11, 10.1)
    assert schedule.due(h, 12, 11)
    feed.accept(head(3), now=12)
    assert schedule.consume(feed.snapshot(),12)


@pytest.mark.parametrize('url',['http://example.com','ws://example.com','wss://user:password@example.com','wss://example.com/#x'])
def test_reject_insecure_or_credential_authority_urls(url):
    with pytest.raises(ValueError):
        HeadFeed(url)


def test_wrong_network_cannot_subscribe_and_stop_interrupts_retry():
    sent=[]
    class Socket:
        def __enter__(self): return self
        def __exit__(self,*args): pass
        def send(self, data): sent.append(json.loads(data)['method'])
        def recv(self, **kwargs): return json.dumps({'id':1,'result':'0x1'})
        def close(self): pass
    feed=HeadFeed('wss://example.invalid',connector=lambda *a,**kw: Socket()).start()
    deadline=time.monotonic()+2
    while feed.reconnects==0 and time.monotonic()<deadline:
        time.sleep(.01)
    feed.stop()
    assert feed.reconnects and sent==['eth_chainId']
    assert not feed.thread.is_alive() and feed.snapshot() is None


def test_local_websocket_subscription_reconnect_and_shutdown():
    from websockets.sync.server import serve
    sessions=[]
    def handler(ws):
        a=json.loads(ws.recv()); sessions.append(a['method'])
        ws.send(json.dumps({'id':a['id'],'result':'0x38'}))
        b=json.loads(ws.recv()); sessions.append(b['method'])
        assert b['params']==['newHeads']
        ws.send(json.dumps({'id':b['id'],'result':'sub'}))
        ws.send(json.dumps({'method':'eth_subscription','params':{'subscription':'sub','result':head(5)}}))
        time.sleep(.15)
    with serve(handler,'127.0.0.1',0) as server:
        thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start()
        feed=HeadFeed('ws://127.0.0.1:'+str(server.socket.getsockname()[1])).start()
        try:
            deadline=time.monotonic()+3
            while feed.head is None and time.monotonic()<deadline: time.sleep(.01)
            assert feed.head.number==5
            deadline=time.monotonic()+3
            while len(sessions)<4 and time.monotonic()<deadline: time.sleep(.01)
            assert sessions[:4]==['eth_chainId','eth_subscribe']*2
        finally:
            feed.stop(); server.shutdown(); thread.join(timeout=2)
        assert not feed.thread.is_alive()


def test_worker_deferred_reads_do_not_busy_spin_and_feed_stops(tmp_path, monkeypatch):
    import queue
    from dipbot.application.worker import Worker
    from dipbot.persistence.storage import Store
    clock = [0.0]
    monkeypatch.setattr('dipbot.application.worker.time.monotonic', lambda: clock[0])
    worker = Worker(Store(tmp_path/'state.json')); worker.running = True; worker.mode = 'PAPER'
    feed = HeadFeed('wss://example.invalid'); feed.connected = True; feed.accept(head(1), now=0)
    stopped = []
    feed.stop = lambda: stopped.append(True)
    worker.head_feed = feed
    starts = []
    class Commands:
        def get(self, timeout):
            assert timeout > 0
            clock[0] += timeout
            raise queue.Empty
    worker.commands = Commands()
    def observe():
        starts.append(clock[0])
        if len(starts) == 3:
            worker.quit_event.set()
    worker.observe = observe; worker.status = lambda: None
    worker.run()
    assert len(starts) == 3 and starts[1]-starts[0] >= .3 - 1e-9
    assert stopped == [True]
