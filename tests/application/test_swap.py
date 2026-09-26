from types import SimpleNamespace
import pytest
from dipbot.market.chain import Pool, WBNB, USDT, address, V2_ROUTER, V3_ROUTER
from dipbot.execution.trader import LiveTrader
from dipbot.execution.errors import UncertainTransaction
from dipbot.domain.strategy import D


@pytest.mark.parametrize("with_quote_reader", [False, True])
@pytest.mark.parametrize("version", ["V2", "V3"])
@pytest.mark.parametrize("buy", [True, False])
@pytest.mark.parametrize("signal_bound", [None, 99])
def test_swap_keeps_preapproval_bound_and_accounts_received(version, buy, signal_bound, with_quote_reader):
    pool = Pool(address("0x"+"12"*20), version, address(USDT), address(WBNB), 18, 18, False, 500 if version == "V3" else 0)
    trader = object.__new__(LiveTrader)
    trader.owner = address("0x"+"34"*20)
    sent = []
    approvals = []
    balances = iter([1000, 10, 1000, 110, 980])
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
    reads=[]
    def reader(*args):
        reads.append(args)
        return trader.chain.quote(*args)
    assert trader.swap(pool, 20, buy, D(2), signal_minimum=signal_bound,
                       quote_reader=reader if with_quote_reader else None) == 100
    assert len(reads)==(2 if with_quote_reader else 0)
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
    balances = iter([1000, 0, 1000, 50, 980])
    trader.chain = SimpleNamespace(verify_pool=lambda *args: pool,
        balance=lambda *args: next(balances), quote=lambda *args: 100,
        contract=lambda *args: SimpleNamespace(functions=SimpleNamespace(
            swapExactTokensForTokensSupportingFeeOnTransferTokens=lambda *args: args)))
    trader.verify_router = lambda p: (V2_ROUTER, [])
    trader.approve = lambda *args: None
    trader.send = lambda *args: None
    with pytest.raises(UncertainTransaction, match="minOut"):
        trader.swap(pool, 20, True, D(2))


@pytest.mark.parametrize('returned,accepted', [(85, True), (84, False)])
def test_converter_rejects_actual_amount_roundtrip_loss(returned, accepted):
    trader = object.__new__(LiveTrader)
    pool = Pool(address('0x'+'12'*20), 'V2', address(USDT), address(WBNB), 18, 18, False)
    calls = []
    def find(target, quote):
        return [pool] if (target, quote) == (address(USDT), address(WBNB)) else []
    def quote(p, amount, reverse=False):
        calls.append((amount, not reverse))
        return returned if reverse else 200
    trader.chain = SimpleNamespace(find_pools=find, quote_route=quote)
    if accepted:
        assert trader.conversion_route(WBNB, USDT, 100) == [pool]
    else:
        with pytest.raises(ValueError, match='15%'):
            trader.conversion_route(WBNB, USDT, 100)
    assert calls == [(100, True), (200, False)]


@pytest.mark.parametrize('spent', [20,19,21])
def test_source_debit_evidence_persists_and_mismatch_latches(tmp_path,spent):
    from dipbot.persistence.storage import Store
    pool=Pool(address('0x'+'12'*20),'V2',address(USDT),address(WBNB),18,18,False)
    trader=object.__new__(LiveTrader)
    trader.owner=address('0x'+'34'*20)
    trader.store=Store(tmp_path/'state.json')
    trader.operation={'transactions':[]}
    trader.store.data['operation']=trader.operation
    receipt={'blockNumber':43,'transactionHash':b'\x12'*32}
    read_blocks=[]
    def balance_at(token,owner,number):
        read_blocks.append(number)
        return 1000
    trader.chain=SimpleNamespace(verify_pool=lambda *a:pool,balance=lambda *a:1000,
        quote=lambda *a:100,balance_at=balance_at,
        balance_snapshot=lambda *a:(10,{'blockNumber':42,'blockHash':b'a'*32}),
        receipt_balance=lambda token,*a:110 if token==pool.token else 1000-spent,
        contract=lambda *a:SimpleNamespace(functions=SimpleNamespace(
            swapExactTokensForTokensSupportingFeeOnTransferTokens=lambda *a:a)))
    trader.verify_router=lambda p:(V2_ROUTER,[])
    trader.approve=lambda *a:None
    trader.send=lambda *a:receipt
    if spent==20:
        assert trader.swap(pool,20,True,D(2))==100
    else:
        with pytest.raises(UncertainTransaction,match='Списание'):
            trader.swap(pool,20,True,D(2))
    row=Store(trader.store.path).data['operation']['asset_flows'][0]
    assert row['source_debit']==spent and row['source_matches']==(spent==20)
    assert row['snapshot_block']==42 and row['receipt_block']==43
    assert read_blocks==[42]
