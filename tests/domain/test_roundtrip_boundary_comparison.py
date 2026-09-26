"""Native static arithmetic model versus actual Mac route selection, no RPC."""

from types import SimpleNamespace

import pytest

from dipbot.domain.assets import USDT, WBNB
from dipbot.execution.trader import LiveTrader
from dipbot.market.chain import Pool, address
from tools.native_models import roundtrip_loss_bps


@pytest.mark.parametrize(
    "amount,returned,bps,mac_accepts",
    [
        (10000, 8500, 1500, True),
        (1000001, 850000, 1500, False),
        (1000000, 849901, 1500, False),
        (1000000, 849900, 1501, False),
        (10**30, 85 * 10**28, 1500, True),
        (10**30, 85 * 10**28 - 1, 1500, False),
    ],
)
def test_fractional_bps_does_not_weaken_mac(amount, returned, bps, mac_accepts):
    assert roundtrip_loss_bps(amount, returned) == bps
    trader = object.__new__(LiveTrader)
    pool = Pool(address("0x" + "12" * 20), "V2", address(USDT), address(WBNB), 18, 18, False)
    calls = []

    def find(target, quote):
        return [pool] if (target, quote) == (address(USDT), address(WBNB)) else []

    def quote(path, value, reverse=False):
        calls.append((value, reverse))
        return returned if reverse else amount * 2

    trader.chain = SimpleNamespace(find_pools=find, quote_route=quote)
    if mac_accepts:
        assert trader.conversion_route(WBNB, USDT, amount) == [pool]
    else:
        with pytest.raises(ValueError, match="15%"):
            trader.conversion_route(WBNB, USDT, amount)
    assert calls == [(amount, False), (amount * 2, True)]


@pytest.mark.parametrize(
    "amount,returned,expected", [(0, 1, 10000), (-1, 1, 10000), (10, -1, 10000), (10, 11, 0)]
)
def test_native_loss_sentinel_and_clamping(amount, returned, expected):
    assert roundtrip_loss_bps(amount, returned) == expected
