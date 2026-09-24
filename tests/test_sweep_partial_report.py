from collections import Counter
from types import SimpleNamespace

from dipbot.app import Window
from dipbot.chain import address
from test_expanded_scenarios import multi_worker
from test_recovery_audit import POOL


def test_stop_before_sweep_reports_unchecked_tokens_without_rpc(tmp_path):
    worker, other, balances, sent, reports = multi_worker(tmp_path)
    def forbidden(*args):
        raise AssertionError('STOP must not trigger new balance requests')
    worker.chain.balance = forbidden
    worker.stop_event.set()
    worker.sweep()
    assert not sent
    assert len(reports) == 1
    assert reports[0]['status'] == 'stopped'
    assert reports[0]['remaining'] == {}
    assert {POOL.token, other.token} <= set(reports[0]['unknown'])
    assert worker.strategy.entry is not None


def test_stop_during_final_check_keeps_verified_zero_separate_from_unknown(tmp_path):
    worker, other, balances, sent, reports = multi_worker(tmp_path)
    reads, verified = Counter(), []
    def balance(token, owner):
        assert not worker.stop_event.is_set(), 'No RPC after STOP'
        reads[token] += 1
        # Initial TARGET/BASE passes read each asset once. The second read is final verification.
        if reads[token] == 2:
            verified.append(token)
            worker.stop_event.set()
        return 0
    worker.chain.balance = balance
    worker.sweep()
    assert not sent and len(verified) == 1
    report = reports[0]
    assert report['status'] == 'stopped'
    assert verified[0] not in report['unknown']
    assert report['remaining'] == {}
    assert report['unknown']


def test_partial_report_is_visible_in_window_log():
    messages = []
    window = SimpleNamespace(log=messages.append, update_controls=lambda: None)
    Window.on_event(window, 'sweep_report', {
        'status': 'interrupted', 'sold': [POOL.token], 'failed': [address(POOL.quote)],
        'skipped': [], 'remaining': {}, 'unknown': [POOL.token],
        'needs_reconciliation': True, 'error': 'Статус транзакции неизвестен',
    })
    assert any('частичный результат' in line for line in messages)
    assert any('обработаны активы' in line and POOL.token in line for line in messages)
    assert any('ошибки по активам' in line for line in messages)
    assert any('нужна сверка' in line for line in messages)
    assert any('балансы не проверены' in line for line in messages)


def test_catalog_target_sold_by_converter_clears_position(tmp_path):
    from dataclasses import replace
    from dipbot.chain import USDT
    from dipbot.discovery import Resolution
    from dipbot import wallet_registry
    from dipbot.storage import Store
    from dipbot.strategy import D
    from test_recovery_audit import setup_worker
    worker = setup_worker(tmp_path)
    worker.set_position(0, 0)
    worker.pool = replace(POOL, token=address(USDT))
    worker.set_position(200, D('1.2'))
    wallet_registry.register(worker.store, worker.live.owner, worker.pool, 'WBNB')
    balances = {address(USDT): 200}
    worker.chain = SimpleNamespace(balance=lambda token, owner: balances.get(token, 0),
        resolve_address=lambda *args: Resolution('CATALOG_TOKEN'))
    worker.live.begin = lambda *_: None
    worker.live.conversion_route = lambda *args: []
    worker.live.convert = lambda token, *args: balances.update({token: 0})
    worker.live.finish = worker.store.save
    reports = []
    worker.event.connect(lambda name, value: reports.append(value) if name == 'sweep_report' else None)
    worker.sweep()
    assert not worker.position() and worker.strategy.entry is None
    assert not Store(worker.store.path).data['positions']
    assert address(USDT) in reports[0]['sold']
    assert not reports[0]['skipped']


def test_already_zero_catalog_target_reconciles_stale_position(tmp_path):
    from dataclasses import replace
    from dipbot.chain import USDT
    from dipbot.strategy import D
    from test_recovery_audit import setup_worker
    worker = setup_worker(tmp_path)
    worker.set_position(0, 0)
    worker.pool = replace(POOL, token=address(USDT))
    worker.set_position(200, D('1.2'))
    worker.chain = SimpleNamespace(balance=lambda *args: 0)
    worker.sweep()
    assert not worker.position() and worker.strategy.entry is None
