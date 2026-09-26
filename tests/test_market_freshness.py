from types import SimpleNamespace
from decimal import Decimal as D
import pytest
from dipbot.market.chain import Chain, StaleBlock
from dipbot.application.worker import Worker
from dipbot.persistence.storage import Store
from dipbot.domain.signal_policy import SignalPolicy
from test_app_autopair_flow import window


def test_stale_block_is_retryable_and_cannot_update_checked_header(monkeypatch):
    c=object.__new__(Chain); c.max_block_age=5
    c.w3=SimpleNamespace(provider=None,eth=SimpleNamespace(chain_id=56,
        get_block=lambda _: {'number':12,'timestamp':94}))
    monkeypatch.setattr('dipbot.market.chain.time.time',lambda:100)
    with pytest.raises(StaleBlock): c.check(force_network=False)
    assert not hasattr(c,'checked_header')
    c.max_block_age=7
    assert c.check(force_network=False)==12


def test_stale_market_keeps_position_but_cannot_trigger_new_trade(tmp_path):
    w=Worker(Store(tmp_path/'state.json'));w.running=True
    w.paper.buy_quoted(D(1),D(1));w.strategy.bought(D(1))
    def old(): raise StaleBlock('old')
    w.read_price=old
    w.observe()
    assert w.running and w.quote_unavailable and w.paper.position==1
    assert w.strategy.entry==1


def test_ui_exposes_block_age_separately_from_read_age(window,monkeypatch):
    monkeypatch.setattr('dipbot.ui.window.time.time',lambda:100)
    window.on_event('price_context',{'source':'BSC','quote':'','block':12,'block_timestamp':98,
                                    'rpc_source':'BSC · резервный RPC'})
    window.on_event('price','1')
    assert 'блок 12' in window.quote_age.text()
    assert 'возраст 2.0 с' in window.quote_age.text()
    assert 'резервный RPC' in window.quote_age.text()
    assert 'последняя котировка' in window.quote_age.text()


def test_invalid_freshness_policy_is_rejected():
    for age in [0,31,float('nan'),float('inf')]:
        with pytest.raises(ValueError): SignalPolicy(max_block_age=age)
