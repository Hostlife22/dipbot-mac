from copy import deepcopy
from dataclasses import asdict
from types import SimpleNamespace

import pytest

from dipbot.execution.recovery import compare_positions
from dipbot.persistence.storage import Store
from tests.support.markets import POOL


@pytest.mark.parametrize("actual,match", [(100, True), (99, False)])
def test_comparison_pins_block_and_preserves_records(tmp_path, actual, match):
    store = Store(tmp_path / "state.json")
    store.data["positions"] = {"0x" + "34" * 20 + ":" + POOL.address: {"amount": 100, "pool": asdict(POOL)}}
    store.data["operation"] = {"pending": True}
    before = deepcopy(store.data)
    calls = []
    chain = SimpleNamespace(
        check=lambda **kw: 12,
        w3=SimpleNamespace(eth=SimpleNamespace(get_block=lambda n: {"hash": b"a"})),
        balance_at=lambda t, o, n: calls.append(n) or actual,
    )
    result = compare_positions(chain, store)
    assert result["rows"][0]["matches"] == match and calls == [12]
    assert store.data == before
    hashes = iter([b"a", b"b"])
    chain.w3.eth.get_block = lambda n: {"hash": next(hashes)}
    with pytest.raises(ValueError, match="Блок изменился"):
        compare_positions(chain, store)
    assert store.data == before


def test_ui_shows_mismatch_and_failed_comparison(window):
    window.on_event(
        "position_comparison",
        {
            "block": 12,
            "rows": [
                {
                    "owner": "owner",
                    "token": "token",
                    "pool": "pool",
                    "decimals": 2,
                    "saved_raw": 100,
                    "actual_raw": 99,
                    "matches": False,
                }
            ],
        },
    )
    assert "РАСХОЖДЕНИЕ" in window.position_comparison.text()
    assert "0.99" in window.position_comparison.text()
    window.on_event("position_comparison_error", "TimeoutError")
    assert "Сверка не выполнена" in window.position_comparison.text()


def test_same_wallet_token_is_compared_as_total_not_per_pool(tmp_path):
    store = Store(tmp_path / "state.json")
    owner = "0x" + "34" * 20
    second = asdict(POOL) | {"address": "0x" + "56" * 20}
    store.data["positions"] = {
        owner + ":a": {"amount": 100, "pool": asdict(POOL)},
        owner + ":b": {"amount": 50, "pool": second},
    }
    calls = []
    chain = SimpleNamespace(
        check=lambda **kw: 12,
        w3=SimpleNamespace(eth=SimpleNamespace(get_block=lambda n: {"hash": b"a"})),
        balance_at=lambda *args: calls.append(args) or 150,
    )
    result = compare_positions(chain, store)
    assert len(result["rows"]) == len(calls) == 1
    assert result["rows"][0]["matches"] and result["rows"][0]["saved_raw"] == 150
    assert result["rows"][0]["position_count"] == 2
    second["token_decimals"] += 1
    with pytest.raises(ValueError, match="decimals"):
        compare_positions(chain, store)
