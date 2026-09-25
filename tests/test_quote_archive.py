from types import SimpleNamespace
import pytest
from dipbot.chain import Chain
from dipbot.market_tape import MarketTape
from dipbot.worker import Worker
from dipbot.storage import Store
from test_autopair_dynamic import POOL
from test_market_tape import read


def fake_chain(reorg=False):
    chain=object.__new__(Chain)
    chain.checked_header={'number':42,'hash':b'a'*32}
    chain.check=lambda **kw:42
    chain.quote=lambda *args,**kw:123
    def canonical(receipt):
        assert receipt['blockNumber']==42 and receipt['blockHash']==b'a'*32
        if reorg:raise ValueError('Reorg')
    chain.canonical_receipt=canonical
    return chain


def test_paper_fill_rejects_changed_block_before_virtual_execution():
    with pytest.raises(ValueError,match='Reorg'):fake_chain(True).paper_quote(POOL,100,True)


def test_paper_quote_records_canonical_context():
    chain=fake_chain()
    assert chain.paper_quote(POOL,100,True)==123
    assert chain.quote_context=={'block':42,'block_hash':(b'a'*32).hex()}


def test_archive_retains_raw_quote_without_credentials(tmp_path):
    worker=Worker(Store(tmp_path/'state.json'));worker.pool=POOL
    worker.recorder=MarketTape(tmp_path/'tapes',{'mode':'PAPER'})
    source=SimpleNamespace(quote_context={'block':42,'block_hash':'ab','rpc':'SECRET'})
    worker.record_quote(source,'paper_fill','BUY',10**18,123)
    worker.recorder.close()
    rows=read(worker.recorder.path)
    assert rows[1]['amount_in_raw']==10**18 and rows[1]['amount_out_raw']==123
    assert rows[1]['block']==42 and rows[1]['pool']==POOL.address
    assert 'SECRET' not in worker.recorder.path.read_text()


def test_zero_paper_quote_reaches_existing_minout_policy():
    chain = fake_chain()
    chain.quote = lambda *args, **kwargs: 0
    assert chain.paper_quote(POOL, 100, True) == 0
