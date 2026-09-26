from decimal import Decimal

import pytest

from dipbot.application.messages import Command, CommandKind, Event, EventKind
from dipbot.application.worker import Worker
from dipbot.persistence.storage import Store


def test_enqueued_settings_are_a_snapshot(tmp_path):
    worker = Worker(Store(tmp_path / "state.json"))
    settings = {"dip": Decimal(10)}
    worker.submit("start", mode="PAPER", settings=settings)
    settings["dip"] = Decimal(99)
    message = worker.commands.get_nowait()
    assert isinstance(message, Command)
    assert message.kind is CommandKind.START
    assert message.data()["settings"]["dip"] == Decimal(10)
    with pytest.raises(TypeError):
        message.payload["mode"] = "LIVE"


@pytest.mark.parametrize(
    "name,data",
    [
        ("byu", {}),
        ("buy", {"setings": {}}),
        ("start", {"mode": "REAL"}),
        ("discover", {"generation": True}),
    ],
)
def test_bad_command_is_rejected_before_queue(tmp_path, name, data):
    worker = Worker(Store(tmp_path / "state.json"))
    with pytest.raises(ValueError):
        worker.submit(name, **data)
    assert worker.commands.empty()


def test_secret_payload_is_not_part_of_repr():
    assert "synthetic-secret" not in repr(Command.from_wire("wallet", {"key": "synthetic-secret"}))


def test_event_adapter_preserves_payload_and_rejects_unknown_name(tmp_path):
    worker = Worker(Store(tmp_path / "state.json"))
    received = []
    worker.event.connect(lambda name, data: received.append(Event.from_wire(name, data)))
    worker.emit_event(EventKind.BUSY, True)
    assert received == [Event(EventKind.BUSY, True)]
    with pytest.raises(ValueError):
        worker.emit_event("bussy", True)
