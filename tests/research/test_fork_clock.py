from types import SimpleNamespace as NS

import pytest

from tools.fork_roundtrip import controlled_mining, mine_local_block


def test_fork_mining_mode_restored_even_after_failure():
    calls = []

    def request(method, params):
        calls.append((method, params))
        return {"result": True}

    chain = NS(w3=NS(provider=NS(make_request=request)))
    with pytest.raises(ValueError), controlled_mining(chain):
        raise ValueError("simulated fault")
    assert calls == [("anvil_getAutomine", []), ("evm_setAutomine", [False]), ("evm_setAutomine", [True])]


@pytest.mark.parametrize("timestamp,expected", [(50, 101), (110, 111)])
def test_local_clock_advances_after_snapshot_without_loosening_freshness(monkeypatch, timestamp, expected):
    monkeypatch.setattr("tools.fork_roundtrip.time.time", lambda: 100)
    calls = []

    def request(method, params):
        calls.append((method, params))
        return {"result": True}

    chain = NS(w3=NS(eth=NS(get_block=lambda _: {"timestamp": timestamp}), provider=NS(make_request=request)))
    mine_local_block(chain)
    assert calls == [("evm_setNextBlockTimestamp", [expected]), ("evm_mine", [])]


def test_failed_timestamp_setting_does_not_mine():
    calls = []

    def request(method, params):
        calls.append(method)
        return {"error": {"code": -1}}

    chain = NS(w3=NS(eth=NS(get_block=lambda _: {"timestamp": 0}), provider=NS(make_request=request)))
    with pytest.raises(RuntimeError):
        mine_local_block(chain)
    assert calls == ["evm_setNextBlockTimestamp"]


def test_fork_cache_only_reuses_success_at_exact_height_and_rebinds_id():
    from tools.fork_roundtrip import ForkReadCache

    cache = ForkReadCache(16)
    row = {"id": 1, "method": "eth_getStorageAt", "params": ["0x123", "0x0", "0x10"]}
    cache.put(row, {"result": "0x0"})
    assert cache.get({**row, "id": 2}) == {"jsonrpc": "2.0", "id": 2, "result": "0x0"}
    for block in ("latest", "pending", "0x11"):
        different = {**row, "params": ["0x123", "0x0", block]}
        cache.put(different, {"result": "0x1"})
        assert cache.get(different) is None
    other = {**row, "params": ["0x456", "0x0", "0x10"]}
    cache.put(other, {"error": {"code": -32000}})
    assert cache.get(other) is None
    forbidden = {**row, "method": "eth_sendRawTransaction"}
    cache.put(forbidden, {"result": "0x123"})
    assert cache.get(forbidden) is None


def test_fork_batch_cache_matches_response_ids_and_only_fetches_missing():
    from tools.fork_roundtrip import ForkReadCache, cached_fork_read

    cache = ForkReadCache(16)
    rows = [{"id": i, "method": "eth_getCode", "params": ["0x" + str(i), "0x10"]} for i in (1, 2)]
    calls = []

    def fetch(payload):
        calls.append(payload)
        return [{"jsonrpc": "2.0", "id": r["id"], "result": "0x" + str(r["id"])} for r in reversed(payload)]

    first = cached_fork_read(rows, cache, fetch)
    second = cached_fork_read([{**r, "id": r["id"] + 10} for r in rows], cache, fetch)
    assert len(calls) == 1 and len(first) == 2
    assert [(r["id"], r["result"]) for r in second] == [(11, "0x1"), (12, "0x2")]


def test_cache_accepts_exact_fork_hash_but_not_another_hash():
    from tools.fork_roundtrip import ForkReadCache

    block_hash = "0x" + "a" * 64
    cache = ForkReadCache(16, block_hash)
    row = {"id": 1, "method": "eth_getCode", "params": ["0x123", block_hash]}
    cache.put(row, {"result": "0xbeef"})
    assert cache.get(row)["result"] == "0xbeef"
    changed = {**row, "params": ["0x123", "0x" + "b" * 64]}
    cache.put(changed, {"result": "0xbad"})
    assert cache.get(changed) is None
