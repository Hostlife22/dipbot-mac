from decimal import Decimal as D
from types import SimpleNamespace
import time
import pytest
from dipbot.domain.sizing import SizingPolicy
from dipbot.execution.accounting import RateBook
from dipbot.domain.entry_guard import EntryRejected
from dipbot.market.chain import WBNB
from dipbot.application.worker import Worker
from dipbot.persistence.storage import Store
from test_autopair_dynamic import POOL
from test_execution import trader,Function
from test_app_autopair_flow import window
from dipbot.domain.strategy import Settings


def test_usd_size_recalculates_and_missing_rate_blocks():
    book=RateBook();policy=SizingPolicy('usd')
    with pytest.raises(EntryRejected):policy.amount_quote(D(1),WBNB,book)
    book.update(WBNB,D(800),time.monotonic())
    assert policy.amount_quote(D(1),WBNB,book)==D('.00125')
    book.update(WBNB,D(1000),time.monotonic())
    assert policy.amount_quote(D(1),WBNB,book)==D('.001')


def test_ui_keeps_usd_and_base_amounts_separate(window):
    w=window
    w.params['amount'].setText('.0123')
    w.amount_unit.setCurrentIndex(1)
    assert w.params['amount'].text()=='1'
    w.params['amount'].setText('5')
    w.amount_unit.setCurrentIndex(0)
    assert D(w.params['amount'].text())==D('.0123')
    w.amount_unit.setCurrentIndex(1)
    assert w.params['amount'].text()=='5'


def test_entry_preserves_gas_reserve_but_exit_can_spend_it(trader):
    trader.reserve_wei=10**15
    trader.chain.w3.eth.get_balance=lambda _:25200*trader.gas_price
    trader.begin('BUY target')
    with pytest.raises(ValueError,match='резерва'):trader.send(Function(),'BUY')
    assert not trader.operation['transactions']
    trader.operation['description']='SELL target'
    trader.send(Function(),'SELL')
    assert trader.operation['transactions'][-1]['status']=='confirmed'


def test_worker_uses_updated_fx_at_each_entry(tmp_path):
    w=Worker(Store(tmp_path/'state.json'));w.mode='PAPER';w.pool=POOL
    w.current_price=D(1);w.sizing=SizingPolicy('usd');w.requested_amount=D(1)
    w.strategy.settings=Settings(amount=D('.00125'),slippage=D(0),dynamic=D(0))
    w.rates.update(POOL.quote,D(1000),time.monotonic())
    w.chain=SimpleNamespace(quote=lambda pool,amount,buy:amount)
    w.open_position()
    assert w.paper.cost==D('.001')


@pytest.mark.parametrize('unit,reserve',[('invalid','0'),('quote','-1'),('usd','NaN')])
def test_invalid_sizing_is_rejected(unit,reserve):
    with pytest.raises(ValueError):SizingPolicy(unit,D(reserve))
