from types import SimpleNamespace
import pytest
from dipbot.chain import Pool, WBNB, USDT, address, V2_ROUTER, V3_ROUTER
from dipbot.trader import LiveTrader, UncertainTransaction
from dipbot.strategy import D


@pytest.mark.parametrize("version", ["V2", "V3"])
@pytest.mark.parametrize("buy", [True, False])
@pytest.mark.parametrize("signal_bound", [None, 99])
def test_swap_keeps_preapproval_bound_and_accounts_received(version, buy, signal_bound):
    pool = Pool(address("0x"+"12"*20), version, address(USDT), address(WBNB), 18, 18, False, 500 if version == "V3" else 0)
    trader = object.__new__(LiveTrader)
    trader.owner = address("0x"+"34"*20)
    sent = []
    approvals = []
    balances = iter([1000, 10, 110])
    quotes = iter([100, 95])  # Price worsened while approval was mined.
    functions = SimpleNamespace(
        swapExactTokensForTokensSupportingFeeOnTransferTokens=lambda *args: ("V2", args),
        exactInputSingle=lambda args: ("V3", args))
    trader.chain = SimpleNamespace(verify_pool=lambda *args: pool,
        balance=lambda *args: next(balances), quote=lambda *args: next(quotes),
        contract=lambda *args: SimpleNamespace(functions=functions))
    trader.verify_router = lambda p: (V2_ROUTER if version == "V2" else V3_ROUTER, [])
    trader.approve = lambda src, dst, n: approvals.append((src, dst, n))
    trader.send = lambda function, label: sent.append(function)
    signal_bound = signal_bound if buy else None
    assert trader.swap(pool, 20, buy, D(2), signal_minimum=signal_bound) == 100
    expected_minimum = signal_bound or 98
    src, dest = (pool.quote, pool.token) if buy else (pool.token, pool.quote)
    assert approvals[0][0] == src and approvals[0][2] == 20
    kind, args = sent[0]
    if version == "V2":
        assert args[0] == 20 and args[1] == expected_minimum
        assert args[2] == [src, dest] and args[3] == trader.owner
    else:
        assert args[:4] == (src, dest, 500, trader.owner)
        assert args[5:7] == (20, expected_minimum)


def test_token_balance_change_below_minimum_latches_error():
    pool = Pool(address("0x"+"12"*20), "V2", address(USDT), address(WBNB), 18, 18, False)
    trader = object.__new__(LiveTrader)
    trader.owner = address("0x"+"34"*20)
    balances = iter([1000, 0, 50])
    trader.chain = SimpleNamespace(verify_pool=lambda *args: pool,
        balance=lambda *args: next(balances), quote=lambda *args: 100,
        contract=lambda *args: SimpleNamespace(functions=SimpleNamespace(
            swapExactTokensForTokensSupportingFeeOnTransferTokens=lambda *args: args)))
    trader.verify_router = lambda p: (V2_ROUTER, [])
    trader.approve = lambda *args: None
    trader.send = lambda *args: None
    with pytest.raises(UncertainTransaction, match="minOut"):
        trader.swap(pool, 20, True, D(2))


def test_sequential_converter_passes_actual_output_and_divides_tolerance():
    trader = object.__new__(LiveTrader)
    hops = [SimpleNamespace(name="first"), SimpleNamespace(name="second")]
    trader.conversion_route = lambda *args: hops
    calls = []
    trader.wrap = lambda n: calls.append(("wrap", n))
    def swap(pool, amount, buy, tolerance):
        calls.append((pool.name, amount, buy, tolerance))
        return amount * 2
    trader.swap = swap
    assert trader.convert(USDT, 10, True, D(2)) == 40
    assert calls == [("wrap", 10), ("first", 10, True, D(1)), ("second", 20, True, D(1))]



@pytest.mark.parametrize('returned,accepted', [(85, True), (84, False)])
def test_converter_rejects_actual_amount_roundtrip_loss(returned, accepted):
    trader = object.__new__(LiveTrader)
    pool = Pool(address('0x'+'12'*20), 'V2', address(USDT), address(WBNB), 18, 18, False)
    calls = []
    def find(target, quote):
        return [pool] if (target, quote) == (address(USDT), address(WBNB)) else []
    def quote(p, amount, buy):
        calls.append((amount, buy))
        return 200 if buy else returned
    trader.chain = SimpleNamespace(find_pools=find, quote=quote)
    if accepted:
        assert trader.conversion_route(WBNB, USDT, 100) == [pool]
    else:
        with pytest.raises(ValueError, match='15%'):
            trader.conversion_route(WBNB, USDT, 100)
    assert calls == [(100, True), (200, False)]
