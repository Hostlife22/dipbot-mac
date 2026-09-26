from types import SimpleNamespace as NS
from dataclasses import replace
import pytest
from dipbot.market.chain import Chain, StaleBlock
from tests.support.markets import POOL
POOL = replace(POOL, router="V2", fee=0)


def setup():
    c=object.__new__(Chain)
    c.checked_header={'number':100,'hash':b'a'*32}
    c.check=lambda **kwargs:c.checked_header['number']
    c.canonical_receipt=lambda receipt:None
    reads=[]
    c.call=lambda *args,**kw:reads.append(kw['block']) or (100,200,0)
    return c,reads


def test_same_block_hash_reuses_price_but_checks_head_each_time():
    c,reads=setup()
    heads=[]
    c.check=lambda **kw:heads.append(100) or 100
    assert c.price(POOL)==c.price(POOL)
    assert reads==[100] and heads==[100,100] and c.price_cache_hit


def test_reorg_and_pool_orientation_invalidate_cache():
    c,reads=setup()
    c.price(POOL)
    c.checked_header['hash']=b'b'*32
    c.price(POOL)
    c.price(replace(POOL,token_is_0=not POOL.token_is_0))
    assert reads==[100,100,100]


def test_old_head_never_serves_cache():
    c,reads=setup()
    c.price(POOL)
    def stale(**kw):raise StaleBlock('old')
    c.check=stale
    with pytest.raises(StaleBlock):c.price(POOL)
    assert reads==[100]
    assert c._paper_price_snapshot is None


def test_reorg_during_pool_read_does_not_cache():
    c,reads=setup()
    def reorg(receipt):raise ValueError('reorg')
    c.canonical_receipt=reorg
    with pytest.raises(ValueError):c.price(POOL)
    assert not hasattr(c,'_price_cache')


def test_v3_multicall_is_reused_only_for_identical_header(monkeypatch):
    c,reads=setup()
    def batch(chain,requests,block):
        reads.append(block)
        return [100, (2**96,)]
    monkeypatch.setattr('dipbot.market.discovery.batch',batch)
    pool=replace(POOL,router='V3',fee=500)
    c.price(pool)
    c.price(pool)
    assert reads==[100]
    c.checked_header={'number':101,'hash':b'c'*32}
    c.price(pool)
    assert reads==[100,101]
