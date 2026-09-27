from decimal import Decimal as D

import pytest

from tools.live_ui_audit import reserve_cost_usd


def test_historical_reservation_does_not_reprice_down():
    old = D(".44542568582050")
    first = reserve_cost_usd(old, 10**12, 10**13, D(800))
    second = reserve_cost_usd(first, 10**12, 0, D(700))
    assert first == old + D(".0088")
    assert second == first + D(".0007")


def test_budget_rejects_before_reservation():
    with pytest.raises(ValueError, match="ceiling"):
        reserve_cost_usd(".89", 10**15, 0, D(800))


@pytest.mark.parametrize(
    "old,gas,value,rate",
    [("-1", 1, 0, 800), ("NaN", 1, 0, 800), ("0", -1, 0, 800), ("0", 1, -1, 800), ("0", 1, 0, 0)],
)
def test_invalid_budget_inputs_fail_closed(old, gas, value, rate):
    with pytest.raises(ValueError):
        reserve_cost_usd(old, gas, value, rate)


def test_finality_requires_canonical_receipt_and_errors_do_not_change_execution():
    from types import SimpleNamespace as NS

    from tools.live_ui_audit import observe_finality

    checked = []
    receipt = {"blockNumber": 10, "blockHash": b"original"}
    chain = NS(w3=NS(eth=NS(get_block=lambda tag: {"number": 11})), canonical_receipt=checked.append)
    result = observe_finality(chain, receipt, timeout=1)
    assert result["observed"] and checked == [receipt]

    def reorg(receipt):
        raise ValueError("canonical hash mismatch")

    chain.canonical_receipt = reorg
    result = observe_finality(chain, receipt, timeout=1)
    assert not result["observed"] and result["errors"] == ["ValueError"]
