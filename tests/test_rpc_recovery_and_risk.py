from types import SimpleNamespace
import pytest
from web3.exceptions import BlockNotFound
from dipbot.worker import Worker
from dipbot.storage import Store
from dipbot.strategy import D
from dipbot.trader import UncertainTransaction, PaperTrader
from test_execution import trader, Function
from test_autopair_dynamic import POOL


@pytest.mark.parametrize('read_error', [TimeoutError, BlockNotFound])
def test_read_timeout_preserves_position_then_checks_exit(tmp_path, read_error):
    worker = Worker(Store(tmp_path/'state.json'))
    worker.running = True
    worker.paper.buy(D(1), D(1))
    worker.strategy.bought(D(1))
    observations = iter([read_error("synthetic read failure"), D('1.20')])
    def read():
        value = next(observations)
        if isinstance(value, Exception):
            raise value
        return value
    worker.read_price = read
    exits = []
    worker.close_position = exits.append
    worker.observe()
    assert worker.running and worker.quote_unavailable and worker.paper.position
    assert exits == []
    worker.observe()
    assert exits == ['TAKE_PROFIT'] and not worker.quote_unavailable


@pytest.mark.parametrize('execution_error', [TimeoutError, BlockNotFound])
def test_execution_timeout_is_not_retried_as_a_quote(tmp_path, execution_error):
    worker = Worker(Store(tmp_path/'state.json'))
    worker.strategy.base = D(100)
    worker.read_price = lambda: D(90)
    def buy():
        raise execution_error("synthetic execution failure")
    worker.open_position = buy
    with pytest.raises(execution_error):
        worker.observe()
    assert not worker.quote_unavailable
    worker.store.data['operation'] = {'description': 'pending BUY'}
    with pytest.raises(UncertainTransaction):
        worker.observe()


def test_old_exhausted_budget_does_not_block_after_restart(trader):
    trader.store.data['risk_limits'] = {'budget': '0', 'position': '0'}
    trader.store.data['risk_spent'] = {trader.owner.lower(): 10**30}
    trader.store.save()
    from dipbot.trader import LiveTrader
    restored = LiveTrader(trader.chain, trader.account.key, Store(trader.store.path), D('0.1'), lambda _: None)
    for _ in range(3):
        restored.begin('BUY')
        restored.send(Function(), 'BUY')  # Offline provider; no real signing wallet.
        restored.finish()
    assert len(restored.store.data['history']) == 3


def test_per_transaction_gas_limit_still_blocks(trader):
    trader.max_fee = 1
    trader.begin('BUY')
    with pytest.raises(ValueError, match='комиссия'):
        trader.send(Function(), 'BUY')
    assert trader.operation['transactions'] == []


def test_quote_paper_does_not_deduct_tolerance_as_fee():
    trader = PaperTrader(D(3))
    trader.buy_quoted(D(100), D(99))
    assert trader.sell_quoted(D(101)) == 1


def test_paper_uses_amount_quote_and_enforces_snapshot(tmp_path):
    worker = Worker(Store(tmp_path/'state.json'));worker.mode='PAPER';worker.pool=POOL
    worker.current_price=D(1)
    calls=[]
    def quote(pool, amount, buy):
        calls.append((amount,buy))
        return amount*99//100
    worker.chain=SimpleNamespace(quote=quote)
    worker.open_position()
    assert calls and worker.paper.position > 0
    # 1% pool fee/impact, not an additional forced 3% haircut.
    assert worker.paper.cost/worker.paper.position < D('1.02')


def test_paper_rejected_quote_does_not_create_position(tmp_path):
    worker = Worker(Store(tmp_path/'state.json'));worker.mode='PAPER';worker.pool=POOL
    worker.current_price=D(1)
    worker.chain=SimpleNamespace(quote=lambda p,a,b:a//2)
    with pytest.raises(ValueError, match='minOut'):
        worker.open_position()
    assert not worker.paper.position and worker.strategy.entry is None


def test_live_cost_survives_reference_update_and_pnl_restart(tmp_path):
    worker = Worker(Store(tmp_path/'state.json'));worker.mode='LIVE';worker.pool=POOL
    worker.current_price=D(1)
    worker.chain=SimpleNamespace(price=lambda p:D('1.1'), balance=lambda *a:10**18)
    worker.live=SimpleNamespace(owner='0x'+'34'*20,begin=lambda _:None,finish=lambda:None,
        swap=lambda p,a,b,t,**kw:19*10**15 if b else 21*10**15)
    worker.open_position()
    assert worker.position()['cost_quote']=='0.02'
    assert worker.position()['entry']=='1.1'
    worker.close_position('TAKE_PROFIT')
    saved=Store(worker.store.path)
    assert D(next(iter(saved.data['realized_quote'].values()))) == D('.001')
    assert not worker.position()
