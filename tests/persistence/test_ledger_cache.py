import json

import pytest

from dipbot.execution.accounting import closed_summary
from dipbot.persistence.ledger_cache import Ledger
from dipbot.persistence.storage import Store


def test_nested_mutations_never_save_stale_json(tmp_path):
    store = Store(tmp_path / "state.json")
    store.data = {
        "gas_ledger": {"tx": {"rate": {"usd": "1"}, "hashes": ["a", "b"]}},
        "operation": {"stage": "prepared"},
    }
    store.save()
    ledger = store.data["gas_ledger"]
    first = ledger.encoded()
    assert ledger.encoded() is first
    ledger["tx"]["rate"]["usd"] = "2"
    ledger["tx"]["hashes"].append("c")
    store.data["operation"]["stage"] = "submitted"
    store.save()
    assert json.loads(store.path.read_text()) == store.data
    assert Store(store.path).data["gas_ledger"]["tx"]["rate"]["usd"] == "2"
    assert ledger.encoded() != first
    cached = ledger.encoded()
    store.data["operation"]["stage"] = "confirmed"
    store.save()
    assert ledger.encoded() is cached
    assert json.loads(store.path.read_text())["operation"]["stage"] == "confirmed"


@pytest.mark.parametrize(
    "mutation",
    [
        lambda x: x["row"].__setitem__("value", 2),
        lambda x: x["row"].update(value=3),
        lambda x: x["row"].setdefault("new", 4),
        lambda x: x["row"].pop("value"),
        lambda x: x["row"].popitem(),
        lambda x: x["row"].clear(),
        lambda x: x["row"].__ior__({"new": 5}),
        lambda x: x["row"].__delitem__("value"),
        lambda x: x.__setitem__("other", {"value": 1}),
        lambda x: x.__delitem__("row"),
        lambda x: x.clear(),
        lambda x: x["row"]["items"].__setitem__(0, {"nested": [5]}),
        lambda x: x["row"]["items"].__setitem__(slice(0, 1), [9, 8]),
        lambda x: x["row"]["items"].__delitem__(0),
        lambda x: x["row"]["items"].append(3),
        lambda x: x["row"]["items"].extend([3, 4]),
        lambda x: x["row"]["items"].insert(0, 4),
        lambda x: x["row"]["items"].pop(),
        lambda x: x["row"]["items"].remove(1),
        lambda x: x["row"]["items"].clear(),
        lambda x: x["row"]["items"].reverse(),
        lambda x: x["row"]["items"].sort(reverse=True),
        lambda x: x["row"]["items"].__iadd__([5]),
        lambda x: x["row"]["items"].__imul__(2),
    ],
)
def test_all_supported_mutators_invalidate_cache(mutation):
    ledger = Ledger({"row": {"value": 1, "items": [1, 2]}})
    ledger.encoded()
    before = ledger.revision
    mutation(ledger)
    assert ledger.revision > before
    assert json.loads(ledger.encoded()) == ledger


def test_nested_replacement_and_summary_mutation(tmp_path):
    store = Store(tmp_path / "state.json")
    store.data = {"closed_trades": {"a": {"wallet": "owner", "net_usd": "2"}}}
    result = closed_summary(store, "owner")
    result["value"] = "999"
    assert closed_summary(store, "owner")["value"] == "2"
    ledger = store.data["closed_trades"]
    ledger["a"]["net_usd"] = None
    assert closed_summary(store, "owner")["value"] is None
    ledger["a"] = {"wallet": "owner", "net_usd": "3", "data": [{"usd": "1"}]}
    ledger.encoded()
    ledger["a"]["data"][0]["usd"] = "4"
    assert json.loads(ledger.encoded())["a"]["data"][0]["usd"] == "4"
    assert closed_summary(store, "owner")["value"] == "3"
    store.data["closed_trades"] = {"b": {"wallet": "owner", "net_usd": "7"}}
    assert closed_summary(store, "owner")["value"] == "7"


def test_cached_ledger_survives_failed_atomic_save(tmp_path, monkeypatch):
    import os

    store = Store(tmp_path / "state.json")
    store.data = {"gas_ledger": {"a": {"usd": "1"}}}
    store.save()
    before = store.path.read_bytes()
    store.data["gas_ledger"]["a"]["usd"] = "2"
    replace = os.replace

    def fail(*args):
        raise OSError("synthetic full disk")

    monkeypatch.setattr(os, "replace", fail)
    with pytest.raises(OSError):
        store.save()
    assert store.path.read_bytes() == before
    monkeypatch.setattr(os, "replace", replace)
    store.save()
    assert Store(store.path).data["gas_ledger"]["a"]["usd"] == "2"


def test_profile_rollback_deepcopy_is_independent(tmp_path):
    from copy import deepcopy

    store = Store(tmp_path / "state.json")
    store.data = {"closed_trades": {"a": {"wallet": "owner", "net_usd": "2", "items": [{"usd": "1"}]}}}
    store.save()
    snapshot = deepcopy(store.data)
    store.data["closed_trades"]["a"]["items"][0]["usd"] = "9"
    assert snapshot["closed_trades"]["a"]["items"][0]["usd"] == "1"
    store.data = snapshot
    store.save()
    assert Store(store.path).data["closed_trades"]["a"]["items"][0]["usd"] == "1"
    assert closed_summary(store, "owner")["value"] == "2"


def test_mutations_across_serialization_groups_keep_exact_disk_state(tmp_path, monkeypatch):
    monkeypatch.setattr(Ledger, "group_size", 2)
    store = Store(tmp_path / "state.json")
    store.data = {"gas_ledger": {str(i): {"usd": str(i), "nested": [{"value": i}]} for i in range(7)}}
    store.save()
    ledger = store.data["gas_ledger"]
    for key in ("0", "2", "6"):
        ledger[key]["nested"][0]["value"] = 99
        store.save()
        assert json.loads(store.path.read_text()) == store.data
    del ledger["1"]
    ledger["7"] = {"usd": "7"}
    store.save()
    assert json.loads(store.path.read_text()) == store.data
    ledger["0"]["nested"][0]["value"] = 100
    ledger.pop("2")
    ledger["2"] = {"usd": "200", "note": "replacement"}
    store.save()
    assert json.loads(store.path.read_text()) == store.data
    ledger.clear()
    ledger["new"] = {"usd": "1"}
    store.save()
    assert json.loads(store.path.read_text()) == store.data
