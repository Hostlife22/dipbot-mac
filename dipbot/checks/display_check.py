"""Visible read-only GUI audit, plus explicitly labelled market-data replay."""
import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PySide6.QtWidgets import QMessageBox, QPushButton

from dipbot.market.chain import Chain, USDT
from dipbot.persistence.storage import Store
from dipbot.persistence.vault import Vault
from dipbot.execution.trader import LiveTrader


def run(app, directory, replay_path, seconds=60):
    from dipbot.ui.window import Window
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    if (directory/'state.json').exists():
        raise ValueError('Use a fresh display-check directory')
    app.setQuitOnLastWindowClosed(False)
    report = {'frozen': bool(getattr(sys, 'frozen', False)), 'platform': app.platformName(),
              'transactions_sent': 0, 'checks': 0, 'mismatches': [], 'screenshots': []}
    errors, logs = [], []
    phase = ['live_rpc']
    snapshots = set()
    original_init = Chain.__init__
    def forbidden(*args, **kwargs):
        raise RuntimeError('Wallet and LIVE are disabled during display audit')
    def init(chain, endpoint):
        original_init(chain, endpoint)
        original = chain.w3.provider.make_request
        def read_only(method, params):
            if method not in {'eth_chainId', 'eth_getBlockByNumber', 'eth_getCode', 'eth_call'}:
                raise RuntimeError('Non-read RPC blocked')
            return original(method, params)
        chain.w3.provider.make_request = read_only
    with patch.object(Chain, '__init__', init), patch.object(Vault, 'get', forbidden), \
            patch.object(Vault, 'save', forbidden), patch.object(LiveTrader, 'send', forbidden), \
            patch.object(QMessageBox, 'warning', lambda *a: errors.append(a[2])):
        w = Window(Store(directory/'state.json'))
        w.setWindowTitle('DipBot · PAPER display check')
        w.worker.log.connect(logs.append)
        w.show()
        def pump():
            app.processEvents()
            time.sleep(.01)
        def wait(condition, timeout=60):
            deadline = time.monotonic()+timeout
            while not condition():
                if time.monotonic() > deadline:
                    raise TimeoutError('Display check timed out')
                pump()
        def capture(name):
            if name in snapshots:
                return
            snapshots.add(name)
            w.grab().save(str(directory/(name+'.png')))
            report['screenshots'].append(name+'.png')
        def check(kind, payload):
            if kind not in {'price', 'status'}:
                return
            report['checks'] += 1
            issues = []
            if kind == 'price' and w.selection_ready:
                if w.metrics['price'].text() != w.display_price(payload):
                    issues.append('price label')
                if not w.chart.values or w.chart.values[-1] != float(payload):
                    issues.append('chart last point')
            if kind == 'status':
                if w.metrics['position'].text() != f"{float(payload['position']):.8g}":
                    issues.append('target quantity')
                if payload['running'] and w.start.isEnabled():
                    issues.append('START enabled while running')
                active = payload['mode'] == w.mode.currentText() and (payload['running'] or float(payload['position']) > 0)
                expected = {k:v for k,v in payload.get('levels', {}).items() if float(v)>0} if active else {}
                if w.selection_ready and w.chart.levels != expected:
                    issues.append('chart strategy levels')
                if phase[0] == 'replay':
                    if float(payload['position']) > 0:
                        capture('replay_position')
                    elif any('SELL: TAKE_PROFIT' in line for line in logs):
                        capture('replay_after_tp')
            report['mismatches'].extend({'phase':phase[0], 'event':kind, 'issue':v} for v in issues)
        w.worker.event.connect(check)  # Runs after Window.on_event on the GUI thread.
        try:
            w.mode.setCurrentText('PAPER')
            w.router.setCurrentText('V2')
            w.tabs.setCurrentIndex(1)
            w.rpc.setText('https://bsc-rpc.publicnode.com')
            w.save_rpc.setChecked(False)
            next(b for b in w.findChildren(QPushButton) if b.text() == 'Подключить').click()
            wait(lambda:not w.busy)
            w.tabs.setCurrentIndex(0)
            w.token.setText(USDT)
            w.pool_input.setText('0x16b9a82891338f9bA80E2D6970FddA79D1eb0daE')
            next(b for b in w.findChildren(QPushButton) if b.text() == 'CHECK POOL').click()
            wait(lambda:not w.busy)
            assert w.selection_ready, errors
            w.params['amount'].setText('0.00003');w.interval.setValue(.1)
            w.start.click();wait(lambda:not w.busy)
            began = time.monotonic()
            last_progress = began
            while time.monotonic()-began < seconds:
                pump()
                assert w.running, errors
                if time.monotonic()-last_progress > 20:
                    last_progress = time.monotonic()
                    print(json.dumps({'phase':phase[0], 'checks':report['checks']}), flush=True)
            report['live_seconds'] = round(time.monotonic()-began,2)
            capture('live_paper')
            w.stop.click();wait(lambda:not w.running and not w.worker.stop_event.is_set())
            wait(lambda:not w.busy)
            w.mode.setCurrentText('DEMO')
            assert not w.chart.values and w.metrics['price'].text() == '—'
            report['mode_change_clears_chart'] = True
            w.mode.setCurrentText('PAPER')
            data = json.loads(Path(replay_path).read_text())['tokens'][0]
            w.send('verify',token=data['token'],pool=data['routes'][0]['pool'])
            wait(lambda:not w.busy)
            assert w.selection_ready, errors
            pool = w.worker.pool
            values = iter(sample['price'] for sample in data['market_samples'])
            last = [data['market_samples'][0]['price']]
            from dipbot.domain.strategy import D
            def price(_):
                last[0] = next(values, last[0])
                return D(last[0])
            # Saved real prices, consumed at the test interval; no claim of original timing.
            w.worker.chain = SimpleNamespace(verify_pool=lambda *a:pool, price=price, price_source='REPLAY')
            w.reset_price_display()
            phase[0] = 'replay'
            w.banner.setText('PAPER · ПОВТОР ЗАПИСАННЫХ ЦЕН · не текущий рынок')
            w.params['amount'].setText('0.00003')
            w.start.click();wait(lambda:not w.busy)
            began = time.monotonic()
            while w.running:
                pump()
                if time.monotonic()-began > 120:
                    raise TimeoutError('Replay failed to stop')
            wait(lambda:not w.worker.running)
            capture('replay_stopped')
            assert not w.worker.paper.position
            report['replay_buys'] = sum('PAPER BUY:' in line for line in logs)
            report['replay_tp'] = sum('SELL: TAKE_PROFIT' in line for line in logs)
            report['replay_sl'] = sum('SELL: STOP_LOSS' in line for line in logs)
            assert report['replay_buys'] and report['replay_tp'] and report['replay_sl']
            w.invalidate_discovery()
            assert not w.chart.values and w.metrics['price'].text() == '—'
            report['market_change_clears_chart'] = True
            w.close()
            report['clean_shutdown'] = not w.worker.isRunning()
            report['passed'] = not errors and not report['mismatches'] and report['clean_shutdown']
            assert report['passed'], report['mismatches'][:5]
        finally:
            w.worker.quit_event.set();w.worker.wait(15000)
            report['errors'] = errors
            (directory/'report.json').write_text(json.dumps(report,indent=2,ensure_ascii=False)+'\n')
    print(json.dumps(report,ensure_ascii=False),flush=True)
    return 0
