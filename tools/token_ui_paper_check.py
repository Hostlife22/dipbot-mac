"""Visible, isolated PAPER check on a specified live BSC market; no wallet access."""
import argparse
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal as D
import json
from pathlib import Path
import time
from unittest.mock import patch

from PySide6.QtWidgets import QApplication, QMessageBox, QPushButton
from dipbot.app import Window, STYLE
from dipbot.chain import Chain
from dipbot.storage import Store, Vault
from dipbot.trader import LiveTrader
from tools.read_only_probe import guard_provider


def run(token, directory, seconds, pool_address=None):
    directory.mkdir(parents=True, exist_ok=False)
    app = QApplication([])
    app.setStyleSheet(STYLE)
    app.setQuitOnLastWindowClosed(False)
    report = {'token': token, 'utc': datetime.now(timezone.utc).isoformat(), 'mode': 'PAPER',
              'transactions_sent': 0, 'checks': 0, 'mismatches': [], 'errors': [],
              'samples': [], 'trades': [], 'source': 'live BSC RPC, no replay'}
    logs, rpc = [], []
    phase = ['setup']
    original = Chain.__init__
    def init(chain, endpoint):
        original(chain, endpoint)
        rpc.append(guard_provider(chain.w3.provider))
    def forbidden(*a, **kw):
        raise RuntimeError('Wallet/LIVE disabled in PAPER test')
    with patch.object(Chain, '__init__', init), patch.object(Vault, 'get', forbidden), \
         patch.object(Vault, 'save', forbidden), patch.object(LiveTrader, 'send', forbidden), \
         patch.object(QMessageBox, 'warning', lambda *a: report['errors'].append(a[2])):
        w = Window(Store(directory/'state.json'))
        w.setWindowTitle('DipBot · видимая проверка PAPER · ' + token[:10])
        w.mode.setCurrentText('PAPER')
        # This dedicated test window cannot switch to LIVE.
        w.mode.model().item(2).setEnabled(False)
        w.worker.log.connect(logs.append)
        w.show();w.raise_();w.activateWindow()
        def pump():
            app.processEvents();time.sleep(.01)
        def wait(condition, timeout=120):
            start = time.monotonic()
            while not condition():
                pump()
                if time.monotonic()-start > timeout:
                    raise TimeoutError('GUI operation timed out')
        def capture(name):
            w.tabs.setCurrentIndex(0)
            w.tabs.widget(0).verticalScrollBar().setValue(0)
            app.processEvents()
            w.grab().save(str(directory/(name+'.png')))
        def check(kind, payload):
            if kind == 'price':
                report['checks'] += 1
                report['samples'].append({'time': time.monotonic(), 'price': payload, 'phase': phase[0]})
                if w.metrics['price'].text() != w.display_price(payload) or w.chart.values[-1] != float(payload):
                    report['mismatches'].append('price / chart')
            elif kind == 'status':
                report['checks'] += 1
                if w.metrics['position'].text() != f"{float(payload['position']):.8g}":
                    report['mismatches'].append('target quantity')
                expected = payload['levels'] if payload['running'] or D(payload['position']) > 0 else {}
                expected = {k:v for k,v in expected.items() if D(v)>0}
                if w.chart.levels != expected:
                    report['mismatches'].append('strategy levels')
            elif kind == 'trade_marker':
                report['trades'].append(dict(payload, phase=phase[0]))
                if payload['side'] == 'BUY':
                    expected = w.worker.paper.cost/(D(payload['price'])*(1+w.worker.paper.slippage/100))
                    if w.worker.paper.position != expected:
                        report['mismatches'].append('PAPER buy quantity formula')
        w.worker.event.connect(check)
        try:
            w.rpc.setText('https://bsc-rpc.publicnode.com')
            w.save_rpc.setChecked(False)
            next(b for b in w.findChildren(QPushButton) if b.text() == 'Подключить').click()
            wait(lambda:not w.busy)
            w.token.setText(token)
            w.router.setCurrentText('AUTO');w.quote.setCurrentText('WBNB')
            w.market_toggle.setChecked(True)
            if pool_address:
                w.pool_input.setText(pool_address)
                next(b for b in w.findChildren(QPushButton) if b.text() == 'CHECK POOL').click()
            else:
                w.send('discover', token=token, quote='WBNB', router='AUTO')
            wait(lambda:not w.busy)
            report['candidates'] = [w.candidates.itemText(i) for i in range(w.candidates.count())]
            if not w.selection_ready and w.candidates.count():
                w.candidates.setCurrentIndex(0);w.select_pool();wait(lambda:not w.busy)
            assert w.selection_ready, report['errors'] or w.pool_label.text()
            pool = w.worker.pool
            report['pool'] = pool.address
            report['router'] = pool.router
            report['token_decimals'] = pool.token_decimals
            w.market_toggle.setChecked(False)
            w.params['amount'].setText('0.00003')
            w.params['dip'].setText('3');w.params['take_profit'].setText('2');w.params['stop_loss'].setText('2')
            report['settings'] = {k:v.text() for k,v in w.params.items()}
            # Manual PAPER actions are explicitly separate from natural strategy signals.
            phase[0] = 'manual_paper_buy'
            w.banner.setText('PAPER · ПРОВЕРКА BUY NOW · реальная цена BSC, виртуальная покупка')
            w.buy.click();wait(lambda:not w.busy)
            assert w.worker.paper.position > 0, report['errors']
            capture('manual_buy')
            until = time.monotonic()+12
            while time.monotonic()<until:pump()
            phase[0] = 'manual_paper_sell'
            w.sell.click();wait(lambda:not w.busy)
            assert not w.worker.paper.position, report['errors']
            report['manual_realized'] = str(w.worker.paper.realized)
            capture('manual_sell')
            phase[0] = 'automatic_live_prices'
            w.banner.setText('PAPER · НАБЛЮДЕНИЕ РЕАЛЬНОГО РЫНКА · DIP 3% / TP 2% / SL 2%')
            w.start.click();wait(lambda:not w.busy)
            began = progress = time.monotonic()
            while time.monotonic()-began < seconds and w.running:
                pump()
                if time.monotonic()-progress > 30:
                    progress = time.monotonic()
                    capture('automatic')
                    print(json.dumps({'elapsed': round(progress-began), 'samples': len(report['samples']),
                        'trades': report['trades'], 'status': w.strategy_status.text()}, ensure_ascii=False), flush=True)
            report['observed_seconds'] = round(time.monotonic()-began,2)
            report['automatic_running_at_end'] = w.running
            capture('automatic_end')
            phase[0] = 'stop'
            w.stop.click();wait(lambda:not w.running and not w.worker.stop_event.is_set() and not w.busy)
            report['clean_stop'] = not w.worker.paper.position
            capture('stopped')
            report['passed'] = report['clean_stop'] and not report['errors'] and not report['mismatches']
        except Exception as exc:
            report['failure'] = str(exc)
            report['passed'] = False
        finally:
            w.worker.stop_event.set()
            wait(lambda:not w.worker.running and not w.worker.stop_event.is_set(), 60)
            w.worker.quit_event.set();w.worker.wait(15000)
            report['logs'] = logs
            calls = Counter()
            for counter in rpc:calls.update(counter)
            report['rpc_methods'] = dict(calls)
            prices = [D(s['price']) for s in report['samples']]
            report['distinct_prices'] = len(set(prices))
            if prices:report['price_range'] = [str(min(prices)),str(max(prices))]
            (directory/'report.json').write_text(json.dumps(report,indent=2,ensure_ascii=False)+'\n')
            print(json.dumps({k:v for k,v in report.items() if k not in {'logs','samples'}},ensure_ascii=False),flush=True)
        # Hand control back to the user; a visible app must not retain dead controls.
        w.worker.event.disconnect(check)
        w.worker.log.disconnect(logs.append)
        w.worker.quit_event.clear()
        w.worker.stop_event.clear()
        w.worker.start()
        w.banner.setText('PAPER · Проверка завершена. Доступно ручное управление; реальные сделки отключены.')
        w.update_controls()
        app.setQuitOnLastWindowClosed(True)
        app.exec()


if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--token',required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--seconds',type=int,default=300)
    parser.add_argument('--pool',help='Explicit pool, still verified against canonical factory')
    args=parser.parse_args();run(args.token,args.output,args.seconds,args.pool)
