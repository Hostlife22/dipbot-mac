from types import SimpleNamespace

import pytest

from dipbot.domain.assets import USDT, V2_FACTORY, WBNB
from dipbot.market.chain import Chain, address

POOL = address("0x" + "12" * 20)


def fixture(monkeypatch):
    chain = object.__new__(Chain)
    chain.checked_header = {"number": 123, "hash": b"a" * 32}
    chain.check = lambda: 123
    seen = []
    chain.w3 = SimpleNamespace(
        eth=SimpleNamespace(get_code=lambda a, **kw: seen.append(("code", kw["block_identifier"])) or b"code")
    )

    def call(a, abi, name, *args, block):
        seen.append((name, block))
        return {"getPair": POOL, "factory": V2_FACTORY, "token0": WBNB, "token1": USDT}[name]

    chain.call = call
    chain.decimals = lambda a: 18
    chain.canonical_receipt = lambda r: seen.append(("canonical", r["blockNumber"]))
    chain.price = lambda p: 1
    monkeypatch.setattr(
        "dipbot.market.discovery.batch",
        lambda c, rs, b: [c.call(a, abi, n, *args, block=b) for a, abi, n, args in rs],
    )
    return chain, seen


def test_all_identity_reads_are_pinned_and_checked(monkeypatch):
    chain, seen = fixture(monkeypatch)
    pool = chain.verify_pool(POOL, WBNB)
    assert pool.token == address(WBNB) and pool.quote == address(USDT)
    assert seen == [
        ("code", 123),
        ("factory", 123),
        ("token0", 123),
        ("token1", 123),
        ("getPair", 123),
        ("canonical", 123),
    ]
    chain.identity_multicall_enabled = False
    assert chain.verify_pool(POOL, WBNB) == pool


@pytest.mark.parametrize(
    "value", [[None, WBNB, USDT], [V2_FACTORY, None, USDT], [V2_FACTORY, WBNB, None], []]
)
def test_partial_multicall_does_not_validate_pool(monkeypatch, value):
    chain, _ = fixture(monkeypatch)
    monkeypatch.setattr("dipbot.market.discovery.batch", lambda *a: value)
    with pytest.raises(ValueError):
        chain.verify_pool(POOL, WBNB)


def test_reorg_during_identity_read_prevents_success(monkeypatch):
    chain, _ = fixture(monkeypatch)

    def reorg(receipt):
        raise ValueError("changed block")

    chain.canonical_receipt = reorg
    with pytest.raises(ValueError, match="changed block"):
        chain.verify_pool(POOL, WBNB)
