from decimal import Decimal as D

import pytest

from dipbot.domain.exit_policy import ExitPolicy
from dipbot.domain.signal_policy import SignalPolicy
from dipbot.domain.strategy import Settings, Strategy


def strategy(**kwargs):
    return Strategy(Settings(dip=D(3), take_profit=D(50), stop_loss=D(20)),
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
    from dipbot.application.worker import Worker
    from dipbot.persistence.storage import Store
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
    from dipbot.persistence import preferences
    from dipbot.persistence.storage import Store
    store = Store(tmp_path/'state.json')
    data = preferences.from_windows_ui({})
    data['exit_policy'] = ExitPolicy(D('1.25'), 300, 12).export()
    preferences.save(store, data)
    restored = preferences.normalize(Store(store.path).data['ui_preferences'])
    assert ExitPolicy.parse(restored['exit_policy']) == ExitPolicy(D('1.25'), 300, 12)


def test_quote_exit_uses_proceeds_not_chart_price():
    s = strategy(tp_sl_basis='quote')
    s.bought(D(100), now=0)
    # Spot is up 60%, but selling the entire position returns only +1%.
    assert s.observe(D(160), .1, exit_return=D(1)) is None
    assert s.observe(D(160), .2, exit_return=D(-20)) == 'STOP_LOSS'


def test_quote_exit_refuses_missing_proceeds():
    s = strategy(tp_sl_basis='quote')
    s.bought(D(100), now=0)
    with pytest.raises(ValueError, match='котировки выхода'):
        s.observe(D(160), .1)


def test_canonical_exit_quote_checks_hash_and_raw_bounds():
    from dipbot.market.chain import Chain
    c = object.__new__(Chain)
    c.checked_header = {'hash': b'a'*32}
    c.check = lambda **kw: 123
    calls = []
    c.quote = lambda pool, amount, buy, **kw: calls.append((amount, buy, kw)) or 1234
    c.canonical_receipt = lambda receipt: calls.append(receipt)
    assert c.exit_quote(None, 42) == 1234
    assert calls == [(42, False, {'block':123}), {'blockNumber':123, 'blockHash':b'a'*32}]
    c.quote = lambda *a, **kw: -1
    with pytest.raises(ValueError):
        c.exit_quote(None, 42)


def test_worker_quote_failure_does_not_sell_or_use_old_return(tmp_path):
    from types import SimpleNamespace
    from dipbot.application.worker import Worker
    from dipbot.persistence.storage import Store
    from test_autopair_dynamic import POOL
    import time
    w = Worker(Store(tmp_path/'state.json'))
    w.mode = 'PAPER'
    w.pool = POOL
    w.strategy = strategy(tp_sl_basis='quote')
    w.strategy.bought(D(100), now=time.monotonic())
    w.paper.position, w.paper.cost = D(1), D(100)
    w.price_time = time.monotonic()
    w.read_price = lambda: D(200)
    w.market_source = 'BSC'
    def fail(*args):
        raise TimeoutError('test')
    w.chain = SimpleNamespace(exit_quote=fail)
    w.exit_return = '100'
    w.observe()
    assert w.quote_unavailable and w.exit_return is None
    assert w.paper.position == 1


def test_ui_quote_exit_is_labeled_without_spot_thresholds(tmp_path):
    from dipbot.application.worker import Worker
    from dipbot.persistence.storage import Store
    w = Worker(Store(tmp_path/'state.json'))
    w.strategy = strategy(tp_sl_basis='quote', trailing_pct=D(5))
    w.strategy.bought(D(100), now=0)
    w.paper.position = D(1)
    rows = []
    w.event.connect(lambda name, payload: rows.append((name, payload)))
    w.status()
    payload = dict(rows)['status']
    assert payload['exit_basis'] == 'quote'
    assert 'TP' not in payload['levels'] and 'SL' not in payload['levels']
    assert payload['levels']['TRAIL'] == '95.00'


def test_quote_status_renders_in_actual_qt_window(window):
    from dipbot.application.worker import Worker
    w = window
    w.worker.strategy = strategy(tp_sl_basis='quote')
    w.worker.strategy.bought(D(100), now=0)
    w.worker.paper.position = D(1)
    w.worker.exit_return = '1.23'
    w.worker.event.connect(w.on_event)
    w.worker.status()
    assert '+1.23%' in w.exit_status.text()
    assert not w.exit_status.isHidden()
    w.worker.paper.position = D(0)
    w.worker.status()
    assert w.exit_status.isHidden()
from test_app_autopair_flow import window


@pytest.mark.parametrize('reason', ['STOP_LOSS', 'TRAILING_STOP'])
def test_opt_in_reentry_requires_new_baseline_after_cooldown(reason):
    s = strategy(continue_after_risk_exit=True, cooldown_seconds=2)
    s.bought(D(100), now=0)
    s.sold(D(90), reason, now=1)
    assert not s.stopped
    assert s.observe(D(70), 2.99) is None
    assert s.observe(D(60), 3) is None
    assert s.base == 60
    assert s.observe(D(57), 3.1) == 'BUY'


def test_opt_in_never_restarts_explicit_stop():
    s = strategy(continue_after_risk_exit=True, cooldown_seconds=2)
    s.bought(D(100), now=0)
    s.sold(D(90), 'STOP', now=1)
    assert s.stopped and s.observe(D(50), 100) is None


@pytest.mark.parametrize('values', [
    {'continue_after_risk_exit': True},
    {'continue_after_risk_exit': 'false', 'cooldown_seconds': 5},
    {'continue_after_risk_exit': True, 'cooldown_seconds': .5},
])
def test_reentry_policy_rejects_ambiguous_or_zero_pause(values):
    with pytest.raises(ValueError): ExitPolicy.parse(values)


def test_reentry_ui_explicit_opt_in_and_export(window):
    assert not window.continue_after_exit.isChecked()
    window.continue_after_exit.setChecked(True)
    p = ExitPolicy.parse(window.exit_policy())
    assert p.continue_after_risk_exit and p.cooldown_seconds >= 1
    assert ExitPolicy.parse(p.export()) == p
