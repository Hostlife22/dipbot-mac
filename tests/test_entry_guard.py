from decimal import Decimal as D
from types import SimpleNamespace
import pytest

from dipbot.market.chain import Chain
from dipbot.domain.entry_guard import assess, EntryRejected
from dipbot.persistence.storage import Store
from dipbot.application.worker import Worker
from test_autopair_dynamic import POOL


def test_roundtrip_boundary_uses_exact_integer_arithmetic():
    amount = 10**60
    assert assess(amount, 1, amount * 97 // 100, 1, D(3)).roundtrip_loss_pct == 3
    with pytest.raises(EntryRejected):
        assess(amount, 1, amount * 97 // 100 - 1, 1, D(3))


@pytest.mark.parametrize('target,reverse', [(0, 1), (1, 0), (-1, 1), (True, 1)])
def test_unusable_quotes_block_entry(target, reverse):
    with pytest.raises(EntryRejected):
        assess(100, target, reverse, 1, D(3))


def test_quotes_share_block_and_reorg_never_accepted():
    c = object.__new__(Chain)
    c.check = lambda **kwargs: 123
    c.checked_header = {'hash': b'a' * 32}
    calls = []
    def quote(pool, amount, buy, *, block):
        calls.append((amount, buy, block))
        return 50 if buy else 99
    c.quote = quote
    c.canonical_receipt = lambda r: calls.append(r)
    assert c.entry_quote(POOL, 100, D(3)).reverse_out == 99
    assert calls[:2] == [(100, True, 123), (50, False, 123)]
    def reorg(_):
        raise ValueError('reorg')
    c.canonical_receipt = reorg
    with pytest.raises(ValueError, match='reorg'):
        c.entry_quote(POOL, 100, D(3))


@pytest.mark.parametrize('mode', ['PAPER', 'LIVE'])
def test_failed_preflight_never_creates_intent_or_buys(tmp_path, mode):
    w = Worker(Store(tmp_path / 'state.json'))
    w.mode = mode
    w.pool = POOL
    w.current_price = D(1)
    def reject(*args):
        raise EntryRejected('illiquid')
    w.chain = SimpleNamespace(entry_quote=reject)
    calls = []
    w.live = SimpleNamespace(owner='0x123', begin=lambda *_: calls.append('begin'))
    with pytest.raises(EntryRejected):
        w.open_position()
    assert calls == [] and not w.store.data.get('operation') and not w.paper.position


def test_live_preflight_rejection_cools_down_without_unlocking_pending(tmp_path):
    w = Worker(Store(tmp_path / 'state.json'))
    w.mode = 'LIVE'; w.pool = POOL; w.running = True
    w.strategy.base = D(100)
    w.read_price = lambda: D(90)
    w.chain = SimpleNamespace(entry_quote=lambda *args: assess(100, 1, 1, 1, D(3)))
    w.live = SimpleNamespace(owner='0x123')
    w.observe()
    assert w.running and w.entry_notice and not w.store.data.get('operation')
