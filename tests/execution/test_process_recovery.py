from tests.support.crash import SCRIPT

"""Abrupt interpreter death with an unfunded ephemeral key and a fake provider."""
import subprocess
import sys

import pytest

from dipbot.execution.errors import UncertainTransaction
from dipbot.execution.trader import LiveTrader
from dipbot.persistence.storage import Store


@pytest.mark.parametrize(
    "boundary,exitcode,count,status",
    [
        ("intent", 70, 0, None),
        ("broadcast", 71, 1, "pending"),
        ("receipt", 72, 1, "pending"),
        ("accounting", 73, 1, "confirmed"),
    ],
)
def test_abrupt_exit_retains_durable_operation(tmp_path, boundary, exitcode, count, status):
    path = tmp_path / "state.json"
    result = subprocess.run(
        [sys.executable, "-c", SCRIPT, str(path), boundary], timeout=20, capture_output=True
    )
    assert result.returncode == exitcode, result.stderr.decode()
    store = Store(path)
    tx = store.data["operation"]["transactions"]
    assert len(tx) == count
    if status:
        assert tx[0]["status"] == status
    trader = object.__new__(LiveTrader)
    trader.store = store
    with pytest.raises(UncertainTransaction):
        trader.begin("must not retry after crash")


@pytest.mark.parametrize(
    "boundary,exitcode,count", [("signed", 74, 1), ("broadcast", 71, 2), ("receipt", 72, 2)]
)
def test_cancel_process_death_keeps_original_and_any_prepared_replacement(
    tmp_path, boundary, exitcode, count
):
    injection = r"""
from dipbot.execution.cancellation import cancel_pending
original_hash='0x'+'12'*32
t.operation['transactions']=[{'hash':original_hash,'nonce':0,'status':'pending',
    'request':{'chainId':56,'nonce':0,'gasPrice':100000000}}]
store.save()
from web3.exceptions import TransactionNotFound
def missing(*a):raise TransactionNotFound('synthetic missing')
t.chain.w3.eth.get_transaction_receipt=missing
t.chain.w3.eth.get_code=lambda *a:b''
account=t.account
class Signer:
 def sign_transaction(self,tx):
  signed=account.sign_transaction(tx)
  if boundary=='signed':os._exit(74)
  return signed
t.account=Signer()
cancel_pending(t,expected_hash=original_hash,expected_gas_price=125000000)
"""
    script = SCRIPT.replace("t.send(Function(),'synthetic')", injection)
    path = tmp_path / "state.json"
    result = subprocess.run(
        [sys.executable, "-c", script, str(path), boundary], timeout=20, capture_output=True
    )
    assert result.returncode == exitcode, result.stderr.decode()
    store = Store(path)
    rows = store.data["operation"]["transactions"]
    assert len(rows) == count and all(r["status"] == "pending" for r in rows)
    if count == 2:
        assert rows[1]["replaces"] == rows[0]["hash"]
        assert rows[1]["request"]["value"] == 0
    trader = object.__new__(LiveTrader)
    trader.store = store
    with pytest.raises(UncertainTransaction):
        trader.begin("no restart rebroadcast")
