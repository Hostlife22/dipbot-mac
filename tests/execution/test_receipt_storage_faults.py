"""Real receipt-save fsync faults with one offline signed broadcast."""

import os
import stat

import pytest

from dipbot.execution.errors import UncertainTransaction
from dipbot.execution.trader import LiveTrader
from dipbot.persistence.storage import SaveAfterReplaceError, Store
from tests.support.execution import Function


@pytest.mark.parametrize("status", [0, 1])
@pytest.mark.parametrize("boundary", ["file_sync", "dir_sync"])
def test_receipt_fsync_fault_never_allows_second_broadcast(trader, monkeypatch, status, boundary):
    eth = trader.chain.w3.eth
    eth.status = status
    sends = []
    state = {"receipt": False}
    send, wait, sync = eth.send_raw_transaction, eth.wait_for_transaction_receipt, os.fsync

    def broadcast(raw):
        sends.append(raw)
        return send(raw)

    def receipt(*a, **k):
        result = wait(*a, **k)
        state["receipt"] = True
        return result

    def fsync(fd):
        is_dir = stat.S_ISDIR(os.fstat(fd).st_mode)
        if state["receipt"] and boundary == ("dir_sync" if is_dir else "file_sync"):
            raise OSError("synthetic receipt storage failure")
        return sync(fd)

    eth.send_raw_transaction = broadcast
    eth.wait_for_transaction_receipt = receipt
    monkeypatch.setattr(os, "fsync", fsync)
    trader.begin("synthetic receipt boundary")
    with pytest.raises(SaveAfterReplaceError if boundary == "dir_sync" else OSError):
        trader.send(Function(), "synthetic")
    assert len(sends) == 1
    with pytest.raises(UncertainTransaction):
        trader.begin("second token")
    recovered = object.__new__(LiveTrader)
    recovered.store = Store(trader.store.path)
    recovered.owner = trader.owner
    recovered.chain = trader.chain
    row = recovered.store.data["operation"]["transactions"][0]
    expected = ("confirmed" if status else "reverted") if boundary == "dir_sync" else "pending"
    assert row["status"] == expected
    with pytest.raises(UncertainTransaction):
        recovered.begin("restart duplicate")
    state["receipt"] = False
    eth.get_transaction_receipt = lambda h: {"status": status, "blockNumber": 123, "transactionHash": h}
    recovered.reconcile()
    with pytest.raises(UncertainTransaction):
        recovered.begin("requires balance review")
    assert len(sends) == 1


def test_error_after_receipt_persist_does_not_mean_no_transaction(trader):
    trader.begin("synthetic late error")

    def log(message):
        if message.startswith("Подтверждено:"):
            raise RuntimeError("synthetic progress callback failure")

    trader.log = log
    with pytest.raises(RuntimeError, match="progress callback"):
        trader.send(Function(), "synthetic")
    restored = Store(trader.store.path)
    assert restored.data["operation"]["transactions"][0]["status"] == "confirmed"
    with pytest.raises(UncertainTransaction):
        trader.begin("must not repeat")
