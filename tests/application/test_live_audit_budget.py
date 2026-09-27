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


def test_lost_ack_injection_permits_exactly_one_send():
    from tools.live_ui_audit import lose_send_ack

    sends, noted = [], []
    send = lose_send_ack(sends.append, lambda: noted.append(True))
    with pytest.raises(TimeoutError):
        send(b"synthetic")
    with pytest.raises(RuntimeError, match="Repeated"):
        send(b"synthetic")
    assert sends == [b"synthetic"] and noted == [True]


@pytest.mark.parametrize("seconds", [0, 601])
def test_automatic_audit_deadline_checked_before_key_access(seconds):
    from tools.live_ui_audit import run

    with pytest.raises(ValueError, match="1..600"):
        run(None, None, automatic_token="unused", automatic_seconds=seconds)


def test_automatic_audit_cannot_resume_a_different_scenario():
    from tools.live_ui_audit import run

    with pytest.raises(ValueError, match="new journal"):
        run(None, None, automatic_token="unused", resume=True)


def test_explicit_automatic_pool_requires_token_before_key_access():
    from tools.live_ui_audit import run

    with pytest.raises(ValueError, match="requires a token"):
        run(None, None, automatic_pool="unused")
