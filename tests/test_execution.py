from types import SimpleNamespace
from dataclasses import asdict
import json
import pytest
from eth_account import Account
from web3 import Web3
from web3.exceptions import TransactionNotFound

from dipbot.chain import WBNB, USDT, Pool, address
from dipbot.storage import Store
from dipbot.trader import LiveTrader, UncertainTransaction
from dipbot.strategy import D


class Function:
    def estimate_gas(self, tx):
        return 21000

    def build_transaction(self, tx):
        return {**tx, "to": address(WBNB), "data": "0x"}


@pytest.fixture
def trader(tmp_path):
    store = Store(tmp_path / "state.json")
    class Eth:
        fail_send = False
        status = 1
        pending = False
        def get_transaction_count(self, owner, kind):
            return 1 if self.pending and kind == "pending" else 0
        def get_balance(self, owner): return 10**18
        def send_raw_transaction(self, raw):
            # Actual broadcast is replaced by this offline fake.
            persisted = json.loads(store.path.read_text())
            assert persisted["operation"]["transactions"][-1]["hash"] == Web3.to_hex(Web3.keccak(raw))
            if self.fail_send:
                raise TimeoutError("private RPC url must not leak")
            return Web3.keccak(raw)
        def wait_for_transaction_receipt(self, tx_hash, **kwargs):
            return {"status": self.status, "blockNumber": 123}
    chain = SimpleNamespace(w3=SimpleNamespace(eth=Eth()), check=lambda: 123)
    # Ephemeral, unfunded key; never leaves local test process.
    key = Account.create().key
    return LiveTrader(chain, key, store, D("0.1"), lambda _: None)


def test_hash_persisted_before_broadcast_and_confirmed(trader):
    trader.begin("test")
    trader.send(Function(), "test")
    record = trader.store.data["operation"]["transactions"][0]
    assert record["status"] == "confirmed"
    trader.finish()
    assert "operation" not in Store(trader.store.path).data
    assert len(trader.store.data["history"]) == 1


def test_timeout_survives_restart_and_prevents_duplicate(trader):
    trader.begin("test")
    trader.chain.w3.eth.fail_send = True
    with pytest.raises(UncertainTransaction):
        trader.send(Function(), "test")
    trader.store = Store(trader.store.path)
    with pytest.raises(UncertainTransaction):
        trader.begin("must not retry")
    assert trader.store.data["operation"]["transactions"][0]["status"] == "pending"


def test_revert_does_not_clear_operation(trader):
    trader.begin("test")
    trader.chain.w3.eth.status = 0
    with pytest.raises(RuntimeError):
        trader.send(Function(), "test")
    assert trader.store.data["operation"]["transactions"][0]["status"] == "reverted"


def test_external_pending_nonce_blocks_send(trader):
    trader.begin("test")
    trader.chain.w3.eth.pending = True
    with pytest.raises(UncertainTransaction, match="pending"):
        trader.send(Function(), "test")
    assert not trader.operation["transactions"]


def test_gas_cap_blocks_before_broadcast(trader):
    trader.begin("test")
    trader.max_fee = 1
    with pytest.raises(ValueError, match="комиссия"):
        trader.send(Function(), "test")
    assert not trader.operation["transactions"]


def test_no_send_without_journal(trader):
    with pytest.raises(RuntimeError):
        trader.send(Function(), "test")


def test_reconcile_missing_receipt_stays_locked(trader):
    trader.begin("test")
    trader.send(Function(), "test")
    def missing(tx): raise TransactionNotFound("missing")
    trader.chain.w3.eth.get_transaction_receipt = missing
    with pytest.raises(UncertainTransaction):
        trader.reconcile()
    assert trader.store.data.get("operation")


def test_reconcile_confirmed_does_not_implicitly_unlock(trader):
    trader.begin("test")
    trader.send(Function(), "test")
    trader.chain.w3.eth.get_transaction_receipt = lambda _: {"status": 1}
    trader.reconcile()
    assert trader.store.data.get("operation")


def test_approve_is_exact_and_resets_nonzero(trader):
    calls = []
    trader.chain.call = lambda *args: 5
    trader.chain.contract = lambda *args: SimpleNamespace(functions=SimpleNamespace(approve=lambda dst, n: n))
    trader.send = lambda function, label: calls.append(function)
    trader.approve(WBNB, USDT, 100)
    assert calls == [0, 100]


@pytest.mark.parametrize('boundary', ['hash', 'receipt'])
def test_disk_failure_at_send_boundaries_survives_restart(trader, boundary):
    trader.begin('crash boundary')
    save = trader.store.save
    count = 0
    broadcasts = []
    broadcast = trader.chain.w3.eth.send_raw_transaction
    def send(raw):
        broadcasts.append(raw)
        return broadcast(raw)
    trader.chain.w3.eth.send_raw_transaction = send
    def fault():
        nonlocal count
        count += 1
        if count == (1 if boundary == 'hash' else 2):
            raise OSError('simulated disk failure')
        save()
    trader.store.save = fault
    with pytest.raises(OSError): trader.send(Function(), 'test')
    assert len(broadcasts) == (0 if boundary == 'hash' else 1)
    reloaded = Store(trader.store.path)
    assert reloaded.data['operation']
    if boundary == 'receipt':
        assert reloaded.data['operation']['transactions'][0]['status'] == 'pending'
    trader.store = reloaded
    with pytest.raises(UncertainTransaction): trader.begin('duplicate after restart')


def test_receipt_timeout_after_accepted_send_never_unlocks(trader):
    trader.begin('accepted but receipt unknown')
    def timeout(*args, **kwargs): raise TimeoutError('receipt timeout')
    trader.chain.w3.eth.wait_for_transaction_receipt = timeout
    with pytest.raises(UncertainTransaction): trader.send(Function(), 'test')
    trader.store = Store(trader.store.path)
    assert trader.store.data['operation']['transactions'][0]['status'] == 'pending'
    with pytest.raises(UncertainTransaction): trader.begin('duplicate')
