"""Abrupt interpreter death with an unfunded ephemeral key and a fake provider."""
import subprocess
import sys
import os
import pytest
from dipbot.storage import Store
from dipbot.trader import LiveTrader, UncertainTransaction

SCRIPT = r'''
import os,sys
from types import SimpleNamespace
from eth_account import Account
from web3 import Web3
from dipbot.storage import Store
from dipbot.trader import LiveTrader
from dipbot.strategy import D
from dipbot.chain import WBNB,address
store=Store(sys.argv[1]);boundary=sys.argv[2]
class Function:
 def estimate_gas(self,tx):return 21000
 def build_transaction(self,tx):return {**tx,'to':address(WBNB),'data':'0x'}
class Eth:
 def get_transaction_count(self,*args):return 0
 def get_balance(self,*args):return 10**18
 def send_raw_transaction(self,raw):
  if boundary=='broadcast':os._exit(71)
  return Web3.keccak(raw)
 def wait_for_transaction_receipt(self,*args,**kwargs):
  if boundary=='receipt':os._exit(72)
  return {'status':1,'blockNumber':123}
t=LiveTrader(SimpleNamespace(check=lambda:123,w3=SimpleNamespace(eth=Eth())),
             Account.create().key,store,D('0.1'),lambda _:None)
t.begin('synthetic process crash')
if boundary=='intent':os._exit(70)
t.send(Function(),'synthetic')
os._exit(73)
'''


@pytest.mark.parametrize('boundary,exitcode,count,status',[
    ('intent',70,0,None),('broadcast',71,1,'pending'),('receipt',72,1,'pending'),('accounting',73,1,'confirmed')])
def test_abrupt_exit_retains_durable_operation(tmp_path,boundary,exitcode,count,status):
    path=tmp_path/'state.json'
    result=subprocess.run([sys.executable,'-c',SCRIPT,str(path),boundary],timeout=20,capture_output=True)
    assert result.returncode==exitcode,result.stderr.decode()
    store=Store(path);tx=store.data['operation']['transactions']
    assert len(tx)==count
    if status: assert tx[0]['status']==status
    trader=object.__new__(LiveTrader);trader.store=store
    with pytest.raises(UncertainTransaction):trader.begin('must not retry after crash')
