from types import SimpleNamespace
import pytest
from dipbot.chain import Pool, WBNB, USDT, address, V2_ROUTER, V3_ROUTER
from dipbot.wallet_registry import base_router
from dipbot.trader import LiveTrader
from dipbot.strategy import D


@pytest.mark.parametrize('catalogs,expected', [
    ({'V2':{'BASE':USDT},'V3':{'BASE':USDT}},'V2'),
    ({'V2':{},'V3':{'BASE':USDT}},'V3'),
    ({'V2':{'OTHER':USDT},'V3':{'BASE':USDT}},'V3'),
    ({'V2':{'BASE':WBNB},'V3':{'BASE':USDT}},'V3'),
])
def test_base_router_requires_name_and_token_then_prefers_v2(catalogs,expected):
    assert base_router('BASE',USDT,catalogs)==expected


def test_missing_base_profile_cannot_use_unrelated_router():
    with pytest.raises(ValueError,match='больше не установлен'):
        base_router('BASE',USDT,{'V2':{'OTHER':USDT},'V3':{}})


@pytest.mark.parametrize('version',['V2','V3'])
@pytest.mark.parametrize('failure',[False,True])
def test_sweep_simulates_exact_sell_after_approval_before_send(monkeypatch,version,failure):
    import dipbot.trader as module
    monkeypatch.setattr(module.time,'time',lambda:1000)
    pool=Pool(address('0x'+'12'*20),version,address(USDT),address(WBNB),18,18,False,500 if version=='V3' else 0)
    trader=object.__new__(LiveTrader);trader.owner=address('0x'+'34'*20)
    events=[];params=[]
    def simulate(tx):
        assert tx=={'from':trader.owner}
        events.append('simulate')
        if failure: raise ValueError('simulation reverted')
    function=SimpleNamespace(call=simulate)
    def build(*args):params.append(args);return function
    functions=SimpleNamespace(swapExactTokensForTokensSupportingFeeOnTransferTokens=build,exactInputSingle=build)
    balances=iter([1000,10,1000,110,980])
    trader.chain=SimpleNamespace(verify_pool=lambda *args:pool,balance=lambda *args:next(balances),
        quote=lambda *args:100,contract=lambda *args:SimpleNamespace(functions=functions))
    trader.verify_router=lambda p:(V2_ROUTER if version=='V2' else V3_ROUTER,[])
    trader.approve=lambda *args:events.append('approve')
    def send(actual,label):
        assert actual is function and label=='SELL'
        events.append('send')
    trader.send=send
    if failure:
        with pytest.raises(ValueError,match='simulation reverted'):
            trader.swap(pool,20,False,D(2),simulate=True,deadline_seconds=60)
        assert events==['approve','simulate']
    else:
        assert trader.swap(pool,20,False,D(2),simulate=True,deadline_seconds=60)==100
        assert events==['approve','simulate','send']
    assert (params[0][-1] if version=='V2' else params[0][0][4])==1060
