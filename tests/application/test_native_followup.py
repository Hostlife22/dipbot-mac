import json
from types import SimpleNamespace

import pytest

from dipbot.application.worker import Worker
from dipbot.execution.errors import UncertainTransaction
from dipbot.persistence import dynamic
from dipbot.persistence.storage import Store
from tests.support.discovery import OWNER
from tests.support.markets import POOL, route
from tools.protected_format import ProtectedFormatError, decode, encode


def test_envelope_serialization_and_explicit_purpose():
    calls = []

    def protect(raw, purpose):
        calls.append((raw, purpose))
        return b"synthetic ciphertext"

    raw = encode({"name": "пара", "version": 1}, "registry", protect)
    assert calls == [(b'{"name":"\\u043f\\u0430\\u0440\\u0430","version":1}', "registry")]
    assert json.loads(raw) == {
        "format": "NRNF-DPAPI",
        "version": 1,
        "payload": "c3ludGhldGljIGNpcGhlcnRleHQ=",
    }

    def unprotect(data, purpose):
        assert (data, purpose) == (b"synthetic ciphertext", "registry")
        return calls[0][0]

    assert decode(raw, "registry", unprotect) == {"name": "пара", "version": 1}


@pytest.mark.parametrize(
    "envelope",
    [
        [],
        {},
        {"format": "other"},
        {"format": "NRNF-DPAPI", "version": 2, "payload": "eA=="},
        {"format": "NRNF-DPAPI", "version": 1, "payload": "eA==\n"},
        {"format": "NRNF-DPAPI", "version": 1, "payload": "!"},
    ],
)
def test_invalid_envelope_never_reaches_unprotect(envelope):
    calls = []
    with pytest.raises(ProtectedFormatError):
        decode(json.dumps(envelope).encode(), "test", lambda *args: calls.append(args))
    assert not calls


@pytest.mark.parametrize("plain", [b"[]", b"null", b"bad json", b"\xff"])
def test_protected_payload_must_decode_to_object(plain):
    raw = encode({}, "test", lambda *_: b"x")
    with pytest.raises(ProtectedFormatError):
        decode(raw, "test", lambda *_: plain)


def test_native_string_version_and_generic_protection_error():
    raw = b'{"format":"NRNF-DPAPI","version":"1","payload":"eA=="}'
    assert decode(raw, "test", lambda *_: b"{}") == {}

    def fail(*args):
        raise RuntimeError("sensitive test detail")

    with pytest.raises(ProtectedFormatError) as error:
        decode(raw, "test", fail)
    assert "sensitive" not in str(error.value)


def test_remove_with_unfinished_operation_does_not_read_balances(tmp_path):
    worker = Worker(Store(tmp_path / "state.json"))
    row = dynamic.upsert(worker.store, POOL, route(), 100)
    worker.store.data["operation"] = {"status": "pending"}
    worker.chain = SimpleNamespace(balance=lambda *_: pytest.fail("must not read"))
    with pytest.raises(UncertainTransaction):
        worker.command("remove_profile", {"symbol": row["name"], "wallet": OWNER})
    assert dynamic.records(worker.store)


def test_remove_emits_refresh_only_after_catalog_persisted(tmp_path):
    worker = Worker(Store(tmp_path / "state.json"))
    row = dynamic.upsert(worker.store, POOL, route(), 100)
    worker.chain = SimpleNamespace(balance=lambda *_: 0)
    events = []

    def receive(name, payload):
        if name == "profile_removed":
            assert not dynamic.records(Store(worker.store.path))
        events.append(name)

    worker.event.connect(receive)
    worker.command("remove_profile", {"symbol": row["name"], "wallet": OWNER})
    assert events == ["pools", "profiles", "profile_removed"]
