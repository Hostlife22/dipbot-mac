from decimal import Decimal as D
from dipbot.research.replay import replay, ReplayCosts
from dipbot.domain.strategy import Settings
from dipbot.domain.signal_policy import SignalPolicy


def test_delayed_fill_does_not_take_profit_against_old_signal_price():
    rows = [{'t':t,'price':str(p)} for t,p in [(0,100),(.1,96),(.2,98),(.3,98),(.4,98)]]
    result = replay(rows, Settings(dip=D(3),take_profit=D(2),dynamic=D(0),min_swaps=D(0)),
                    SignalPolicy(), ReplayCosts(fee_bps=D(0),latency_seconds=.1))
    assert [t['side'] for t in result['trades']] == ['BUY']
    assert D(result['open_quantity']) > 0
    assert not result['pending']
