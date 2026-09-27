"""Router batching must retain identity checks and fail closed on incomplete reads."""

from types import SimpleNamespace as NS

import pytest

from dipbot.domain.assets import USDT, V2_FACTORY, V3_FACTORY, WBNB
from dipbot.execution.trader import LiveTrader
from dipbot.market.chain import Chain, Pool, address


@pytest.mark.parametrize("version,factory,wrapped", [("V2", V2_FACTORY, "WETH"), ("V3", V3_FACTORY, "WETH9")])
@pytest.mark.parametrize("enabled", [False, True])
def test_router_reads_are_fresh_and_wrong_identity_stops_execution(
    monkeypatch, version, factory, wrapped, enabled
):
    chain = object.__new__(Chain)
    chain.router_identity_multicall_enabled = enabled
    reads = []
    values = [factory, WBNB]
    chain.w3 = NS(eth=NS(get_code=lambda addr: reads.append("code") or b"code"))
    chain.call = lambda addr, abi, method: values[0 if method == "factory" else 1]

    def batch(c, requests, block):
        assert block == "latest" and [r[2] for r in requests] == ["factory", wrapped]
        reads.append("batch")
        return list(values)

    monkeypatch.setattr("dipbot.market.discovery.batch", batch)
    trader = object.__new__(LiveTrader)
    trader.chain = chain
    pool = Pool(address("0x" + "12" * 20), version, address(USDT), address(WBNB), 18, 18, True)
    trader.verify_router(pool)
    values[0] = address("0x" + "34" * 20)
    with pytest.raises(ValueError, match="Factory"):
        trader.verify_router(pool)
    values[:] = [factory, address(USDT)]
    with pytest.raises(ValueError, match="WBNB"):
        trader.verify_router(pool)
    assert reads.count("code") == 3
    assert reads.count("batch") == (3 if enabled else 0)


@pytest.mark.parametrize(
    "code,result", [(b"", [V2_FACTORY, WBNB]), (b"code", [None, WBNB]), (b"code", [V2_FACTORY, None])]
)
def test_missing_code_or_partial_multicall_cannot_pass(monkeypatch, code, result):
    chain = object.__new__(Chain)
    chain.w3 = NS(eth=NS(get_code=lambda addr: code))
    chain.call = lambda *args: pytest.fail("No silent fallback after failed identity")
    monkeypatch.setattr("dipbot.market.discovery.batch", lambda *a: result)
    with pytest.raises(ValueError):
        chain.router_identity(address("0x" + "12" * 20), [], "WETH")


def test_router_rpc_timeout_is_not_treated_as_success(monkeypatch):
    chain = object.__new__(Chain)
    chain.w3 = NS(eth=NS(get_code=lambda addr: b"code"))

    def timeout(*a):
        raise TimeoutError("synthetic")

    monkeypatch.setattr("dipbot.market.discovery.batch", timeout)
    with pytest.raises(TimeoutError):
        chain.router_identity(address("0x" + "12" * 20), [], "WETH")
