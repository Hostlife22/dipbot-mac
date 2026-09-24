"""Explicit, read-only PAPER acceptance mode for the packaged application."""
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QPushButton, QMessageBox, QScrollArea

from .chain import Chain, USDT
from .storage import Store, Vault
from .trader import LiveTrader


def run(app, directory, seconds, resume=False):
    from .app import Window
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / 'state.json'
    if not resume and path.exists():
        raise ValueError('Acceptance requires a new directory')
    if resume and not path.exists():
        raise ValueError('Acceptance state is missing')
    methods, events, logs, errors = Counter(), [], [], []
    report = {'utc': datetime.now(timezone.utc).isoformat(), 'frozen': bool(getattr(sys, 'frozen', False)),
              'platform': app.platformName(), 'resume': resume, 'transactions_sent': 0}
    def forbidden(*args, **kwargs):
        raise RuntimeError('Signing, transactions and Keychain are disabled in acceptance mode')
    LiveTrader.send = forbidden
    Vault.get = Vault.save = forbidden
    original_init = Chain.__init__
    fault = {'enabled': False}
    def init(chain, endpoint):
        original_init(chain, endpoint)
        original = chain.w3.provider.make_request
        def read_only(method, params):
            if method not in {'eth_chainId', 'eth_getBlockByNumber', 'eth_getCode', 'eth_call'}:
                raise RuntimeError('RPC method blocked in acceptance mode')
            methods[method] += 1
            if fault['enabled']:
                raise ConnectionError('synthetic RPC outage')
            return original(method, params)
        chain.w3.provider.make_request = read_only
    Chain.__init__ = init
    QMessageBox.warning = lambda *args: errors.append(args[2])
    w = Window(Store(path))
    w.worker.log.connect(logs.append)
    w.worker.event.connect(lambda name, value: events.append((name, value)))
    w.show()
    def wait(predicate, timeout=90):
        until = time.monotonic() + timeout
        while not predicate():
            if time.monotonic() >= until:
                raise TimeoutError('Packaged PAPER acceptance timeout')
            QTest.qWait(20)
    def click(label):
        widget = next(x for x in w.findChildren(QPushButton) if x.text() == label)
        assert widget.isEnabled(), label
        parent = widget.parentWidget()
        while parent:
            if isinstance(parent, QScrollArea):
                parent.ensureWidgetVisible(widget)
            parent = parent.parentWidget()
        QTest.mouseClick(widget, Qt.LeftButton)
    def idle():
        wait(lambda: not w.busy)
    def stop():
        click('STOP · закрыть позицию')
        wait(lambda: not w.running and not w.worker.running and not w.worker.stop_event.is_set())
    def start():
        click('START BOT')
        idle()
        wait(lambda: w.running)
    try:
        if resume:
            assert w.router.currentText() == 'V2'
            assert w.quote.currentText() == 'WBNB'
            assert w.params['amount'].text() == '0.00003'
            assert w.mode.currentText() == 'DEMO' and not w.selection_ready
            report['settings_restored_without_autostart'] = True
        w.tabs.setCurrentIndex(1)
        w.rpc.setText('https://bsc-dataseed.binance.org')
        w.save_rpc.setChecked(False)
        click('Подключить'); idle()
        assert w.worker.chain is not None, errors
        w.tabs.setCurrentIndex(0)
        w.mode.setCurrentText('PAPER'); w.router.setCurrentText('V2')
        w.token.setText(USDT)
        click('AutoPair · найти пулы'); idle()
        assert w.selection_ready, errors
        w.params['amount'].setText('0.00003')
        w.interval.setValue(0.5)
        start()
        began = time.monotonic()
        restarted = False
        while time.monotonic() - began < seconds:
            QTest.qWait(100)
            assert w.running, errors
            if not restarted and time.monotonic() - began >= seconds / 2:
                stop(); start(); restarted = True
        report['observation_seconds'] = round(time.monotonic() - began, 2)
        report['stop_restart'] = restarted
        # Failure is injected only after normal market observation; retain its origin.
        index = len(errors)
        fault['enabled'] = True
        wait(lambda: not w.running and len(errors) > index)
        report['injected_rpc_failure_paused'] = True
        fault['enabled'] = False
        start()
        index = len(events)
        wait(lambda: any(name == 'price' for name, _ in events[index:]))
        stop()
        report['restart_after_rpc_failure'] = True
        report['market_price_observations'] = sum(name == 'price' for name, _ in events)
        report['market_buys'] = sum('PAPER BUY:' in line for line in logs)
        report['market_take_profits'] = sum('SELL: TAKE_PROFIT' in line for line in logs)
        report['market_stop_losses'] = sum('SELL: STOP_LOSS' in line for line in logs)
        report['gap_resets'] = sum('0.55' in line for line in logs)
        assert not w.worker.paper.position
        w.grab().save(str(directory / ('resume.png' if resume else 'paper.png')))
        w.close()
        assert not w.isVisible() and not w.worker.isRunning()
        report['clean_shutdown'] = True
        report['passed'] = True
    except Exception as exc:
        report['passed'] = False
        report['error_type'] = type(exc).__name__
        raise
    finally:
        fault['enabled'] = False
        w.worker.quit_event.set()
        w.worker.wait(15000)
        report['rpc_methods'] = dict(methods)
        report['errors'] = errors
        (directory / ('resume.json' if resume else 'paper.json')).write_text(
            json.dumps(report, indent=2, ensure_ascii=False) + '\n')
    return 0
