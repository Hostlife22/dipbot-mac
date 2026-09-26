from decimal import Decimal as D

import pytest

from dipbot.research.replay import ReplayCosts, pool_fee_bps


@pytest.mark.parametrize("tier,bps", [(100, 1), (500, 5), (2500, 25), (10000, 100)])
def test_v3_fee_units(tier, bps):
    fee = pool_fee_bps({"pool": {"router": "V3", "fee": tier}})
    assert fee == bps
    assert ReplayCosts(fee_bps=fee).factor == 1 - D(tier) / 1000000


def test_v2_and_legacy_assumptions_and_explicit_override():
    assert pool_fee_bps({}) == 25
    assert pool_fee_bps({"pool": "0x" + "12" * 20}) == 25
    assert pool_fee_bps({"pool": {"router": "V2", "fee": 0}}) == 25
    assert pool_fee_bps({"pool": {"router": "V3"}}, "12.5") == D("12.5")


@pytest.mark.parametrize("fee", [None, "NaN", 0, 500.5, 1234])
def test_unknown_v3_fee_requires_explicit_model(fee):
    with pytest.raises(ValueError):
        pool_fee_bps({"pool": {"router": "V3", "fee": fee}})
