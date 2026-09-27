from types import SimpleNamespace

import pytest
from web3.exceptions import ContractLogicError

from dipbot.application.errors import safe_error
from dipbot.execution.errors import SimulationRejected
from dipbot.persistence.storage import Store
from tests.support.execution import Function


@pytest.mark.parametrize("approve_first", [False, True])
def test_estimate_revert_never_signs_or_clears_journal(trader, approve_first):
    trader.begin("BUY target")
    if approve_first:
        trader.send(Function(), "APPROVE EXACT AMOUNT")

    class RejectedFunction(Function):
        def estimate_gas(self, tx):
            raise ContractLogicError("execution reverted: PancakeRouter: INSUFFICIENT_OUTPUT_AMOUNT")

    trader.account = SimpleNamespace(sign_transaction=lambda _: pytest.fail("must not sign"))
    trader.chain.w3.eth.send_raw_transaction = lambda _: pytest.fail("must not broadcast")
    with pytest.raises(SimulationRejected) as caught:
        trader.send(RejectedFunction(), "BUY")
    message = safe_error(caught.value)
    assert "выход ниже minOut" in message and "не подписана и не отправлена" in message
    records = Store(trader.store.path).data["operation"]["transactions"]
    assert len(records) == int(approve_first)
    assert all(r["label"] == "APPROVE EXACT AMOUNT" and r["status"] == "confirmed" for r in records)


@pytest.mark.parametrize(
    "message",
    [
        "execution reverted: https://node/private-key",
        "0x" + "ab" * 32,
        "https://node/PancakeRouter: INSUFFICIENT_OUTPUT_AMOUNT/private-key",
    ],
)
def test_unknown_revert_reason_is_not_exposed_or_guessed(message):
    displayed = safe_error(SimulationRejected(message))
    assert "причина не распознана" in displayed
    assert message not in displayed and "private-key" not in displayed


def test_broadcast_revert_is_not_mislabeled_as_unsubmitted(trader):
    trader.begin("BUY target")

    def fail_broadcast(_):
        raise ContractLogicError("execution reverted: PancakeRouter: INSUFFICIENT_OUTPUT_AMOUNT")

    trader.chain.w3.eth.send_raw_transaction = fail_broadcast
    with pytest.raises(Exception) as caught:
        trader.send(Function(), "BUY")
    assert not isinstance(caught.value, SimulationRejected)
    assert "не подписана и не отправлена" not in safe_error(caught.value)
    assert len(Store(trader.store.path).data["operation"]["transactions"]) == 1
