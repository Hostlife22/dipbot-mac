from types import SimpleNamespace
import pytest
from dipbot.market.chain import Chain
from dipbot.research.market_tape import MarketTape
from dipbot.application.worker import Worker
from dipbot.persistence.storage import Store
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


def test_fresh_price_snapshot_saves_head_read_but_keeps_canonical_check():
    import time
    chain = fake_chain()
    chain.checked_header['timestamp'] = time.time()
    chain._paper_price_snapshot = (POOL, dict(chain.checked_header), time.monotonic())
    calls = []
    chain.check = lambda **kw: calls.append('head') or 42
    chain.canonical_receipt = lambda receipt: calls.append(('canonical', receipt['blockHash']))
    assert chain.paper_quote(POOL, 100, True) == 123
    assert calls == [('canonical', b'a'*32)]
    chain.paper_quote(POOL, 100, True)
    assert calls[-2:] == ['head', ('canonical', b'a'*32)]


@pytest.mark.parametrize('invalid', ['age', 'pool', 'block_age', 'disabled', 'future'])
def test_paper_snapshot_falls_back_when_not_reusable(invalid):
    import time
    from dataclasses import replace
    chain = fake_chain()
    header = dict(chain.checked_header, timestamp=time.time())
    pool, when = POOL, time.monotonic()
    if invalid == 'age': when -= 1
    if invalid == 'future': when += 1
    if invalid == 'pool': pool = replace(POOL, token_is_0=not POOL.token_is_0)
    if invalid == 'block_age': header['timestamp'] -= 60
    if invalid == 'disabled': chain.paper_price_reuse_enabled = False
    chain._paper_price_snapshot = (pool, header, when)
    heads = []
    chain.check = lambda **kw: heads.append(42) or 42
    chain.paper_quote(POOL, 100, True)
    assert heads == [42]


def test_reused_snapshot_cannot_bypass_reorg_rejection():
    import time
    chain = fake_chain(True)
    chain._paper_price_snapshot = (POOL, dict(chain.checked_header, timestamp=time.time()), time.monotonic())
    with pytest.raises(ValueError, match='Reorg'):
        chain.paper_quote(POOL, 100, True)
    assert chain._paper_price_snapshot is None
