"""Deterministic PAPER signal-to-execution checks; no market-parity claim."""

from types import SimpleNamespace

import pytest

from dipbot.application.worker import Worker
from dipbot.domain.strategy import D
from dipbot.persistence.storage import Store
from tests.support.recovery import POOL
from tests.support.worker import config


@pytest.mark.parametrize(
    "exit_price,reason,stopped", [("0.99", "TAKE_PROFIT", False), ("0.90", "STOP_LOSS", True)]
)
def test_paper_dip_signal_executes_buy_then_exit(tmp_path, monkeypatch, exit_price, reason, stopped):
    worker = Worker(Store(tmp_path / "state.json"))
    clock = {"now": 1.0, "price": D("1")}
    monkeypatch.setattr("dipbot.application.worker.time.monotonic", lambda: clock["now"])
    worker.pool = POOL
    worker.chain = SimpleNamespace(verify_pool=lambda *args: POOL, price=lambda _: clock["price"])
    events = []
    worker.log.connect(events.append)
    data = config("PAPER") | {"token": POOL.token, "pool": POOL.address, "router": "V2"}
    worker.command("start", data)
    for price in ("1", "0.96"):
        clock["price"] = D(price)
        clock["now"] += 0.1
        worker.observe()
    assert worker.paper.position > 0 and worker.strategy.entry == D("0.96")
    clock["price"] = D(exit_price)
    clock["now"] += 0.1
    worker.observe()
    assert not worker.paper.position and worker.strategy.entry is None
    assert worker.strategy.stopped is stopped
    assert worker.running is not stopped
    assert sum("PAPER BUY:" in line for line in events) == 1
    assert any("SELL: " + reason in line for line in events)
    worker.running = False
    worker.command("start", data)
    assert worker.running and not worker.strategy.stopped
    assert worker.strategy.entry is None


def test_paper_gap_resets_base_without_buy(tmp_path, monkeypatch):
    worker = Worker(Store(tmp_path / "state.json"))
    worker.pool = POOL
    state = {"time": 1.0, "price": D("1")}
    monkeypatch.setattr("dipbot.application.worker.time.monotonic", lambda: state["time"])
    worker.chain = SimpleNamespace(verify_pool=lambda *args: POOL, price=lambda _: state["price"])
    worker.command("start", config("PAPER") | {"token": POOL.token, "pool": POOL.address})
    worker.observe()
    state.update(time=2.0, price=D("0.9"))
    worker.observe()
    assert not worker.paper.position and worker.strategy.base == D("0.9")


@pytest.mark.parametrize(
    "reason,exit_price,elapsed",
    [
        ("TRAILING_STOP", "108", 0.2),
        ("TIME_EXIT", "110", 0.5),
        ("TAKE_PROFIT", "150", 0.2),
        ("STOP_LOSS", "70", 0.2),
    ],
)
def test_worker_autonomous_exit_cooldown_and_second_dip(tmp_path, monkeypatch, reason, exit_price, elapsed):
    """Synthetic prices, real Worker execution; no START after the first command."""
    worker = Worker(Store(tmp_path / "state.json"))
    clock = {"now": 1.0, "price": D(100)}
    monkeypatch.setattr("dipbot.application.worker.time.monotonic", lambda: clock["now"])
    worker.pool = POOL
    worker.chain = SimpleNamespace(verify_pool=lambda *args: POOL, price=lambda _: clock["price"])
    data = config("PAPER") | {
        "token": POOL.token,
        "pool": POOL.address,
        "paper_policy": {"latency_seconds": 0},
        "exit_policy": {
            "trailing_pct": 1,
            "max_hold_seconds": 0.4,
            "cooldown_seconds": 2,
            "continue_after_risk_exit": True,
        },
    }
    data["settings"] = data["settings"] | {
        "amount": "1",
        "slippage": "0",
        "dynamic": "0",
        "take_profit": "50",
        "stop_loss": "20",
    }
    executions = []
    worker.log.connect(executions.append)
    events = []
    worker.event.connect(lambda kind, value: events.append((kind, value)))
    worker.command("start", data)

    def observe(now, price):
        clock.update(now=now, price=D(price))
        worker.observe()

    observe(1, "100")
    observe(1.1, "96")
    assert worker.paper.position > 0
    observe(1.2, "110")
    sold_at = 1.1 + elapsed
    observe(sold_at, exit_price)
    assert not worker.paper.position and worker.running
    assert any("SELL: " + reason in line for line in executions)
    observe(sold_at + 0.1, "50")
    assert not worker.paper.position
    observe(sold_at + 2, "50")
    assert not worker.paper.position and worker.strategy.base == D(50)
    observe(sold_at + 2.1, "48")
    assert worker.paper.position > 0 and worker.running
    assert sum("PAPER BUY:" in line for line in executions) == 2
    for index, (kind, value) in enumerate(events):
        if kind == "trade_marker":
            previous_kind, previous = events[index - 1]
            assert previous_kind == "status"
            assert (D(previous["position"]) > 0) == (value["side"] == "BUY")
            assert previous["exit_return"] is None
