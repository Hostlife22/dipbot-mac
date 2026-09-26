import time
from decimal import Decimal as D
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from dipbot.worker import Worker
from dipbot.storage import Store
from test_autopair_dynamic import POOL


def worker(tmp_path):
    w = Worker(Store(tmp_path/'watch.json'))
    w.mode = 'PAPER'
    w.pool = POOL
    w.paper.position = D(2)
    w.paper.cost = D(1)
    w.paper_usd['entry'] = D(1)
    w.chain = SimpleNamespace(exit_quote=Mock(return_value=2*10**POOL.quote_decimals))
    def read():
        w.price_time = time.monotonic()
        return D(1)
    w.read_price = read
    w.paper_operation_cost = lambda: D('.01')
    w.rates.update(POOL.quote, D(1), time.monotonic())
    w.strategy.observe = Mock(side_effect=AssertionError('Must never run strategy'))
    w.open_position = w.close_position = Mock(side_effect=AssertionError('Must never trade'))
    return w


def test_idle_quote_without_strategy_entry_or_start(tmp_path):
    w = worker(tmp_path)
    assert not w.running and w.strategy.entry is None
    w.watch_position()
    assert D(w.open_estimate['value_usd']) == D('1.99')
    assert D(w.open_estimate['pnl_usd']) == D('.99')
    w.strategy.observe.assert_not_called()
    assert not w.running


def test_failures_recover_without_modal_or_execution(tmp_path):
    w = worker(tmp_path)
    events = []
    w.event.connect(lambda name, value: events.append(name))
    w.chain.exit_quote.side_effect = [TimeoutError(), TimeoutError(), 2*10**POOL.quote_decimals]
    for _ in range(2):
        w.watch_position()
        assert w.open_estimate is None and w.quote_unavailable
        assert w.position_watch_error
    w.watch_position()
    assert w.open_estimate and not w.quote_unavailable
    assert not w.position_watch_error and 'error' not in events


def test_unknown_operation_no_read_or_resend(tmp_path):
    w = worker(tmp_path)
    w.store.data['operation'] = {'phase':'sent'}
    w.watch_position()
    w.chain.exit_quote.assert_not_called()
    assert w.open_estimate is None and 'неизвестен' in w.position_watch_error
    assert w.store.data['operation'] == {'phase':'sent'}


def test_restored_live_unknown_cost_remains_unknown(tmp_path):
    w = worker(tmp_path)
    w.mode = 'LIVE'
    w.live = SimpleNamespace(owner='0x'+'34'*20)
    w.set_position(2*10**POOL.token_decimals, D(1))
    w.store.save()
    w.store = Store(tmp_path/'watch.json')
    w.watch_position()
    assert D(w.open_estimate['value_usd']) == D(2)
    assert w.open_estimate['pnl_usd'] is None
    assert w.open_estimate['excludes_exit_gas']


def test_idle_loop_observes_without_start_and_quits(tmp_path):
    w = worker(tmp_path)
    observe = w.watch_position
    def watch():
        observe()
        w.quit_event.set()
    w.watch_position = watch
    w.run_loop()
    assert w.open_estimate and not w.running
    w.close_position.assert_not_called()


@pytest.mark.parametrize('mode', ['DEMO','PAPER','LIVE'])
def test_no_position_no_watcher(tmp_path, mode):
    w = worker(tmp_path)
    w.mode = mode
    w.paper.position = D(0)
    assert not w.watchable_position()


def test_stale_quote_clears_previous_estimate(tmp_path):
    w = worker(tmp_path)
    w.watch_position()
    assert w.open_estimate
    def stale():
        w.price_time = time.monotonic()-10
        return D(1)
    w.read_price = stale
    w.watch_position()
    assert w.open_estimate is None and w.quote_unavailable


def test_live_known_cost_restored_without_write(tmp_path):
    w = worker(tmp_path)
    w.mode = 'LIVE'
    w.live = SimpleNamespace(owner='0x'+'34'*20)
    w.set_position(2*10**POOL.token_decimals, D(1))
    w.position()['entry_cost_usd'] = '1.5'
    w.store.save()
    w.store = Store(tmp_path/'watch.json')
    w.store.save = Mock(side_effect=AssertionError('Read-only watcher must not save'))
    w.watch_position()
    assert D(w.open_estimate['pnl_usd']) == D('.5')
    w.store.save.assert_not_called()
