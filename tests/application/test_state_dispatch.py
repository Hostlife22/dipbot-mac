"""State ownership and dispatch coverage, independent of market timing."""

import pytest

from dipbot.application.commands import HANDLERS
from dipbot.application.messages import CommandKind, EventKind
from dipbot.application.worker import Worker
from dipbot.domain.strategy import D
from dipbot.persistence.storage import Store
from dipbot.ui.event_handlers import HANDLERS as EVENT_HANDLERS


def test_every_wire_command_has_a_handler():
    assert set(HANDLERS) == set(CommandKind)
    # ENTRY_CHECK is diagnostic only and historically triggers control refresh.
    assert set(EVENT_HANDLERS) == set(EventKind) - {EventKind.ENTRY_CHECK}


def test_grouped_state_has_one_owner_and_is_not_shared(tmp_path):
    first = Worker(Store(tmp_path / "one.json"))
    second = Worker(Store(tmp_path / "two.json"))
    first.current_price = D("0.00000012")
    assert first.market.price == first.current_price
    first.market.current_price = D("0.00000013")
    assert first.current_price == D("0.00000013")
    first.session.paper_usd["closed"] = 2
    assert second.paper_usd["closed"] == 0
    first.entry_notice = "cooldown"
    assert first.session.entry_notice == "cooldown"
    assert second.entry_notice == ""


def test_unavailable_dependencies_cannot_be_used(tmp_path):
    worker = Worker(Store(tmp_path / "state.json"))
    for group, field in (
        (worker.connections, "reader"),
        (worker.connections, "backup"),
        (worker.market, "selected"),
        (worker.market, "price"),
        (worker.session, "executor"),
    ):
        with pytest.raises(ValueError):
            getattr(group, field)
    assert not worker.store.data.get("operation")
