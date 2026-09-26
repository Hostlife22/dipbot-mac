from decimal import Decimal as D
import pytest
from dipbot.research.replay import replay,ReplayCosts
from dipbot.domain.strategy import Settings
from dipbot.domain.signal_policy import SignalPolicy


def samples(prices):
    return [{'t':i*.1,'price':str(p),'block':i,'block_hash':str(i)} for i,p in enumerate(prices)]


def test_delayed_fill_never_uses_future_price_and_keeps_terminal_pending():
    rows=samples([100,96,96,96,96,100,100,100,100])
    prefix=replay(rows[:3],Settings(dip=D(3), take_profit=D(2), stop_loss=D(2), slippage=D(3), dynamic=D(150)),SignalPolicy('window'),ReplayCosts(latency_seconds=.2))
    assert not prefix['trades'] and prefix['pending']
    full=replay(rows,Settings(dip=D(3), take_profit=D(2), stop_loss=D(2), slippage=D(3), dynamic=D(150)),SignalPolicy('window'),ReplayCosts(latency_seconds=.2))
    assert full['trades'][0]['t']>=.3 and full['trades'][0]['signal_t']==.1
    assert full['trades'][1]['side']=='SELL'
    changed=rows[:3]+samples([500]*6)
    # The output for an unchanged prefix is independent of any appended future.
    assert replay(changed[:3],Settings(dip=D(3), take_profit=D(2), stop_loss=D(2), slippage=D(3), dynamic=D(150)),SignalPolicy('window'),ReplayCosts(latency_seconds=.2))==prefix


def test_snapshot_bound_rejects_adverse_move_without_a_position():
    result=replay(samples([100,96,110,110]),Settings(dip=D(3), take_profit=D(2), stop_loss=D(2), slippage=D(3), dynamic=D(150)),SignalPolicy('window'),ReplayCosts(latency_seconds=.1))
    assert result['rejected'][0]['reason']=='snapshot_minOut'
    assert result['open_quantity']=='0' and not result['trades']


def test_gas_is_charged_on_both_sides_and_stop_does_not_auto_restart():
    cost=ReplayCosts(fee_bps=D(0),gas_quote=D('.001'),latency_seconds=0)
    result=replay(samples([100,96,96,90,90,89]),Settings(dip=D(3), take_profit=D(2), stop_loss=D(2), slippage=D(3), dynamic=D(150)),SignalPolicy('window'),cost)
    assert result['stopped'] and len(result['trades'])==2
    expected=D('.02')/96*90-D('.02')-D('.002')
    assert abs(D(result['realized_quote'])-expected)<D('1e-25')


def test_replay_rejects_noncausal_or_nonfinite_input():
    for rows in [[{'t':1,'price':'1'},{'t':0,'price':'2'}],[{'t':0,'price':'NaN'}]]:
        with pytest.raises(ValueError): replay(rows,Settings(dip=D(3), take_profit=D(2), stop_loss=D(2), slippage=D(3), dynamic=D(150)),SignalPolicy())


def test_usd_size_uses_only_fx_available_at_signal_and_missing_rate_rejects():
    rows=samples([100,96,96])
    rows[1]['quote_usd']='100'
    rows[2]['quote_usd']='200'
    result=replay(rows,Settings(dip=D(3), take_profit=D(2), stop_loss=D(2), slippage=D(3), dynamic=D(150)),SignalPolicy('window'),ReplayCosts(latency_seconds=0),
                  size_unit='usd',requested_amount=D(1))
    assert D(result['trades'][0]['cost'])==D('.01')
    rows[1].pop('quote_usd')
    result=replay(rows,Settings(dip=D(3), take_profit=D(2), stop_loss=D(2), slippage=D(3), dynamic=D(150)),SignalPolicy('window'),ReplayCosts(latency_seconds=0),
                  size_unit='usd',requested_amount=D(1))
    assert result['rejected'][0]['reason']=='missing_usd_rate' and not result['trades']
