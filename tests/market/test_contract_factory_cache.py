from copy import deepcopy

from web3 import Web3

from dipbot.domain.assets import USDT, WBNB
from dipbot.market.chain import POOL_ABI, TOKEN_ABI, Chain, address, fn


def test_cached_factory_preserves_calldata_and_isolates_bindings():
    chain = Chain("http://127.0.0.1:1")
    chain.w3.provider.make_request = lambda *a, **kw: (_ for _ in ()).throw(AssertionError("no network"))
    first = chain.contract(WBNB, TOKEN_ABI)
    second = chain.contract(USDT, TOKEN_ABI)
    assert first is not second and first.address == address(WBNB) and second.address == address(USDT)
    a = first.functions.approve(address(USDT), 123)
    b = second.functions.approve(address(WBNB), 456)
    original = chain.w3.eth.contract(address=address(WBNB), abi=TOKEN_ABI)
    assert (
        a._encode_transaction_data()
        == original.functions.approve(address(USDT), 123)._encode_transaction_data()
    )
    assert a.args != b.args
    assert len(chain._contract_factories) == 1


def test_abi_mutation_and_web3_replacement_do_not_reuse_factory():
    chain = Chain("http://127.0.0.1:1")
    abi = deepcopy(POOL_ABI)
    old = chain.contract(WBNB, abi)
    abi.append(fn("extra", outputs=("uint256",)))
    new = chain.contract(WBNB, abi)
    assert type(new) is not type(old)
    assert hasattr(new.functions, "extra")
    chain.w3 = Web3()
    replaced = chain.contract(WBNB, abi)
    assert replaced.w3 is chain.w3 and type(replaced) is not type(new)
    assert len(chain._contract_factories) == 1


def test_factory_cache_is_bounded_and_uncached_path_matches():
    chain = Chain("http://127.0.0.1:1")
    for i in range(50):
        chain.contract(WBNB, [fn("read" + str(i), outputs=("uint256",))])
    assert len(chain._contract_factories) == 32
    cached = chain.contract(WBNB, POOL_ABI).functions.getReserves()._encode_transaction_data()
    chain.contract_cache_enabled = False
    assert cached == chain.contract(WBNB, POOL_ABI).functions.getReserves()._encode_transaction_data()
