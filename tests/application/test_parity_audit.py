from tests.support.parity import pool, VECTORS
"""Expected results from native call sites or independent safety invariants."""
from types import SimpleNamespace
import pytest
from dipbot.market.chain import Chain, Pool, WBNB, USDT, ETH, address
from dipbot.execution.trader import LiveTrader
from dipbot.execution.errors import UncertainTransaction
from dipbot.persistence.storage import Store
from dipbot.domain.strategy import D




def test_finish_save_failure_keeps_latch_and_retry_does_not_duplicate_history(tmp_path):
    # Protection invariant; not a claim about original disk persistence.
    trader = object.__new__(LiveTrader)
    trader.store = Store(tmp_path / 'state.json')
    trader.owner = address('0x'+'34'*20)
    trader.operation = None
    trader.begin('confirmed BUY')
    real_save = trader.store.save
    def fail():
        raise OSError('disk full')
    trader.store.save = fail
    with pytest.raises(OSError):
        trader.finish()
    assert trader.store.data.get('operation') is trader.operation
    with pytest.raises(UncertainTransaction):
        trader.begin('duplicate')
    trader.store.save = real_save
    trader.finish()
    assert len(trader.store.data['history']) == 1


def test_route_quotes_use_one_encoded_path_and_reverse_fees():
    # Original _quote_route calls quoteExactInput, VA 0x141e754ed.
    chain = object.__new__(Chain)
    hops = [pool(USDT, WBNB, fee=500), pool(ETH, USDT, fee=2500, ident='13')]
    calls = []
    chain.call = lambda *args, **kwargs: calls.append(args) or (123, [], [], 0)
    assert chain.quote_route(hops, 100) == 123
    expected = bytes.fromhex(WBNB[2:]) + (500).to_bytes(3,'big') + bytes.fromhex(USDT[2:]) + (2500).to_bytes(3,'big') + bytes.fromhex(ETH[2:])
    assert calls[0][2:] == ('quoteExactInput', expected, 100)
    chain.quote_route(hops, 123, reverse=True)
    reverse = bytes.fromhex(ETH[2:]) + (2500).to_bytes(3,'big') + bytes.fromhex(USDT[2:]) + (500).to_bytes(3,'big') + bytes.fromhex(WBNB[2:])
    assert calls[1][2:] == ('quoteExactInput', reverse, 123)


def test_converter_tries_safe_alternative_after_best_quote_fails_roundtrip():
    # _select_safe_route sorts quotes and tries reverse safety per candidate.
    trader = object.__new__(LiveTrader)
    best = pool(USDT, WBNB, fee=500)
    safe = pool(USDT, WBNB, fee=2500, ident='13')
    def find(target, quote, routers=('V2','V3')):
        return [best, safe] if (target,quote)==(address(USDT),address(WBNB)) else []
    def quote_route(path, amount, reverse=False):
        if reverse:
            return 50 if path[0] == best else 95
        return 200 if path[0] == best else 190
    trader.chain = SimpleNamespace(find_pools=find, quote_route=quote_route)
    assert trader.conversion_route(WBNB, USDT, 100) == [safe]


