"""Shared execution fixtures/builders."""
from types import SimpleNamespace
from dataclasses import asdict
import json
import pytest
from eth_account import Account
from web3 import Web3
from web3.exceptions import TransactionNotFound
from dipbot.market.chain import WBNB, USDT, Pool, address
from dipbot.persistence.storage import Store
from dipbot.execution.trader import LiveTrader
from dipbot.execution.errors import UncertainTransaction
from dipbot.domain.strategy import D


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
            return {"status": self.status, "blockNumber": 123, "transactionHash": tx_hash}
    chain = SimpleNamespace(w3=SimpleNamespace(eth=Eth()), check=lambda: 123)
    # Ephemeral, unfunded key; never leaves local test process.
    key = Account.create().key
    return LiveTrader(chain, key, store, D("0.1"), lambda _: None)
