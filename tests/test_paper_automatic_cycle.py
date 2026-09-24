"""Deterministic PAPER signal-to-execution checks; no market-parity claim."""
from types import SimpleNamespace
import pytest
from dipbot.worker import Worker
from dipbot.storage import Store
from dipbot.strategy import D
from test_worker import config
from test_recovery_audit import POOL


@pytest.mark.parametrize('exit_price,reason,stopped', [
    ('0.99', 'TAKE_PROFIT', False), ('0.90', 'STOP_LOSS', True)])
def test_paper_dip_signal_executes_buy_then_exit(tmp_path, monkeypatch, exit_price, reason, stopped):
    worker = Worker(Store(tmp_path/'state.json'))
    clock = {'now': 1.0, 'price': D('1')}
    monkeypatch.setattr('dipbot.worker.time.monotonic', lambda: clock['now'])
    worker.pool = POOL
    worker.chain = SimpleNamespace(verify_pool=lambda *args: POOL, price=lambda _: clock['price'])
    events = []
    worker.log.connect(events.append)
    data = config('PAPER') | {'token': POOL.token, 'pool': POOL.address, 'router': 'V2'}
    worker.command('start', data)
    for price in ('1', '0.96'):
        clock['price'] = D(price)
        clock['now'] += 0.1
        worker.observe()
    assert worker.paper.position > 0 and worker.strategy.entry == D('0.96')
    clock['price'] = D(exit_price)
    clock['now'] += 0.1
    worker.observe()
    assert not worker.paper.position and worker.strategy.entry is None
    assert worker.strategy.stopped is stopped
    assert worker.running is not stopped
    assert sum('PAPER BUY:' in line for line in events) == 1
    assert any('SELL: '+reason in line for line in events)
    worker.running = False
    worker.command('start', data)
    assert worker.running and not worker.strategy.stopped
    assert worker.strategy.entry is None


def test_paper_gap_resets_base_without_buy(tmp_path, monkeypatch):
    worker = Worker(Store(tmp_path/'state.json')); worker.pool = POOL
    state = {'time': 1.0, 'price': D('1')}
    monkeypatch.setattr('dipbot.worker.time.monotonic', lambda: state['time'])
    worker.chain = SimpleNamespace(verify_pool=lambda *args: POOL, price=lambda _: state['price'])
    worker.command('start', config('PAPER') | {'token': POOL.token, 'pool': POOL.address})
    worker.observe()
    state.update(time=2.0, price=D('0.9'))
    worker.observe()
    assert not worker.paper.position and worker.strategy.base == D('0.9')
