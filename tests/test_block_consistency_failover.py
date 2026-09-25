from dataclasses import replace
from types import SimpleNamespace as NS
from hexbytes import HexBytes
import pytest
from dipbot.chain import Chain
from dipbot.storage import Store
from dipbot.worker import Worker
from dipbot.trader import LiveTrader, UncertainTransaction
from dipbot.strategy import D
from test_autopair_dynamic import POOL
from test_execution import trader, Function

HASH = HexBytes('0x'+'12'*32)
OTHER = HexBytes('0x'+'34'*32)


def chain_fixture():
    chain = object.__new__(Chain)
    chain.w3 = NS(eth=NS(get_block=lambda n:{'number':n,'hash':HASH}))
    return chain


def test_receipt_balance_reads_exact_block_and_checks_snapshot():
    chain=chain_fixture();calls=[]
    chain.balance_at=lambda token,owner,n:calls.append(n) or 120
    assert chain.receipt_balance('token','owner',{'blockNumber':12,'blockHash':HASH},
        {'blockNumber':10,'blockHash':HASH})==120
    assert calls==[12]


@pytest.mark.parametrize('where', ['snapshot', 'receipt', 'after_balance'])
def test_reorg_rejected_before_accounting(where):
    chain=chain_fixture();reads=[]
    def block(n):
        reads.append(n)
        bad = (where=='snapshot' and n==10) or (where=='receipt' and n==12) or (
            where=='after_balance' and len(reads)==3)
        return {'number':n,'hash':OTHER if bad else HASH}
    chain.w3.eth.get_block=block
    chain.balance_at=lambda *a:120
    with pytest.raises(ValueError, match='канонической'):
        chain.receipt_balance('token','owner',{'blockNumber':12,'blockHash':HASH},
            {'blockNumber':10,'blockHash':HASH})


def test_post_send_canonical_failure_keeps_pending_no_resend(trader, monkeypatch):
    checks=[]
    def check(receipt):
        checks.append(receipt)
        raise ValueError('reorg')
    trader.chain.canonical_receipt=check
    monkeypatch.setattr('dipbot.trader.time.sleep',lambda _:None)
    trader.begin('BUY')
    with pytest.raises(UncertainTransaction):trader.send(Function(),'BUY')
    assert len(checks)==3
    record=Store(trader.store.path).data['operation']['transactions']
    assert len(record)==1 and record[0]['status']=='pending'


def test_read_retry_recovers_without_executing_any_transaction(monkeypatch):
    attempts=[]
    def read():
        attempts.append(1)
        if len(attempts)<3:raise TimeoutError()
        return 123
    monkeypatch.setattr('dipbot.trader.time.sleep',lambda _:None)
    assert LiveTrader.retry_read(read)==123 and len(attempts)==3


def test_backup_keeps_primary_executor_and_returns_to_primary(tmp_path):
    worker=Worker(Store(tmp_path/'state.json'));worker.pool=POOL;worker.mode='LIVE'
    attempts=[]
    def primary_price(p):
        attempts.append(1)
        if len(attempts)==1:raise TimeoutError()
        return D(2)
    primary=NS(price=primary_price)
    worker.chain=primary;worker.live=NS(chain=primary)
    worker.backup_chain=NS(check=lambda:12,verify_pool=lambda *a:POOL,price=lambda p:D(1))
    assert worker.market_price()==1
    assert worker.market_price()==1 and len(attempts)==1
    assert worker.live.chain is primary and worker.chain is primary
    worker.backup_until=0
    assert worker.market_price()==2 and worker.market_source=='BSC'


@pytest.mark.parametrize('failure', ['pool','behind','fork','wrong_network'])
def test_untrusted_backup_is_not_used(tmp_path, failure):
    worker=Worker(Store(tmp_path/'state.json'));worker.pool=POOL
    def down(p):raise TimeoutError()
    worker.chain=NS(price=down,price_block={'number':12,'hash':HASH})
    def check():
        if failure=='wrong_network':raise ValueError('chainId')
    worker.backup_chain=NS(check=check, verify_pool=lambda *a:replace(POOL,fee=3000) if failure=='pool' else POOL,
        price=lambda p:D(1),price_block={'number':11 if failure=='behind' else 12,
                                       'hash':OTHER if failure=='fork' else HASH})
    with pytest.raises(ValueError):worker.market_price()


def test_backup_transport_forbids_broadcast():
    chain=chain_fixture();calls=[]
    chain.w3.provider=NS(make_request=lambda method,params:calls.append(method) or {})
    chain.restrict_to_reads()
    with pytest.raises(RuntimeError):chain.w3.provider.make_request('eth_sendRawTransaction',['secret'])
    assert calls==[]
    chain.w3.provider.make_request('eth_call',[])
    assert calls==['eth_call']
