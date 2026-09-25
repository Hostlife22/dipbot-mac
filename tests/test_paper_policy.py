from dataclasses import replace
from decimal import Decimal as D
from types import SimpleNamespace as NS
import pytest
from dipbot.paper_policy import PaperPolicy
from dipbot.trader import PaperTrader
from dipbot.storage import Store
from dipbot.worker import Worker
from dipbot.entry_guard import EntryRejected
from test_autopair_dynamic import POOL


def test_model_fees_do_not_reduce_token_quantity_and_can_exceed_proceeds():
    paper=PaperTrader(D(0))
    paper.buy_quoted(D('1.1'),D(2))
    assert paper.position==2 and paper.cost==D('1.1')
    assert paper.sell_quoted(D(1),D('1.2'))==D('-1.3')
    assert paper.realized==D('-1.3') and not paper.position


def test_stop_interrupts_delayed_paper_buy_before_fill(tmp_path):
    w=Worker(Store(tmp_path/'state.json'));w.mode='PAPER';w.pool=POOL;w.current_price=D(1)
    w.paper_policy=PaperPolicy(10,D('.1'))
    w.stop_event.set()
    with pytest.raises(EntryRejected,match='STOP'):w.open_position()
    assert not w.paper.position


def test_worker_applies_model_fee_at_both_fills(tmp_path):
    w=Worker(Store(tmp_path/'state.json'));w.mode='PAPER';w.pool=POOL;w.current_price=D(1)
    w.paper_policy=PaperPolicy(0,D('.01'))
    w.strategy.settings=replace(w.strategy.settings,amount=D(1),slippage=D(0),dynamic=D(0))
    w.chain=NS(quote=lambda pool,amount,buy:amount)
    w.open_position()
    assert w.paper.cost==D('1.01') and w.paper.position==1
    w.read_price=lambda:D(1)
    w.close_position('MANUAL')
    assert w.paper.realized==D('-.02')


@pytest.mark.parametrize('data',[{'latency_seconds':-1},{'fee_quote':'NaN'},{'latency_seconds':'inf'}])
def test_invalid_paper_policy(data):
    with pytest.raises(ValueError):PaperPolicy.parse(data)


def test_stop_during_delay_cancels_before_router_quote(tmp_path):
    w=Worker(Store(tmp_path/'state.json'));w.mode='PAPER';w.pool=POOL;w.current_price=D(1)
    w.paper_policy=PaperPolicy(10,D('.1'))
    calls=[]
    w.chain=NS(quote=lambda *args:calls.append(args))
    def interrupted_wait(seconds):
        assert seconds==10
        w.stop_event.set()
        return True
    w.stop_event.wait=interrupted_wait
    with pytest.raises(EntryRejected,match='STOP'):w.open_position()
    assert not calls and not w.paper.position
