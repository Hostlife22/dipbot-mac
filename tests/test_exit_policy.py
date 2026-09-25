from decimal import Decimal as D

import pytest

from dipbot.exit_policy import ExitPolicy
from dipbot.signal_policy import SignalPolicy
from dipbot.strategy import Settings, Strategy


def strategy(**kwargs):
    return Strategy(Settings(take_profit=D(50), stop_loss=D(20)),
                    SignalPolicy(mode='window'), ExitPolicy(**kwargs))


def test_trailing_exact_boundary_and_stop():
    s = strategy(trailing_pct=D(5))
    s.bought(D(100), now=0)
    assert s.observe(D(120), .1) is None
    assert s.observe(D('114.0001'), .2) is None
    assert s.observe(D(114), .3) == 'TRAILING_STOP'
    s.sold(D(114), 'TRAILING_STOP', now=.4)
    assert s.stopped
    assert s.observe(D(90), .5) is None


def test_tp_and_sl_have_priority():
    s = strategy(trailing_pct=D(1), max_hold_seconds=1)
    s.bought(D(100), now=0)
    assert s.observe(D(150), 2) == 'TAKE_PROFIT'
    assert s.observe(D(80), 3) == 'STOP_LOSS'


def test_time_exit_on_same_block_and_after_gap():
    s = strategy(max_hold_seconds=10)
    s.bought(D(100), now=0)
    assert s.observe(D(100), 9.9, observation_id='block') is None
    assert s.observe(D(100), 10, observation_id='block') == 'TIME_EXIT'
    assert s.observe(D(100), 20, observation_id='block') == 'TIME_EXIT'


def test_cooldown_builds_new_baseline():
    s = strategy(cooldown_seconds=2)
    s.bought(D(100), now=0)
    s.sold(D(100), 'TIME_EXIT', now=1)
    assert s.observe(D(90), 2.9) is None
    assert s.observe(D(80), 3) is None
    assert s.base == D(80)
    assert s.observe(D(77), 3.1) == 'BUY'


@pytest.mark.parametrize('values', [
    {'trailing_pct': 'NaN'}, {'trailing_pct': 100},
    {'max_hold_seconds': -1}, {'cooldown_seconds': 'inf'},
])
def test_invalid_policy(values):
    with pytest.raises(ValueError):
        ExitPolicy.parse(values)


def test_default_exits_remain_disabled():
    s = strategy()
    s.bought(D(100), now=0)
    assert s.observe(D(120), 1) is None
    assert s.observe(D(90), 86401) is None
    assert ExitPolicy.parse(ExitPolicy().export()) == ExitPolicy()


def test_restart_keeps_open_position_clock_and_peak(tmp_path):
    from dipbot.worker import Worker
    from dipbot.storage import Store
    from test_worker import config
    w = Worker(Store(tmp_path/'state.json'))
    data = config() | {'exit_policy': {'max_hold_seconds': 30, 'trailing_pct': 5}}
    w.command('buy', data)
    started = w.strategy.entry_time
    w.strategy.peak_price = w.strategy.entry*D('1.1')
    peak = w.strategy.peak_price
    w.configure(data)
    assert w.strategy.entry_time == started
    assert w.strategy.peak_price == peak
    assert w.strategy.exit_policy.max_hold_seconds == 30


def test_exit_preferences_roundtrip(tmp_path):
    from dipbot import preferences
    from dipbot.storage import Store
    store = Store(tmp_path/'state.json')
    data = preferences.from_windows_ui({})
    data['exit_policy'] = ExitPolicy(D('1.25'), 300, 12).export()
    preferences.save(store, data)
    restored = preferences.normalize(Store(store.path).data['ui_preferences'])
    assert ExitPolicy.parse(restored['exit_policy']) == ExitPolicy(D('1.25'), 300, 12)
