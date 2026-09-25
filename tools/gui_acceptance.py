"""Visible Qt acceptance run: public read-only BSC plus controlled fault scenarios.

Uses temporary state, no Keychain, no trades. Run as a module on macOS.
"""
import argparse
import json
import tempfile
import time
import threading
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from dataclasses import replace
from PySide6.QtWidgets import QApplication, QPushButton, QMessageBox, QScrollArea
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from dipbot.app import Window, STYLE
from dipbot.chain import Chain, Pool, USDT, WBNB, address
from dipbot.storage import Store
from dipbot.autopair import Candidate
from dipbot.discovery import Resolution
from dipbot.trader import LiveTrader
from tools.read_only_probe import guard_provider


def run(endpoint, output):
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    app.setStyleSheet(STYLE)
    report = {'utc': datetime.now(timezone.utc).isoformat(), 'platform': app.platformName(), 'scenarios': [], 'transactions_sent': 0}
    dialogs = []
    original_warning = QMessageBox.warning
    QMessageBox.warning = lambda *args: dialogs.append(args[2])
    def wait(predicate, timeout=60):
        deadline = time.monotonic() + timeout
        while not predicate():
            if time.monotonic() > deadline:
                raise TimeoutError('GUI scenario deadline')
            app.processEvents();time.sleep(.02)
    def button(w, label):
        return next(b for b in w.findChildren(QPushButton) if b.text() == label)
    def click(w, label):
        b = button(w, label)
        assert b.isEnabled(), label
        parent = b.parentWidget()
        while parent is not None:
            if isinstance(parent, QScrollArea):
                parent.ensureWidgetVisible(b)
            parent = parent.parentWidget()
        app.processEvents();time.sleep(.02)
        QTest.mouseClick(b, Qt.LeftButton)
    def enter(field, text):
        field.setFocus()
        field.selectAll()
        QTest.keyClicks(field, text)
    def complete(w):
        wait(lambda: not w.busy)
    with tempfile.TemporaryDirectory(prefix='dipbot-gui-check-') as directory:
        w = Window(Store(Path(directory)/'state.json'))
        try:
            w.show()
            app.processEvents();time.sleep(.2)
            w.market_toggle.setChecked(True)
            assert w.isVisible()
            chain = Chain(endpoint)
            calls = guard_provider(chain.w3.provider)
            chain.check()
            w.worker.chain = chain
            w.mode.setCurrentText('PAPER')
            w.router.setCurrentText('V2')
            enter(w.token, USDT)
            click(w, 'AutoPair · найти пулы')
            complete(w)
            assert w.selection_ready and w.start.isEnabled(), dialogs
            pool = w.worker.pool
            report['scenarios'].append('live_token_search_select_quote')
            enter(w.token, pool.address)
            click(w, 'AutoPair · найти пулы')
            complete(w)
            assert w.selection_ready and w.worker.pool == pool, dialogs
            report['scenarios'].append('live_pool_address_search')
            w.grab().save(str(output.with_suffix('.png')))
            report['rpc_methods'] = dict(calls)

            # Controlled delayed RPC, with a real worker thread and queued Qt signals.
            started, release = threading.Event(), threading.Event()
            def slow(*args):
                started.set()
                assert release.wait(10)
                candidate = Candidate(pool, 'WBNB', True, 100)
                return Resolution('RESOLVED', (candidate,), candidate, pool.token)
            w.worker.chain = SimpleNamespace(resolve_address=slow)
            click(w, 'AutoPair · найти пулы')
            wait(started.is_set)
            enter(w.token, WBNB)
            enter(w.token, USDT)
            enter(w.token, '0x123')
            release.set()
            complete(w)
            assert w.token.text() == '0x123' and not w.selection_ready
            assert not w.pool_input.text() and not w.start.isEnabled()
            report['scenarios'].append('edit_during_worker_rpc_discards_late_result')

            w.worker.chain = SimpleNamespace(resolve_address=lambda *args: Resolution('PENDING'))
            enter(w.token, USDT)
            click(w, 'AutoPair · найти пулы')
            complete(w)
            assert 'PENDING' in w.pool_label.text() and not w.start.isEnabled()
            report['scenarios'].append('synthetic_pending_retry_available')

            # Actual refused local HTTP connection exercises the network error path.
            bad = Chain('http://127.0.0.1:1')
            guard_provider(bad.w3.provider)
            w.worker.chain = bad
            enter(w.token, USDT)
            click(w, 'AutoPair · найти пулы')
            complete(w)
            assert dialogs and 'повторите' in w.pool_label.text()
            assert not w.start.isEnabled()
            w.worker.chain = chain
            click(w, 'AutoPair · найти пулы')
            complete(w)
            assert w.selection_ready, dialogs
            report['scenarios'].append('real_connection_failure_then_live_retry')

            # Synthetic custom base: execute the actual ADD/REMOVE worker commands.
            base = address('0x'+'ab'*20)
            custom = replace(pool, quote=base)
            conversion = replace(pool, token=base, quote=address(WBNB))
            w.worker.chain = SimpleNamespace(verify_pool=lambda *args: custom,
                price=lambda *args: 1, quote_route=lambda path, amount, **kw: amount,
                symbol=lambda token: 'GUI_TEST', balance=lambda *args: 0)
            original_route = LiveTrader.conversion_route
            LiveTrader.conversion_route = lambda *args: [conversion]
            try:
                enter(w.pool_input, custom.address)
                click(w, 'CHECK POOL')
                complete(w)
                click(w, 'ADD BASE')
                complete(w)
            finally:
                LiveTrader.conversion_route = original_route
            assert w.quote.currentText() == 'GUI_TEST', dialogs
            enter(w.params['amount'], '1.25')
            w.close()
            assert not w.isVisible()
            child = subprocess.run([sys.executable, '-c', """
import sys
from PySide6.QtWidgets import QApplication
from dipbot.app import Window
from dipbot.storage import Store
app=QApplication([])
w=Window(Store(sys.argv[1]))
assert w.quote.currentText() == 'GUI_TEST'
assert w.router.currentText() == 'V2'
assert w.params['amount'].text() == '1.25'
assert not w.selection_ready
w.close()
""", str(w.store.path)], capture_output=True, text=True, timeout=20)
            assert child.returncode == 0, child.stderr
            report['scenarios'].append('settings_loaded_in_fresh_process')
            w = Window(Store(Path(directory)/'state.json'))
            w.show()
            assert w.quote.currentText() == 'GUI_TEST'
            assert w.params['amount'].text() == '1.25'
            assert not w.selection_ready
            report['scenarios'].append('synthetic_add_and_restart_settings')
            w.worker.chain = SimpleNamespace(balance=lambda *args: 0)
            w.tabs.setCurrentIndex(2)
            w.wallet.setText(address('0x'+'34'*20))
            row = next(i for i in range(w.table.rowCount()) if w.table.item(i,0).text() == 'GUI_TEST')
            w.table.selectRow(row)
            click(w, 'REMOVE выбранную пользовательскую базу')
            complete(w)
            w.autopair_timer.stop()
            assert w.quote.currentText() == 'WBNB' and not w.pool_input.text()
            assert not w.store.data.get('dynamic_profiles')
            w.close()
            w = Window(Store(Path(directory)/'state.json'))
            assert w.quote.findText('GUI_TEST') < 0
            assert w.quote.currentText() == 'WBNB'
            report['scenarios'].append('synthetic_remove_and_second_restart')
            report['rpc_methods'] = dict(calls)
            report['dialogs'] = dialogs
            output.write_text(json.dumps(report, indent=2, ensure_ascii=False)+'\n')
        finally:
            w.autopair_timer.stop()
            w.worker.quit_event.set()
            w.worker.wait(15000)
            w.busy = w.running = False
            w.close()
            QMessageBox.warning = original_warning
    print(json.dumps(report, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--endpoint', default='https://bsc-rpc.publicnode.com')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    run(args.endpoint, args.output)
