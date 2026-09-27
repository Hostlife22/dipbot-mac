"""Optional real-EVM crash/restart checks, isolated node and ephemeral unfunded keys."""

import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.request

import pytest


@pytest.mark.parametrize("boundary,exitcode", [("send", 71), ("receipt", 72)])
def test_local_evm_process_crash_reconciles_without_resend(tmp_path, boundary, exitcode):
    binary = os.environ.get("DIPBOT_ANVIL") or shutil.which("anvil")
    if not binary:
        pytest.skip("Set DIPBOT_ANVIL to run the isolated EVM integration check")
    with socket.socket() as socket_:
        socket_.bind(("127.0.0.1", 0))
        port = socket_.getsockname()[1]
    endpoint = f"http://127.0.0.1:{port}"
    node = subprocess.Popen(
        [
            binary,
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--chain-id",
            "56",
            "--accounts",
            "0",
            "--base-fee",
            "0",
            "--silent",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    state = tmp_path / "state.json"
    crash = r"""
import os,sys
from pathlib import Path
from decimal import Decimal
from eth_account import Account
from dipbot.market.chain import Chain
from dipbot.persistence.storage import Store
from dipbot.execution.trader import LiveTrader
endpoint,path,boundary=sys.argv[1:]
assert endpoint.startswith('http://127.0.0.1:')
c=Chain(endpoint,request_timeout=2)
account=Account.create();recipient=Account.create().address
assert 'error' not in c.w3.provider.make_request('anvil_setBalance',[account.address,hex(10**18)])
c.check_receipt_access=lambda:None  # The new isolated chain has no historical receipts.
t=LiveTrader(c,account.key,Store(Path(path)),Decimal('1'),lambda _:None)
class Transfer:
 def estimate_gas(self,tx):return c.w3.eth.estimate_gas(self.build_transaction(tx))
 def build_transaction(self,tx):return {**tx,'to':recipient,'data':'0x'}
sender=c.w3.eth.send_raw_transaction
wait=c.w3.eth.wait_for_transaction_receipt
def send(raw):
 h=sender(raw)
 if boundary=='send':os._exit(71)
 return h
def receipt(*a,**kw):
 r=wait(*a,**kw)
 if boundary=='receipt':os._exit(72)
 return r
c.w3.eth.send_raw_transaction=send
c.w3.eth.wait_for_transaction_receipt=receipt
t.begin('LOCAL crash audit')
t.send(Transfer(),'LOCAL TRANSFER',value=10**12)
raise AssertionError('Crash boundary not reached')
"""
    recovery = r"""
import sys,json
from pathlib import Path
from dipbot.market.chain import Chain
from dipbot.persistence.storage import Store
from dipbot.execution.reconciliation import reconcile_receipts
from dipbot.execution.trader import LiveTrader
from dipbot.execution.errors import UncertainTransaction
c=Chain(sys.argv[1],request_timeout=2)
read=c.w3.provider.make_request
def read_only(method,params):
    assert method in {'eth_chainId','eth_getBlockByNumber','eth_getBlockByHash',
                      'eth_getTransactionReceipt','eth_getTransactionCount',
                      'eth_getTransactionByHash','eth_getBalance'}
    return read(method,params)
c.w3.provider.make_request=read_only
s=Store(Path(sys.argv[2]));owner=s.data['operation']['wallet']
reconcile_receipts(c,s,owner)
t=object.__new__(LiveTrader);t.store=s
try:t.begin('duplicate after restart')
except UncertainTransaction:pass
else:raise AssertionError('Restart allowed duplicate operation')
rows=s.data['operation']['transactions']
print(json.dumps({'statuses':[r['status'] for r in rows],
 'nonce':c.w3.eth.get_transaction_count(owner,'latest'),
 'recipient_balance':c.w3.eth.get_balance(rows[0]['request']['to']),
 'locked':bool(s.data.get('operation'))}))
"""
    try:
        deadline = time.monotonic() + 10
        while True:
            try:
                request = urllib.request.Request(
                    endpoint,
                    data=json.dumps(
                        {"jsonrpc": "2.0", "id": 1, "method": "eth_chainId", "params": []}
                    ).encode(),
                    headers={"Content-Type": "application/json"},
                )
                with urllib.request.urlopen(request, timeout=1) as response:
                    assert json.load(response)["result"] == "0x38"
                break
            except OSError:
                assert node.poll() is None and time.monotonic() < deadline
                time.sleep(0.05)
        first = subprocess.run(
            [sys.executable, "-c", crash, endpoint, str(state), boundary], capture_output=True, timeout=20
        )
        assert first.returncode == exitcode, first.stderr.decode()
        pending = json.loads(state.read_text())["operation"]["transactions"]
        assert len(pending) == 1 and pending[0]["status"] == "pending"
        second = subprocess.run(
            [sys.executable, "-c", recovery, endpoint, str(state)], capture_output=True, timeout=20
        )
        assert second.returncode == 0, second.stderr.decode()
        assert json.loads(second.stdout) == {
            "statuses": ["confirmed"],
            "nonce": 1,
            "recipient_balance": 10**12,
            "locked": True,
        }
    finally:
        node.terminate()
        try:
            node.wait(timeout=5)
        except subprocess.TimeoutExpired:
            node.kill()
            node.wait(timeout=5)
