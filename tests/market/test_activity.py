from tests.support.activity import setup
from dataclasses import replace
from types import SimpleNamespace as NS
from decimal import Decimal as D
import pytest
from web3 import Web3
from dipbot.market.activity import swap_count, SIGNATURES
from dipbot.domain.strategy import Settings
from tests.support.markets import POOL




@pytest.mark.parametrize('router', ['V2','V3'])
def test_counts_unique_canonical_events(router):
    c,p,logs,calls = setup(router)
    assert swap_count(c,p) == {'count':1,'from_block':24,'to_block':123}
    assert calls == [{'blockNumber':123,'blockHash':b'h'*32}]


@pytest.mark.parametrize('field,value', [('removed',True),('blockNumber',124),('data',b'')])
def test_partial_or_removed_events_do_not_pass(field,value):
    c,p,logs,calls = setup()
    logs[0][field] = value
    with pytest.raises(ValueError): swap_count(c,p)


def test_invalid_minimum_rejected():
    for value in ('NaN','-1','0.5','10001'):
        with pytest.raises(ValueError): Settings(min_swaps=D(value))


def test_empty_market_is_zero_not_rpc_failure():
    c,p,logs,calls = setup()
    logs.clear()
    assert swap_count(c,p)['count'] == 0


def test_legacy_preferences_default_activity_filter_off():
    from dipbot.persistence import preferences
    old = preferences.from_windows_ui({})
    old['settings'].pop('min_swaps')
    assert preferences.normalize(old)['settings']['min_swaps'] == '0'


def test_insufficient_activity_rejects_before_paper_buy(tmp_path, monkeypatch):
    from dipbot.application.worker import Worker
    from dipbot.persistence.storage import Store
    from dipbot.domain.entry_guard import EntryRejected
    w = Worker(Store(tmp_path/'state.json'))
    w.mode, w.pool = 'PAPER', POOL
    w.current_price = D(1)
    w.strategy.settings = Settings(min_swaps=D(5))
    monkeypatch.setattr('dipbot.market.activity.swap_count', lambda *a:dict(count=4,from_block=1,to_block=100))
    with pytest.raises(EntryRejected, match='Недостаточная активность'):
        w.open_position()
    assert not w.paper.position and not w.store.data.get('operation')


def test_activity_failure_checks_backup_before_buy(tmp_path, monkeypatch):
    from dipbot.application.worker import Worker
    from dipbot.persistence.storage import Store
    from dipbot.domain.entry_guard import EntryRejected
    from web3.exceptions import Web3RPCError
    w = Worker(Store(tmp_path/'state.json'))
    w.mode, w.pool, w.current_price = 'PAPER', POOL, D(1)
    w.strategy.settings = Settings(min_swaps=D(5))
    w.chain = object()
    w.backup_chain = NS(verify_pool=lambda *a:POOL)
    seen = []
    def count(chain, pool):
        seen.append(chain)
        if chain is w.chain: raise Web3RPCError('limit exceeded')
        return dict(count=4,from_block=1,to_block=100)
    monkeypatch.setattr('dipbot.market.activity.swap_count', count)
    with pytest.raises(EntryRejected, match='Недостаточная активность'):
        w.open_position()
    assert seen == [w.chain,w.backup_chain]
    assert not w.paper.position
