from tools.market_cycle_audit import audit


def signal(action, t):
    return dict(event='signal', action=action, t=t)


def execution(side, t, reason=None):
    return dict(event='execution', side=side, t=t, reason=reason)


def test_no_trades_is_not_a_completed_cycle():
    result = audit({}, [], {})
    assert result['consistent'] and result['natural_completed_cycles'] == 0


def test_natural_cycle_and_stop_cleanup_are_separate():
    events = [signal('BUY', 1), execution('BUY', 2),
              signal('TRAILING_STOP', 3), execution('SELL', 4, 'TRAILING_STOP'),
              signal('BUY', 9), execution('BUY', 10), execution('SELL', 11, 'STOP')]
    result = audit({'exit_policy': {'cooldown_seconds': 5}}, events, {'BUY': 2, 'SELL': 2})
    assert result['consistent'] and not result['open_position']
    assert result['natural_completed_cycles'] == 1
    assert result['reentry_signal_gaps_seconds'] == [5]
    assert result['observed_natural_exits'] == ['TRAILING_STOP']
    events[4]['t'] = 8
    assert not audit({'exit_policy': {'cooldown_seconds': 5}}, events)['consistent']


def test_mismatched_journal_and_missing_signal_are_rejected():
    result = audit({}, [execution('BUY', 1), execution('SELL', 2, 'STOP')], {'BUY': 2})
    assert not result['consistent']
    assert len(result['errors']) == 2
