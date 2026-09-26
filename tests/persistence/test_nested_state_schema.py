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
