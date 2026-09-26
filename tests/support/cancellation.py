"""Shared cancellation fixtures/builders."""

from types import SimpleNamespace as NS

from eth_account import Account
from web3 import Web3
from web3.exceptions import TransactionNotFound

from dipbot.execution.trader import LiveTrader
from dipbot.persistence.storage import Store

H = "0x" + "12" * 32


def original():
    return {
        "hash": H,
        "nonce": 0,
        "status": "pending",
        "request": {"chainId": 56, "nonce": 0, "gasPrice": 10**8},
    }


def make(tmp_path, *, timeout=False):
    account = Account.create()
    store = Store(tmp_path / "state.json")
    store.data["operation"] = {"wallet": account.address, "transactions": [original()]}
    sent = []
    receipts = {}

    def get_receipt(h):
        if h not in receipts:
            raise TransactionNotFound("synthetic missing")
        return receipts[h]

    def send(raw):
        tx_hash = Web3.to_hex(Web3.keccak(raw))
        assert Store(store.path).data["operation"]["transactions"][-1]["hash"] == tx_hash
        sent.append(raw)
        if timeout:
            raise TimeoutError("synthetic timeout")
        receipts[tx_hash] = {
            "transactionHash": bytes.fromhex(tx_hash[2:]),
            "status": 1,
            "blockNumber": 42,
            "blockHash": b"b" * 32,
            "gasUsed": 21000,
            "effectiveGasPrice": 125000000,
        }
        return bytes.fromhex(tx_hash[2:])

    eth = NS(
        get_transaction_receipt=get_receipt,
        get_transaction_count=lambda *a: 0,
        get_code=lambda *a: b"",
        get_balance=lambda *a: 10**18,
        send_raw_transaction=send,
        wait_for_transaction_receipt=lambda h, **kw: receipts[h],
    )
    chain = NS(check=lambda: 42, w3=NS(eth=eth), canonical_receipt=lambda r: None)
    trader = object.__new__(LiveTrader)
    trader.account = account
    trader.owner = account.address
    trader.store = store
    trader.chain = chain
    trader.gas_price = 10**8
    return trader, sent, receipts
