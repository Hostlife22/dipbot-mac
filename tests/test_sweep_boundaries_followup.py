from dataclasses import replace
import pytest
from dipbot import wallet_registry
from dipbot.autopair import Candidate
from dipbot.discovery import Resolution
from dipbot.storage import Store
from test_expanded_scenarios import multi_worker
from test_recovery_audit import POOL


def test_zero_target_clears_saved_position_without_inventing_pnl(tmp_path):
    w, other, balances, sent, reports = multi_worker(tmp_path)
    balances[POOL.token] = 0
    w.sweep()
    assert not Store(w.store.path).data['positions']
    assert not w.store.data.get('closed_trades')
    assert POOL.token not in reports[0]['sold']


def test_zero_target_save_failure_restores_memory_and_stops(tmp_path):
    w, other, balances, sent, reports = multi_worker(tmp_path)
    balances[POOL.token] = 0
    def fail(): raise OSError('synthetic disk failure')
    w.store.save = fail
    with pytest.raises(OSError): w.sweep()
    assert len(w.store.data['positions']) == len(Store(w.store.path).data['positions']) == 2
    assert not sent and reports[0]['status'] == 'interrupted'


def test_registered_target_changes_route_and_keeps_failed_other(tmp_path):
    w, other, balances, sent, reports = multi_worker(tmp_path)
    wallet_registry.register(w.store, w.live.owner, POOL, 'WBNB')
    wallet_registry.register(w.store, w.live.owner, other, 'WBNB')
    from dipbot.chain import address
    alternate = replace(POOL, address=address('0x'+'cd'*20))
    verified = []
    def verify(addr, token):
        assert addr == alternate.address and token == POOL.token
        verified.append(addr)
        return alternate
    w.chain.verify_pool = verify
    candidate = Candidate(alternate, 'WBNB', True, 100)
    def resolve(token, *_):
        if token == other.token: raise TimeoutError('unavailable second target')
        return Resolution('RESOLVED', (candidate,), candidate)
    w.chain.resolve_address = resolve
    actual = []
    def swap(pool, *args, **kwargs):
        actual.append(pool)
        balances[pool.token] = 0
    w.live.swap = swap
    w.sweep()
    assert actual == [alternate] and verified == [alternate.address]
    assert reports[0]['sold'] == [POOL.token]
    assert reports[0]['failed'] == [other.token]
    assert reports[0]['remaining'][other.token] == 300
    assert len(Store(w.store.path).data['positions']) == 1


def test_stop_during_preflight_quote_does_not_begin(tmp_path):
    w, other, balances, sent, reports = multi_worker(tmp_path)
    def quote(*_):
        w.stop_event.set()
        return 190
    w.chain.quote = quote
    w.sweep()
    assert not sent and reports[0]['status'] == 'stopped'
    assert len(Store(w.store.path).data['positions']) == 2


def test_pending_operation_blocks_sweep_even_zero_cleanup(tmp_path):
    from dipbot.trader import UncertainTransaction
    w, other, balances, sent, reports = multi_worker(tmp_path)
    w.store.data['operation'] = {'transactions':[{'status':'pending'}]}
    w.store.save()
    w.chain.balance = lambda *_: pytest.fail('No reads before reconciliation')
    with pytest.raises(UncertainTransaction): w.sweep()
    assert len(w.store.data['positions']) == 2
    assert reports[0]['needs_reconciliation'] and not sent


def test_zero_target_failure_after_replace_keeps_visible_file_state(tmp_path):
    from dipbot.storage import SaveAfterReplaceError
    w, other, balances, sent, reports = multi_worker(tmp_path)
    balances[POOL.token] = 0
    save = w.store.save
    def fail_after():
        save()
        raise SaveAfterReplaceError('synthetic directory sync failure')
    w.store.save = fail_after
    with pytest.raises(SaveAfterReplaceError): w.sweep()
    assert w.store.data['positions'] == Store(w.store.path).data['positions']
    assert len(w.store.data['positions']) == 1
    assert not sent
