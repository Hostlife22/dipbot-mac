from decimal import Decimal as D
import threading
import time
from types import SimpleNamespace
from dipbot.market.market_monitor import MarketMonitor, MarketSnapshot
from test_autopair_dynamic import POOL
from test_app_autopair_flow import window


def test_monitor_updates_while_executor_is_waiting_and_never_writes():
    calls=[]; published=threading.Event()
    class ReadOnly:
        price_block={'number':12}
        def restrict_to_reads(self): calls.append('restricted')
        def verify_pool(self,*args):
            assert calls==['restricted']
            return POOL
        def price(self,pool):
            calls.append('price'); published.set()
            return D(len(calls))
    monitor=MarketMonitor('synthetic',POOL,factory=lambda _:ReadOnly()).start()
    try:
        assert published.wait(2)
        deadline=time.monotonic()+2
        while len(calls)<3 and time.monotonic()<deadline: time.sleep(.01)
        snapshot=monitor.snapshot()
        assert snapshot.revision>=2 and snapshot.block==12
        assert calls==['restricted']+['price']*(len(calls)-1)
    finally: monitor.stop()
    assert not monitor.thread.is_alive() and monitor.snapshot() is None


def test_stale_or_failed_monitor_never_marks_price_fresh():
    monitor=MarketMonitor('synthetic',POOL)
    monitor.latest=MarketSnapshot(D(1),10,1,12,POOL.address,POOL.quote)
    assert monitor.snapshot(10.5) is not None
    assert monitor.snapshot(10.6) is None
    monitor.error_type='TimeoutError'
    assert monitor.snapshot(10.1) is None


def test_ui_updates_during_busy_executor_without_changing_signal_snapshot(window):
    w=window
    w.mode.setCurrentText('PAPER'); w.worker.mode='PAPER'
    w.selection_ready=True; w.pool_input.setText(POOL.address); w.selection_ready=True
    w.busy=True; w.worker.current_price=D(100)
    monitor=MarketMonitor('synthetic',POOL)
    now=time.monotonic()
    monitor.latest=MarketSnapshot(D(95),now,1,12,POOL.address,POOL.quote)
    w.worker.execution_monitor=monitor
    try:
        w.update_quote_age()
        assert w.last_price==95 and w.worker.current_price==100
        assert w.last_quote_at==now
        count=len(w.chart.values)
        w.update_quote_age()
        assert len(w.chart.values)==count
        monitor.latest=MarketSnapshot(D(1),now,2,12,'0x'+'ff'*20,POOL.quote)
        w.update_quote_age()
        assert w.last_price==95
    finally:
        w.worker.execution_monitor=None; w.busy=False


def test_late_monitor_cannot_roll_display_back_to_older_block(window):
    w = window
    w.mode.setCurrentText('PAPER'); w.worker.mode = 'PAPER'
    w.pool_input.setText(POOL.address); w.selection_ready = True
    monitor = MarketMonitor('synthetic', POOL)
    monitor.latest = MarketSnapshot(D(95), time.monotonic(), 1, 12, POOL.address, POOL.quote)
    w.worker.execution_monitor = monitor
    try:
        w.on_event('price_context', {'source':'BSC', 'quote':POOL.quote, 'block':13})
        w.on_event('price', '101')
        assert w.last_price == 101 and w.market_block == 13
        assert w.chart.values[-1] == 101
        assert w.metrics['price'].text() == w.display_price(101)
        monitor.latest = MarketSnapshot(D(102), time.monotonic(), 2, 14, POOL.address, POOL.quote)
        w.update_quote_age()
        assert w.last_price == 102 and w.market_block == 14
    finally:
        w.worker.execution_monitor = None
