"""Actual Qt controls with offline worker/network doubles."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from types import SimpleNamespace
import pytest
from PySide6.QtWidgets import QApplication
from dipbot.app import Window, QMessageBox
from dipbot.worker import Worker
from dipbot.storage import Store, SaveAfterReplaceError
from dipbot import dynamic
from test_autopair_dynamic import POOL, TARGET, BASE, route


@pytest.fixture
def window(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(Worker, 'start', lambda self: None)
    monkeypatch.setattr(QMessageBox, 'warning', lambda *args: None)
    w = Window(Store(tmp_path/'state.json'))
    yield w
    w.autopair_timer.stop()
    w.deleteLater()
    app.processEvents()


def test_input_change_discards_selection_and_late_result(window):
    w = window
    w.mode.setCurrentText('PAPER')
    assert not w.start.isEnabled()
    w.on_event('selected', POOL)
    assert w.start.isEnabled()
    w.send('discover', token=TARGET, quote='ALL', router='AUTO')
    old = w.auto_generation
    assert w.token.isEnabled()
    w.token.setText(BASE)
    w.token.textEdited.emit(BASE)
    w.on_event('discovery_event', (old, 'selected', POOL))
    w.on_event('busy', False)
    assert w.token.text() == BASE
    assert not w.pool_input.text() and not w.start.isEnabled()
    assert not w.candidates.count()


def test_pending_error_retry_and_success(window):
    w = window
    w.mode.setCurrentText('PAPER')
    w.send('discover', token=TARGET, quote='ALL', router='AUTO')
    g = w.auto_generation
    w.on_event('discovery_event', (g, 'autopair', 'PENDING'))
    w.on_event('busy', False)
    assert 'PENDING' in w.pool_label.text() and not w.buy.isEnabled()
    w.send('discover', token=TARGET, quote='ALL', router='AUTO')
    g = w.auto_generation
    w.on_event('discovery_event', (g, 'error', 'TimeoutError'))
    w.on_event('busy', False)
    assert 'повторите' in w.pool_label.text()
    w.send('discover', token=TARGET, quote='ALL', router='AUTO')
    w.on_event('discovery_event', (w.auto_generation, 'selected', POOL))
    w.on_event('busy', False)
    assert w.start.isEnabled() and w.pool_input.text() == POOL.address


def test_add_remove_refresh_and_restart_settings(window):
    w = window
    w.quote.setCurrentText('ALL')
    w.update_profiles()
    assert w.quote.currentText() == 'ALL'
    row = dynamic.upsert(w.store, POOL, route(), 100, symbol='CUSTOM')
    w.on_event('profiles', w.store.data['dynamic_profiles'])
    w.on_event('selected', POOL)
    w.params['amount'].setText('1.25')
    w.quote.setCurrentText('WBNB')
    w.params['amount'].setText('0.37')
    w.quote.setCurrentText(row['name'])
    assert w.params['amount'].text() == '1.25'
    event = SimpleNamespace(accept=lambda: None, ignore=lambda: pytest.fail('close rejected'))
    w.closeEvent(event)
    reopened = Window(Store(w.store.path))
    try:
        assert reopened.router.currentText() == 'V3'
        assert reopened.quote.currentText() == row['name']
        assert reopened.params['amount'].text() == '1.25'
        reopened.mode.setCurrentText('PAPER')
        assert not reopened.start.isEnabled()  # requires fresh RPC verification
        reopened.worker.chain = SimpleNamespace(balance=lambda *args: 0)
        reopened.worker.command('remove_profile', {'symbol': row['name'], 'wallet': TARGET})
        assert reopened.quote.currentText() == 'WBNB'
        assert not reopened.pool_input.text()
        assert not reopened.selection_ready
        assert row['name'] not in Store(w.store.path).data['dynamic_profiles']
    finally:
        reopened.autopair_timer.stop()
        reopened.deleteLater()


@pytest.mark.parametrize('command', ['verify', 'select'])
def test_manual_verification_discards_late_result(tmp_path, command):
    worker = Worker(Store(tmp_path/'state.json'))
    worker.discovery_generation = 1
    def verify(*args):
        worker.discovery_generation = 2
        return POOL
    worker.chain = SimpleNamespace(verify_pool=verify)
    events = []
    worker.event.connect(lambda *args: events.append(args))
    worker.command(command, {'generation': 1, 'token': TARGET,
                            'pool': POOL if command == 'select' else POOL.address})
    assert worker.pool is None and not events


def test_remove_after_replace_error_refreshes_visible_state(window):
    w = window
    row = dynamic.upsert(w.store, POOL, route(), 100)
    w.update_profiles()
    w.on_event('selected', POOL)
    original = w.store.save
    def fail_after_replace():
        original()
        raise SaveAfterReplaceError('synthetic')
    w.store.save = fail_after_replace
    w.worker.chain = SimpleNamespace(balance=lambda *args: 0)
    with pytest.raises(SaveAfterReplaceError):
        w.worker.command('remove_profile', {'symbol': row['name'], 'wallet': TARGET})
    assert not w.pool_input.text() and not w.selection_ready
    assert w.quote.currentText() == 'WBNB'
    assert row['name'] not in Store(w.store.path).data['dynamic_profiles']


@pytest.mark.parametrize('raw', [TARGET, POOL.address])
def test_address_resolution_to_trading_selection(window, raw):
    from dipbot.discovery import Resolution
    from dipbot.autopair import Candidate
    w = window
    w.mode.setCurrentText('PAPER')
    seen = []
    candidate = Candidate(POOL, 'CUSTOM', True, 100)
    def resolve(value, catalogs):
        seen.append(value)
        return Resolution('RESOLVED', (candidate,), candidate, value)
    w.worker.chain = SimpleNamespace(resolve_address=resolve,
        verify_pool=lambda *args: POOL, price=lambda pool: 1)
    w.send('discover', token=raw, quote='ALL', router='AUTO')
    name, data = w.worker.commands.get_nowait()
    w.worker.command(name, data)
    w.on_event('busy', False)
    assert seen == [raw]
    assert w.token.text() == TARGET and w.pool_input.text() == POOL.address
    assert w.worker.pool_generation == w.auto_generation
    assert w.start.isEnabled()
    assert Store(w.store.path).data['last_pool']['address'] == POOL.address


def test_add_rejects_selection_invalidated_by_input(window):
    w = window
    w.worker.chain = SimpleNamespace()
    w.worker.pool = POOL
    w.worker.pool_generation = w.auto_generation
    w.on_event('selected', POOL)
    w.invalidate_discovery()
    with pytest.raises(ValueError, match='Ввод изменился'):
        w.worker.command('add_profile', {'generation': w.auto_generation})
    assert not w.store.data.get('dynamic_profiles')


def test_stop_preserves_verified_market_for_restart(window):
    w = window
    w.mode.setCurrentText('PAPER')
    w.on_event('selected', POOL)
    generation = w.auto_generation
    w.running = True
    w.stop_bot()
    w.running = False
    w.worker.stop_event.clear()
    w.on_event('status', {'mode':'PAPER','running':False,'locked':False,
        'position':'0','base':'0','realized':'0','levels':{}})
    assert w.start.isEnabled() and w.selection_ready
    assert w.pool_input.text() == POOL.address
    assert w.auto_generation == generation


def test_stop_invalidates_inflight_search(window):
    w = window
    w.send('discover', token=TARGET, quote='ALL', router='AUTO')
    generation = w.auto_generation
    w.stop_bot()
    w.on_event('discovery_event', (generation, 'selected', POOL))
    assert not w.selection_ready and not w.pool_input.text()


def test_recovery_statuses_are_distinct(window):
    w = window
    payload = {'running': True, 'mode': 'DEMO', 'locked': False,
               'position': '0', 'base': '1', 'entry': '0', 'realized': '0', 'levels': {}}
    w.on_event('status', payload | {'entry_notice': 'minOut; пауза 5 с'})
    assert w.metrics['state'].text() == 'WAIT DIP'
    assert 'Вход пропущен' in w.strategy_status.text()
    w.on_event('status', payload | {'quote_unavailable': True, 'position': '1'})
    assert w.metrics['state'].text() == 'WAIT RPC'
    assert 'TP/SL временно недоступны' in w.strategy_status.text()
    w.on_event('status', payload | {'running': False, 'halt_reason': 'Сбой исполнения'})
    assert w.metrics['state'].text() == 'ERROR'
    assert 'Сбой исполнения' in w.strategy_status.text() and 'START' in w.strategy_status.text()


def test_failed_stop_guides_user_to_close_saved_position(window):
    w = window
    w.on_event('status', {'running': False, 'mode': 'DEMO', 'locked': False,
        'position': '1', 'base': '1', 'entry': '1', 'realized': '0',
        'halt_reason': 'TimeoutError', 'levels': {'ENTRY':'1'}})
    assert 'позиция сохранена' in w.strategy_status.text()
    assert 'SELL POSITION или STOP' in w.strategy_status.text()
    assert w.sell.isEnabled()


def test_route_compare_uses_amount_and_discards_late_ui_result(window):
    w=window
    w.on_event('pools',[POOL])
    submitted=[]
    w.worker.submit=lambda name,**data:submitted.append((name,data))
    w.params['amount'].setText('0.004')
    w.compare_routes()
    name,data=submitted[-1]
    assert name=='compare_routes' and data['amount']=='0.004'
    assert data['reference']==POOL and data['generation']==w.auto_generation
    generation=w.auto_generation
    w.invalidate_discovery()
    w.on_event('discovery_event',(generation,'route_comparison_error','OLD RESULT'))
    assert 'OLD RESULT' not in w.route_comparison.text()


def test_pending_cancel_confirmation_contains_fee_and_requires_yes(window,monkeypatch):
    from test_cancellation import original
    w=window
    w.mode.setCurrentText('LIVE')
    w.store.data['operation']={'wallet':'0x'+'34'*20,'transactions':[original()]}
    sent=[];prompts=[]
    w.send=lambda name,**data:sent.append((name,data))
    def no(*args):prompts.append(args[2]);return QMessageBox.No
    monkeypatch.setattr(QMessageBox,'question',no)
    w.cancel_pending()
    assert not sent and '0.125 gwei' in prompts[0] and 'Nonce 0' in prompts[0]
    monkeypatch.setattr(QMessageBox,'question',lambda *args:QMessageBox.Yes)
    w.cancel_pending()
    assert sent[0][0]=='cancel_pending' and sent[0][1]['expected_gas_price']==125000000


def test_uncatalogued_base_has_address_and_separate_amount(window):
    from dataclasses import replace
    w = window
    a = replace(POOL, quote='0x'+'a'*40)
    b = replace(POOL, quote='0x'+'b'*40)
    w.on_event('selected', a)
    assert '/ ALL ' not in w.market_summary.text()
    assert a.quote in w.market_summary.toolTip()
    w.params['amount'].setText('0.123')
    w.on_event('selected', b)
    w.params['amount'].setText('0.456')
    w.on_event('selected', a)
    assert w.params['amount'].text() == '0.123'
    assert w.pair_amounts[b.router+':'+b.quote] == '0.456'
