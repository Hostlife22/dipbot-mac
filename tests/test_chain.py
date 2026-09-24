from dataclasses import replace
import pytest
from dipbot.chain import (Chain, Pool, WBNB, USDT, V2_FACTORY, V3_FACTORY, ZERO, profiles,
                          address, V3_ABI, QUOTER_ABI)
from dipbot.strategy import D
from web3 import Web3

POOL = address("0x" + "12"*20)


def fake_chain(reserves=(2*10**18, 1000*10**6, 0), sqrt=2**96):
    chain = object.__new__(Chain)
    chain.check = lambda: 123
    def call(addr, abi, name, *args, **kwargs):
        assert kwargs.get("block") == 123
        return {"getReserves": reserves, "liquidity": 1, "slot0": (sqrt,)}[name]
    chain.call = call
    return chain


def test_v2_price_with_different_decimals_both_orientations():
    c = fake_chain()
    p = Pool(POOL, "V2", WBNB, USDT, 18, 6, True)
    assert c.price(p) == 500
    inverse = replace(p, token_decimals=6, quote_decimals=18, token_is_0=False)
    assert c.price(inverse) == D("0.002")


def test_v3_q96_price_both_orientations():
    c = fake_chain(sqrt=2*2**96)
    p = Pool(POOL, "V3", WBNB, USDT, 18, 18, True, 500)
    assert c.price(p) == 4
    assert c.price(replace(p, token_is_0=False)) == D("0.25")


def test_zero_liquidity_rejected():
    with pytest.raises(ValueError, match="WAITING"):
        fake_chain(reserves=(0,1,0)).price(Pool(POOL, "V2", WBNB, USDT, 18, 18, True))


@pytest.mark.parametrize("url", ["http://example.com", "file:///tmp/rpc", "https://user:secret@host", "https://"])
def test_bad_endpoints(url):
    with pytest.raises(ValueError):
        Chain(url)


def test_profiles_extracted_and_unique():
    p = profiles()
    assert len(p) == 42 and len(set(p.values())) == 42
    assert p["WBNB"] == address(WBNB)
    assert p["USDT"] == address(USDT)


def test_v3_abi_exact_input_has_deadline_and_quoter_argument_order():
    w = Web3()
    swap = w.eth.contract(abi=V3_ABI).functions.exactInputSingle(
        (address(WBNB), address(USDT), 500, POOL, 1234, 100, 95, 0))._encode_transaction_data()
    assert swap[:10] == Web3.to_hex(Web3.keccak(text="exactInputSingle((address,address,uint24,address,uint256,uint256,uint256,uint160))")[:4])
    quote = w.eth.contract(abi=QUOTER_ABI).functions.quoteExactInputSingle(
        (address(WBNB), address(USDT), 100, 500, 0))._encode_transaction_data()
    assert quote[:10] == Web3.to_hex(Web3.keccak(text="quoteExactInputSingle((address,address,uint256,uint24,uint160))")[:4])


@pytest.mark.parametrize("bad", ["factory", "canonical", "target"])
def test_reject_forged_pool(bad):
    c = object.__new__(Chain)
    c.check = lambda: 1
    class Eth:
        def get_code(self, a): return b"code"
    c.w3 = type("W", (), {"eth": Eth()})()
    def call(addr, abi, method, *args):
        return {"factory": POOL if bad == "factory" else V2_FACTORY,
                "token0": address(WBNB), "token1": address(USDT),
                "getPair": ZERO if bad == "canonical" else POOL}[method]
    c.call = call
    with pytest.raises(ValueError):
        c.verify_pool(POOL, POOL if bad == "target" else WBNB)

