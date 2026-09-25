"""Visible read-only PAPER buy/sell using real amount quotes; no wallet access."""
import argparse
import json
import time
from pathlib import Path
from unittest.mock import patch
from PySide6.QtWidgets import QApplication, QMessageBox, QPushButton
from dipbot.app import Window, STYLE
from dipbot.chain import Chain, USDT
from dipbot.storage import Store, Vault
from dipbot.trader import LiveTrader
from tools.read_only_probe import guard_provider


def run(output):
    output.mkdir(parents=True, exist_ok=False)
    report = {'transactions_sent': 0, 'errors': [], 'passed': False}
    app = QApplication([]);app.setStyleSheet(STYLE)
    original = Chain.__init__
    def init(chain, endpoint):
        original(chain, endpoint)
        guard_provider(chain.w3.provider)
    def forbidden(*a, **kw):
        raise RuntimeError('Wallet and LIVE disabled')
    with patch.object(Chain, '__init__', init), patch.object(Vault, 'get', forbidden), \
         patch.object(Vault, 'save', forbidden), patch.object(LiveTrader, 'send', forbidden), \
         patch.object(QMessageBox, 'warning', lambda *a:report['errors'].append(a[2])):
        w=Window(Store(output/'state.json'));w.show()
        def wait():
            deadline=time.monotonic()+90
            while w.busy:
                app.processEvents();time.sleep(.01)
                if time.monotonic()>deadline:raise TimeoutError('UI operation')
            app.processEvents()
            assert not report['errors'],report['errors']
        def click(label):
            button=next(b for b in w.findChildren(QPushButton) if b.text()==label)
            assert button.isEnabled(),label
            button.click();wait()
        try:
            w.mode.setCurrentText('PAPER');w.router.setCurrentText('V2')
            w.rpc.setText('https://bsc-dataseed.binance.org')
            click('Подключить')
            w.token.setText(USDT)
            w.pool_input.setText('0x16b9a82891338f9bA80E2D6970FddA79D1eb0daE')
            click('CHECK POOL')
            w.params['amount'].setText('0.00003')
            click('BUY NOW')
            assert w.worker.paper.position > 0
            report['received_target']=str(w.worker.paper.position)
            report['cost_base']=str(w.worker.paper.cost)
            w.grab().save(str(output/'position.png'))
            click('SELL POSITION')
            assert w.worker.paper.position == 0
            report['realized_base_excluding_gas_tax']=str(w.worker.paper.realized)
            w.grab().save(str(output/'closed.png'))
            report['passed']=True
        finally:
            w.worker.quit_event.set();w.worker.wait(15000)
            w.hide();w.deleteLater();app.processEvents()
            (output/'report.json').write_text(json.dumps(report,indent=2,ensure_ascii=False)+'\n')
    print(json.dumps(report,ensure_ascii=False))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True)
    run(parser.parse_args().output)
