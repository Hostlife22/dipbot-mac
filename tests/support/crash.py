"""Shared crash fixtures/builders."""

SCRIPT = r"""
import os,sys
from types import SimpleNamespace
from eth_account import Account
from web3 import Web3
from dipbot.persistence.storage import Store
from dipbot.execution.trader import LiveTrader
from dipbot.domain.strategy import D
from dipbot.domain.assets import WBNB
from dipbot.market.chain import address
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
  return {'status':1,'blockNumber':123,'transactionHash':args[0]}
t=LiveTrader(SimpleNamespace(check=lambda:123,w3=SimpleNamespace(eth=Eth())),
             Account.create().key,store,D('0.1'),lambda _:None)
t.begin('synthetic process crash')
if boundary=='intent':os._exit(70)
t.send(Function(),'synthetic')
os._exit(73)
"""