@pytest.mark.parametrize('version', ['V2', 'V3'])
@pytest.mark.parametrize('buy', [True, False])
def test_converter_native_funding_atomic_swap_and_only_new_wbnb_unwrapped(version, buy, monkeypatch):
    # Native execution branches: 0x141e7ca84 / 0x141e7dc88 / 0x141e7ff8c.
    import dipbot.execution.trader as module
    monkeypatch.setattr(module.time, 'time', lambda: 1000)
    hops = ([pool(USDT, WBNB, version), pool(ETH, USDT, version, ident='13')] if buy else
            [pool(USDT, ETH, version), pool(WBNB, USDT, version, ident='13')])
    trader = object.__new__(LiveTrader)
    trader.owner = address('0x'+'34'*20)
    trader.conversion_route = lambda *args: hops
    trader.verify_router = lambda _: (address('0x'+'56'*20), [])
    approvals, calls, unwrapped = [], [], []
    trader.approve = lambda *args: approvals.append(args)
    trader.wrap = lambda _: pytest.fail('BUY must fund router directly, not wrap first')
    trader.unwrap = unwrapped.append
    native_balances = iter([1000, 1180])  # 200 received minus 20 gas
    token_balances = iter(([30, 230] if buy else [100, 30, 230]))
    functions = SimpleNamespace(
        exactInput=lambda args: ('v3', args),
        swapExactETHForTokensSupportingFeeOnTransferTokens=lambda *args: ('v2buy', args),
        swapExactTokensForETHSupportingFeeOnTransferTokens=lambda *args: ('v2sell', args))
    trader.chain = SimpleNamespace(
        verify_pool=lambda addr, token: next(p for p in hops if p.address==addr),
        balance=lambda *args: next(token_balances),
        quote_route=lambda path, amount, reverse=False: 95 if reverse else 200,
        w3=SimpleNamespace(eth=SimpleNamespace(get_balance=lambda _: next(native_balances))),
        contract=lambda *args: SimpleNamespace(functions=functions))
    def send(function, label, value=0):
        calls.append((function, value))
        return {'gasUsed': 10, 'effectiveGasPrice': 2}
    trader.send = send
    assert trader.convert(ETH, 100, buy, D(2)) == 200
    assert len(calls) == 1
    (kind, args), value = calls[0]
    assert value == (100 if buy else 0)
    assert bool(approvals) == (not buy)
    if version == 'V3':
        assert args[1:] == (trader.owner, 1060, 100, 196)
        assert len(args[0]) == 66
    else:
        assert args[-1] == 1060
        assert args[0 if buy else 1] == 196
    assert unwrapped == ([200] if version=='V3' and not buy else [])


def test_pending_stop_is_not_erased_by_queued_buy(tmp_path):
    from dipbot.application.worker import Worker
    worker = Worker(Store(tmp_path / 'state.json'))
    worker.stop_event.set()
    worker.configure = lambda _: None
    worker.read_price = lambda: D(1)
    worker.open_position = lambda: pytest.fail('STOP must cancel a not-yet-started BUY')
    with pytest.raises(ValueError, match='STOP'):
        worker.command('buy', {})
    assert worker.stop_event.is_set()


import json
from pathlib import Path
from dipbot.domain.strategy import Strategy, Settings



@pytest.mark.parametrize('case', VECTORS['scenarios'], ids=lambda c: c['id'])
def test_native_strategy_vectors(case):
    # These historical vectors explicitly use SL=5, not the recovered UI default.
    strategy = Strategy(Settings(dip=D(3), take_profit=D(2), stop_loss=D(5)))
    if 'entry' in case:
        strategy.bought(D(case['entry']))
    for now, price, signal, base in case['ticks']:
        assert strategy.observe(D(price), now) == signal
        assert strategy.base == (None if base is None else D(base))


@pytest.mark.parametrize('prices', [['0'], ['-1'], ['NaN'], ['Infinity']])
def test_invalid_price_never_creates_signal(prices):
    strategy = Strategy(Settings())
    with pytest.raises(ValueError):
        strategy.observe(D(prices[0]), 0)
    assert strategy.last_time is None


def test_mixed_or_broken_converter_route_cannot_reach_quote():
    from dipbot.market.chain import route_path
    with pytest.raises(ValueError, match='Смешанный'):
        route_path([pool(USDT, WBNB, 'V2'), pool(ETH, USDT, 'V3')])
    with pytest.raises(ValueError, match='Разрыв'):
        route_path([pool(USDT, WBNB), pool(ETH, WBNB)])


