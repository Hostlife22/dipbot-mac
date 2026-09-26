"""Corrupt nested records must not erase pending intent or overwrite the source."""

import json
from copy import deepcopy

import pytest

from dipbot.persistence.schema import load_state
from dipbot.persistence.storage import Store


@pytest.mark.parametrize(
    "record",
    [
        {"positions": {"synthetic": {"amount": True}}},
        {"positions": {"synthetic": {"pool": {"token_decimals": "18"}}}},
        {"positions": {"synthetic": []}},
        {"operation": {"transactions": {}}},
        {"operation": {"transactions": [{"nonce": False}]}},
        {"operation": {"transactions": [{"request": []}]}},
        {"history": [{"transactions": [None]}]},
        {"operation": {"transactions": [{"request": {"nonce": "1"}}]}},
    ],
)
def test_nested_corruption_does_not_overwrite_file(tmp_path, record):
    path = tmp_path / "state.json"
    original = json.dumps(record)
    path.write_text(original)
    with pytest.raises(ValueError):
        Store(path)
    assert path.read_text() == original


def test_sparse_legacy_records_and_extensions_remain_intact():
    original = {
        "positions": {"synthetic": {"amount": 2, "future": {"version": 3}}},
        "operation": {"transactions": [{"hash": "synthetic", "future": [1, 2]}]},
        "history": [{"description": "legacy"}],
    }
    expected = deepcopy(original)
    result = load_state(original)
    assert original == expected
    assert {k: v for k, v in result.items() if k != "state_version"} == expected


@pytest.mark.parametrize(
    "row",
    [
        [],
        {"usd": "NaN"},
        {"net_usd": "Infinity"},
        {"wei": 1},
        {"entry_gas_hashes": [1]},
        {"exit_fees": {"usd": "broken"}},
        {"inventory_matches": 1},
        {"block": True},
        {"residual_raw": -1},
    ],
)
@pytest.mark.parametrize("ledger", ["closed_trades", "gas_ledger"])
def test_financial_corruption_preserves_last_durable_state(tmp_path, ledger, row):
    store = Store(tmp_path / "state.json")
    store.data[ledger] = {"known": {"usd": "0.01", "extension": {"future": 2}}}
    store.save()
    before = store.path.read_bytes()
    store.ledger(ledger)["bad"] = row
    with pytest.raises(ValueError):
        store.save()
    assert store.path.read_bytes() == before
    assert Store(store.path).data[ledger]["known"]["extension"] == {"future": 2}


def test_nested_financial_mutation_invalidates_validation_cache(tmp_path):
    store = Store(tmp_path / "state.json")
    store.data["closed_trades"] = {"tx": {"exit_fees": {"usd": None, "wei": "1"}}}
    store.save()
    store.save()  # validated group is cached
    before = store.path.read_bytes()
    store.ledger("closed_trades")["tx"]["exit_fees"]["usd"] = "NaN"
    with pytest.raises(ValueError):
        store.save()
    assert store.path.read_bytes() == before
    store.ledger("closed_trades")["tx"]["exit_fees"]["usd"] = None
    store.save()
    assert Store(store.path).data["closed_trades"]["tx"]["exit_fees"]["usd"] is None


def test_schema_checks_only_changed_ledger_groups(monkeypatch):
    from dipbot.persistence.ledger_cache import Ledger

    monkeypatch.setattr(Ledger, "group_size", 2)
    ledger = Ledger({str(i): {"usd": str(i)} for i in range(6)})
    calls = []

    def validate(row):
        calls.append(row["usd"])

    ledger.validate_rows(validate)
    assert len(calls) == 6
    ledger.validate_rows(validate)
    assert len(calls) == 6
    ledger["3"]["usd"] = "7"
    ledger.validate_rows(validate)
    assert calls[6:] == ["2", "7"]
    del ledger["0"]
    ledger.validate_rows(validate)
    assert len(calls) == 13
