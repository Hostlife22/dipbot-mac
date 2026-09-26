"""Shared discovery fixtures/builders."""
from dataclasses import asdict, replace
from types import SimpleNamespace
import json
import pytest
from web3 import Web3
from dipbot.market import discovery
from dipbot.persistence import preferences
from dipbot.persistence import dynamic
from dipbot.persistence import wallet_registry
from dipbot.market.chain import Chain, Pool, WBNB, USDT, ETH, V2_FACTORY, POOL_ABI, address
from dipbot.market.autopair import Candidate
from dipbot.persistence.storage import Store
from dipbot.application.worker import Worker
from dipbot.execution.trader import LiveTrader
from dipbot.execution.errors import UncertainTransaction


OWNER=address('0x'+'34'*20)


TOKEN=address('0x'+'ab'*20)


BASE=address('0x'+'cd'*20)


POOL=Pool(address('0x'+'12'*20),'V2',TOKEN,address(WBNB),18,18,True)
