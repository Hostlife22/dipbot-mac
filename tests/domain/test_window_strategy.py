from decimal import Decimal as D
import pytest
from dipbot.domain.strategy import Settings, Strategy
from dipbot.domain.signal_policy import SignalPolicy
from dipbot.persistence import preferences
from tests.support.preferences import values


def window(**kwargs):
    return Strategy(Settings(dip=D(3), take_profit=D(2), stop_loss=D(2), max_gap=10), SignalPolicy('window', **kwargs))


def test_gradual_dip_retains_high_where_legacy_reanchors():
    rolling, legacy = window(), Strategy(Settings(dip=D(3), take_profit=D(2), stop_loss=D(2), max_gap=10))
    actions, old = [], []
    for i, price in enumerate(['100','99','98','97']):
        actions.append(rolling.observe(D(price), i))
        old.append(legacy.observe(D(price), i))
    assert actions == [None,None,None,'BUY']
    assert old == [None]*4
    assert rolling.base == 100 and rolling.base_time == 0


def test_duplicate_states_do_not_emit_extra_signals():
    s = window()
    s.observe(D(100), 0, observation_id='a')
    assert s.observe(D(96), 1, observation_id='b') == 'BUY'
    assert s.observe(D(96), 1.1, observation_id='b') is None
    assert s.observe(D(96), 1.2, observation_id='b') is None


def test_same_event_sequence_has_same_signal_despite_duplicate_polls():
    def replay(extra):
        s = window(); result = []
        for now, price, identity in [(0,'100','a'),(1,'99','b'),(2,'98','c'),(3,'96','d')]:
            action = s.observe(D(price), now, observation_id=identity)
            if action: result.append((identity, action))
            if extra:
                for offset in [.1,.2,.3]:
                    assert s.observe(D(price), now+offset, observation_id=identity) is None
        return result
    assert replay(False) == replay(True) == [('d','BUY')]


def test_expired_peak_is_not_used_for_entry():
    s = window(window_seconds=2)
    s.observe(D(100), 0)
    s.observe(D(99), 1)
    assert s.observe(D(96), 3.1) is None
    assert s.base == 96


def test_rebound_requires_dip_and_bounce_within_dip_zone():
    s = window(rebound_pct=D(1))
    assert s.observe(D(100), 0) is None
    assert s.observe(D(96), 1) is None
    assert s.observe(D(94), 2) is None
    assert s.observe(D('94.5'), 3) is None
    assert s.observe(D(95), 4) == 'BUY'


def test_gap_and_rejection_reset_trough_and_high():
    s = window(rebound_pct=D(1))
    s.observe(D(100), 0); s.observe(D(90), 1)
    assert s.observe(D(91), 20) is None
    assert s.base == 91 and s.trough is None
    s.reset_anchor()
    assert not s.highs and s.base is None
    assert s.observe(D(85), 21) is None


def test_open_position_still_exits_even_after_gap():
    s = window(); s.bought(D(100))
    assert s.observe(D(90), 100) == 'STOP_LOSS'


def test_policy_preferences_roundtrip_and_invalid_mode():
    data = values() | {'signal_policy': {'mode':'window','window_seconds':45,'rebound_pct':'0.75'}}
    assert preferences.normalize(data)['signal_policy'] == SignalPolicy('window',45,D('.75')).export()
    with pytest.raises(ValueError):
        preferences.normalize(values() | {'signal_policy': {'mode': 'unknown'}})
