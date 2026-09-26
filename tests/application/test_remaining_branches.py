"""Synthetic REMOVE/Sweep/format regressions; no Windows execution or RPC."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from dipbot.application.worker import Worker
from dipbot.domain.strategy import D
from dipbot.execution.errors import UncertainTransaction
from dipbot.persistence import dynamic
from dipbot.persistence.storage import Store
from tests.support.discovery import OWNER
from tests.support.execution import Function
from tests.support.markets import POOL, route
from tests.support.recovery import POOL as HELD
from tests.support.recovery import sweep_worker
from tools.protected_format import ProtectedFormatError, decode, encode


@pytest.mark.parametrize("failure", ["balance", "save"])
def test_remove_failure_retains_registry_and_does_not_emit_success(tmp_path, failure):
    worker = Worker(Store(tmp_path / "state.json"))
    row = dynamic.upsert(worker.store, POOL, route(), 100)
    before = deepcopy(worker.store.data)
    events = []
    worker.event.connect(lambda name, value: events.append(name))

    def fail(*args):
        raise OSError("synthetic fault")

    worker.chain = SimpleNamespace(balance=fail if failure == "balance" else lambda *_: 0)
    if failure == "save":
        worker.store.save = fail
    with pytest.raises(OSError):
        worker.command("remove_profile", {"symbol": row["name"], "wallet": OWNER})
    assert worker.store.data == before and Store(worker.store.path).data == before
    assert "profile_removed" not in events and "profiles" not in events


def test_sweep_residual_keeps_position_and_entry_across_restart(tmp_path):
    worker, sent = sweep_worker(tmp_path)
    worker.chain.balance = lambda token, owner: 1 if token == HELD.token else 0
    worker.live.swap = lambda *a, **k: None
    worker.sweep()
    assert len(sent) == 1
    saved = Store(worker.store.path).data["positions"]
    assert len(saved) == 1 and next(iter(saved.values()))["amount"] == 1
    assert worker.strategy.entry is not None


def test_sweep_unreadable_post_receipt_balance_does_not_finish(trader):
    worker = Worker(trader.store)
    worker.mode = "LIVE"
    worker.pool = HELD
    worker.live = trader
    worker.chain = trader.chain
    worker.set_position(200, D("1.2"))
    confirmed = False

    def swap(*a, **k):
        nonlocal confirmed
        trader.send(Function(), "synthetic SELL")
        confirmed = True

    def balance(token, owner):
        if confirmed:
            raise TimeoutError("synthetic failure")
        return 200 if token == HELD.token else 0

    worker.chain.balance = balance
    worker.chain.verify_pool = lambda *_: HELD
    worker.chain.quote = lambda *_: 190
    trader.swap = swap
    with pytest.raises(UncertainTransaction, match="остаток"):
        worker.sweep()
    restored = Store(worker.store.path)
    assert restored.data["positions"]
    assert restored.data["operation"]["transactions"][0]["status"] == "confirmed"
    trader.store = restored
    with pytest.raises(UncertainTransaction):
        trader.begin("must reconcile before another trade")


@pytest.mark.parametrize("version", [0, 2, -1, "future", None])
def test_unknown_protected_version_is_not_silently_migrated(version):
    import json

    envelope = json.loads(encode({"version": 1}, "test", lambda *_: b"synthetic"))
    envelope["version"] = version
    with pytest.raises(ProtectedFormatError):
        decode(json.dumps(envelope).encode(), "test", lambda *_: pytest.fail("must reject before decrypt"))