def test_multihop_abi_selector_is_exact_input_not_single():
    from web3 import Web3
    from dipbot.market.chain import V3_ABI, route_path
    path = route_path([pool(USDT, WBNB), pool(ETH, USDT, ident='13')])[1]
    encoded = Web3().eth.contract(abi=V3_ABI).functions.exactInput(
        (path, address('0x'+'34'*20), 1060, 100, 196))._encode_transaction_data()
    assert encoded[:10] == Web3.to_hex(Web3.keccak(text='exactInput((bytes,address,uint256,uint256,uint256))')[:4])


@pytest.mark.parametrize('stage', ['configure', 'read'])
def test_stop_during_manual_buy_preparation_cancels_before_position(stage, tmp_path):
    from dipbot.application.worker import Worker
    worker = Worker(Store(tmp_path / 'state.json'))
    def config(_):
        if stage == 'configure': worker.stop_event.set()
    def read():
        if stage == 'read': worker.stop_event.set()
    worker.configure = config
    worker.read_price = read
    worker.open_position = lambda: pytest.fail('BUY after STOP')
    with pytest.raises(ValueError, match='STOP'):
        worker.command('buy', {})


def test_worker_waits_for_buy_then_stop_closes_once_and_discards_queued_buy(tmp_path):
    # Controlled synchronous analogue of receipt callback while UI requests STOP.
    from dipbot.application.worker import Worker
    worker = Worker(Store(tmp_path / 'state.json'))
    events = []
    worker.configure = lambda _: None
    worker.read_price = lambda: D(1)
    def buy():
        events.append('buy_confirmed')
        worker.paper.position = D(1)
        worker.stop_event.set()
    def sell(reason):
        events.append(reason)
        worker.paper.position = D(0)
        worker.quit_event.set()
    worker.open_position, worker.close_position = buy, sell
    worker.submit('buy')
    worker.submit('buy')
    worker.run()
    assert events == ['buy_confirmed', 'STOP']
    assert worker.commands.empty()
    assert worker.strategy.stopped


def test_confirmed_sell_failed_price_does_not_resurrect_position(tmp_path):
    from dipbot.application.worker import Worker
    worker = Worker(Store(tmp_path / 'state.json'))
    worker.mode = 'LIVE'
    worker.pool = pool(USDT, WBNB)
    worker.live = SimpleNamespace(owner=address('0x'+'34'*20), begin=lambda _: None,
        swap=lambda *args, **kwargs: 100, finish=lambda: None)
    def failed_price(_): raise TimeoutError()
    worker.chain = SimpleNamespace(balance=lambda *args: 100, price=failed_price)
    worker.set_position(100, D(1))
    worker.strategy.bought(D(1))
    with pytest.raises(TimeoutError):
        worker.close_position('STOP')
    assert not worker.position()
    assert worker.strategy.entry is None and worker.strategy.stopped
    assert not Store(worker.store.path).data['positions']


def test_original_converter_candidates_preference_bridge_fee_and_sell_reversal():
    # Native _buy_route_candidates, bridge constants both 100 at 0x141e840c1.
    from dipbot.market.routes import conversion_specs
    aapl = address('0x431a3bee82e2ca41e49895cbece5bb0f76a89b7a')
    specs = conversion_specs(WBNB, aapl)
    assert specs[0] == ('V3', (address(WBNB),address(USDT),aapl), (100,2500))
    assert specs[1] == ('V2', (address(WBNB),aapl), ())
    assert all(address(ETH) not in nodes for _,nodes,_ in specs)
    assert len(specs) == 10 and len(set(specs)) == len(specs)
    assert conversion_specs(aapl,WBNB) == [(k,ns[::-1],fs[::-1]) for k,ns,fs in specs]
    bmnr = address('0x3548Da95a9eFFE481e8604664d75e95821e557F5')
    eth_specs = conversion_specs(WBNB,bmnr)
    assert eth_specs[0] == ('V3',(address(WBNB),address(ETH),bmnr),(100,10000))
    assert len(eth_specs) == 14
