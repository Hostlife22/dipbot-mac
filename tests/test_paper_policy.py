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


def test_paper_fill_uses_fresh_spot_but_preserves_signal_minout(tmp_path):
    w=Worker(Store(tmp_path/'state.json'));w.mode='PAPER';w.pool=POOL;w.current_price=D(1)
    w.paper_policy=PaperPolicy(0,D(0))
    w.strategy.settings=replace(w.strategy.settings,amount=D(1),slippage=D(0),dynamic=D(0))
    w.chain=NS(price=lambda _:D(2),quote=lambda p,a,b:a*3//4)
    def fresh():w.current_price=D(2);return D(2)
    w.read_price=fresh
    with pytest.raises(EntryRejected,match='minOut'):w.open_position()
    assert not w.paper.position
    w.current_price=D(1);w.chain.quote=lambda p,a,b:a
    w.open_position()
    assert w.strategy.entry==D(2)


def test_stop_during_router_quote_prevents_virtual_fill(tmp_path):
    w=Worker(Store(tmp_path/'state.json'));w.mode='PAPER';w.pool=POOL;w.current_price=D(1)
    def quote(p,a,b):w.stop_event.set();return a
    w.chain=NS(quote=quote)
    with pytest.raises(EntryRejected,match='STOP'):w.open_position()
    assert not w.paper.position



def test_gas_model_uses_fresh_base_and_native_fx_without_pool_fee_double_count():
    import time
    from dipbot.accounting import RateBook
    from dipbot.chain import WBNB
    rates = RateBook();now = time.monotonic()
    rates.update(POOL.quote, D(2), now);rates.update(WBNB, D(800), now)
    p = PaperPolicy(gas_units=200000, fee_quote=D('.001'))
    assert p.operation_cost(D('.1'),POOL.quote,rates) == D('.009')
    assert p.operation_cost(D('.1'),WBNB,rates) == D('.00102')
    assert PaperPolicy.parse(p.export()) == p


def test_missing_fx_cannot_become_free_gas():
    from dipbot.accounting import RateBook
    with pytest.raises(TimeoutError):
        PaperPolicy(gas_units=200000).operation_cost(D('.1'),POOL.quote,RateBook())


@pytest.mark.parametrize('value',[-1,True,1.5,2000001])
def test_invalid_gas_units_are_rejected(value):
    with pytest.raises(ValueError):PaperPolicy.parse({'gas_units':value})


def test_worker_gas_model_charges_both_fills(tmp_path):
    import time
    from dipbot.chain import WBNB
    w=Worker(Store(tmp_path/'state.json'));w.mode='PAPER';w.pool=POOL;w.current_price=D(1)
    w.paper_policy=PaperPolicy(0,D('.001'),200000)
    w.rates.update(POOL.quote,D(2),time.monotonic())
    w.rates.update(WBNB,D(800),time.monotonic())
    w.strategy.settings=replace(w.strategy.settings,amount=D(1),slippage=D(0),dynamic=D(0))
    w.chain=NS(quote=lambda pool,amount,buy:amount)
    w.open_position()
    assert w.paper.cost==D('1.009') and w.paper.position==1
    w.read_price=lambda:D(1)
    w.close_position('MANUAL')
    assert w.paper.realized==D('-.018')


def test_gas_fx_expiring_during_quote_prevents_fill(tmp_path):
    import time
    from dipbot.chain import WBNB
    w=Worker(Store(tmp_path/'state.json'));w.mode='PAPER';w.pool=POOL;w.current_price=D(1)
    w.paper_policy=PaperPolicy(0,D(0),200000)
    w.rates.update(POOL.quote,D(2),time.monotonic())
    w.rates.update(WBNB,D(800),time.monotonic())
    def quote(pool,amount,buy):
        w.rates.rates.clear()
        return amount
    w.chain=NS(quote=quote)
    with pytest.raises(EntryRejected,match='курсы'):w.open_position()
    assert not w.paper.position
