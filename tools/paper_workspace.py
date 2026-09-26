"""Interactive isolated PAPER window. Never starts trading automatically."""
import argparse
from pathlib import Path
import time
from unittest.mock import patch
from PySide6.QtWidgets import QApplication
from dipbot.ui.window import Window, STYLE
from dipbot.market.chain import Chain
from dipbot.persistence.storage import Store
from dipbot.persistence.vault import Vault
from dipbot.execution.trader import LiveTrader
from dipbot.checks.read_only import guard_provider


def main(token, pool, directory):
    directory.mkdir(parents=True, exist_ok=False)
    app = QApplication([])
    app.setStyleSheet(STYLE)
    original = Chain.__init__
    def init(chain, endpoint):
        original(chain, endpoint)
        guard_provider(chain.w3.provider)
    def forbidden(*args, **kwargs):
        raise RuntimeError('В этом окне доступны только PAPER и read-only RPC')
    with patch.object(Chain, '__init__', init), patch.object(Vault, 'get', forbidden), \
         patch.object(Vault, 'save', forbidden), patch.object(LiveTrader, 'send', forbidden):
        w = Window(Store(directory/'state.json'))
        w.setWindowTitle('DipBot · PAPER · ручное управление')
        w.mode.setCurrentText('PAPER')
        w.mode.model().item(2).setEnabled(False)
        w.show(); w.raise_(); w.activateWindow()
        def wait():
            deadline = time.monotonic()+120
            while w.busy:
                app.processEvents();time.sleep(.01)
                if time.monotonic()>deadline:
                    raise TimeoutError('RPC setup timed out')
        w.rpc.setText('https://bsc-rpc.publicnode.com')
        w.save_rpc.setChecked(False)
        w.send('connect', rpc=w.rpc.text(), save=False);wait()
        w.token.setText(token);w.pool_input.setText(pool)
        w.send('verify', token=token, pool=pool);wait()
        w.params['amount'].setText('0.00003')
        w.banner.setText('PAPER · Ручное управление · START запускает стратегию, STOP останавливает её. Реальных сделок нет.')
        w.journal_toggle.setChecked(False)
        w.tabs.widget(0).verticalScrollBar().setValue(0)
        app.processEvents()
        print('PAPER ready; selected=' + str(w.selection_ready) + '; worker=' + str(w.worker.isRunning()),flush=True)
        return app.exec()


if __name__ == '__main__':
    p=argparse.ArgumentParser()
    p.add_argument('--token',required=True);p.add_argument('--pool',required=True)
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();main(a.token,a.pool,a.output)
