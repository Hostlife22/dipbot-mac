"""Offline, explicitly synthetic Qt acceptance scenarios; no network or wallet."""
from dataclasses import asdict
import json
from pathlib import Path
import sys
import time
from unittest.mock import patch
from types import SimpleNamespace

from PySide6.QtWidgets import QMessageBox
from .chain import Chain, Pool, WBNB, USDT, address
from .storage import Store, Vault
from .strategy import D
from .trader import LiveTrader, UncertainTransaction


def run(app, directory, resume=False):
    from .app import Window
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory/'state.json'
    if path.exists() != resume:
        raise ValueError('Use a fresh directory, or --position-check-resume for saved fixture')
    app.setQuitOnLastWindowClosed(False)
    pool = Pool(address('0x'+'12'*20), 'V2', address(USDT), address(WBNB), 18, 18, True)
    owner = address('0x'+'34'*20)
    report = {'frozen': bool(getattr(sys, 'frozen', False)), 'synthetic': True,
              'resume': resume, 'transactions_sent': 0, 'scenarios': [], 'passed': False}
    errors, logs, trades = [], [], []
    market = {'price': D(100), 'offline': False, 'sell_failure': False}
    def forbidden(*a, **kw):
        raise RuntimeError('Network, wallet and signing disabled in position check')
    def price(_):
        if market['offline']:
            raise TimeoutError()
        return market['price']
    def quote(pool, amount, buy):
        if market['sell_failure'] and not buy:
            raise TimeoutError()
        return int(D(amount)/market['price'] if buy else D(amount)*market['price'])
    with patch.object(Chain, '__init__', forbidden), patch.object(Vault, 'get', forbidden), \
         patch.object(Vault, 'save', forbidden), patch.object(LiveTrader, 'send', forbidden), \
         patch.object(QMessageBox, 'warning', lambda *a: errors.append(a[2])):
        w = Window(Store(path))
        w.show(); w.raise_(); w.activateWindow()
        w.banner.setText('СИНТЕТИЧЕСКАЯ ПРОВЕРКА · без сети, кошелька и реальных сделок')
        w.worker.log.connect(logs.append)
        w.worker.event.connect(lambda name, value: trades.append(value) if name == 'trade_marker' else None)
        def wait(predicate, timeout=12):
            end = time.monotonic()+timeout
            while not predicate():
                if time.monotonic() > end:
                    raise TimeoutError('Position check timed out')
                app.processEvents(); time.sleep(.01)
            app.processEvents()
        def snap(name):
            w.banner.setText('СИНТЕТИЧЕСКАЯ ПРОВЕРКА · без сети, кошелька и реальных сделок')
            w.journal_toggle.setChecked(False)
            app.processEvents()
            w.grab().save(str(directory/(name+'.png')))
        def select(mode):
            w.mode.setCurrentText(mode)
            w.worker.chain = SimpleNamespace(price=price, quote=quote, verify_pool=lambda *a: pool, price_source='REPLAY')
            w.worker.select_pool(pool)
            wait(lambda:w.selection_ready)
            w.params['amount'].setText('1')
            w.params['dip'].setText('3')
            w.params['take_profit'].setText('2')
            w.params['stop_loss'].setText('2')
            w.interval.setValue(.1)
        def start_buy():
            market['price'] = D(100)
            w.start.click()
            wait(lambda:w.running and w.worker.strategy.base == 100)
            market['price'] = D(96)
            wait(lambda:w.display_position > 0)
            assert w.worker.strategy.entry == 96
            quantity = D(int(D(10)**18/96))/D(10)**18
            assert w.worker.paper.position == quantity
            assert w.chart.levels == {'ENTRY':'96', 'TP':'97.92', 'SL':'94.08'}
            assert w.metrics['position'].text() == f'{float(quantity):.8g}'
            return quantity
        def stop():
            w.stop.click()
            wait(lambda:not w.running and not w.busy and not w.worker.stop_event.is_set())
        try:
            if resume:
                operation = w.store.data['operation'].copy()
                positions = w.store.data['positions'].copy()
                assert not w.worker.running and w.worker.chain is None and w.mode.currentText() == 'DEMO'
                assert not w.recovery_notice.isHidden()
                assert w.router.currentText() == 'V2' and w.quote.currentText() == 'WBNB'
                assert w.params['amount'].text() == '1'
                w.quote.setCurrentText('USDT'); assert w.params['amount'].text() == '0.25'
                w.quote.setCurrentText('WBNB'); assert w.params['amount'].text() == '1'
                report['scenarios'].append('pair_amounts_restored_without_autostart')
                snap('startup_recovery')
                w.recovery_notice.click(); assert w.tabs.currentIndex() == 1
                w.prepare_saved_position()
                assert w.token.text() == pool.token and w.pool_input.text() == pool.address
                assert not w.selection_ready and not w.start.isEnabled()
                select('LIVE')
                w.worker.mode = 'LIVE'
                w.worker.live = SimpleNamespace(owner=owner)
                w.worker.strategy.entry = D(w.worker.position()['entry'])
                w.worker.status()
                wait(lambda:w.locked and w.display_position > 0)
                assert w.metrics['state'].text() == 'LOCKED'
                assert not w.start.isEnabled() and not w.buy.isEnabled() and not w.sell.isEnabled()
                assert w.worker.position()['amount'] == 10**16
                try:
                    w.worker.observe()
                except UncertainTransaction:
                    pass
                else:
                    raise AssertionError('Pending operation did not block observation')
                assert w.store.data['operation'] == operation and w.store.data['positions'] == positions
                snap('live_restart_locked')
                report['scenarios'].append('fresh_process_restores_position_and_pending_lock')
                from web3 import Web3
                from web3.exceptions import TransactionNotFound
                pending = [True]
                def receipt(tx_hash):
                    if pending[0]:
                        raise TransactionNotFound('synthetic pending')
                    return {'transactionHash':Web3.to_bytes(hexstr=tx_hash), 'status':1, 'blockNumber':7}
                w.worker.chain.check = lambda:7
                w.worker.chain.w3 = SimpleNamespace(eth=SimpleNamespace(get_transaction_receipt=receipt))
                w.show_recovery(); w.check_receipts()
                wait(lambda:not w.busy and len(errors) == 1)
                assert 'Не найден receipt' in w.receipt_result.text()
                assert w.locked and w.store.data['positions'] == positions
                pending[0] = False
                w.check_receipts(); wait(lambda:not w.busy and 'Все записанные' in w.receipt_result.text())
                assert w.locked and not w.start.isEnabled()
                assert w.store.data['operation']['transactions'][0]['status'] == 'confirmed'
                snap('receipt_review_locked')
                report['scenarios'].append('keyless_receipt_review_pending_then_confirmed_stays_locked')
            else:
                select('PAPER')
                quantity = start_buy(); snap('dip_buy')
                before = w.worker.paper.realized
                market['price'] = D(99)
                wait(lambda:not w.display_position and any('TAKE_PROFIT' in x for x in logs))
                expected = D(int(quantity*99*D(10)**18))/D(10)**18-D(1)
                assert w.worker.paper.realized-before == expected
                assert format(w.worker.paper.realized, '.8g') in w.footer.text()
                snap('take_profit'); stop()
                report['scenarios'].append('automatic_dip_buy_tp_quantity_levels_pnl')
                start_buy()
                buys = sum(t['side']=='BUY' for t in trades)
                market['offline'] = True
                wait(lambda:w.metrics['state'].text() == 'WAIT RPC')
                assert w.display_position > 0 and w.running and 'TP/SL временно недоступны' in w.strategy_status.text()
                snap('open_position_rpc_outage')
                market['price'] = D(93); market['offline'] = False
                wait(lambda:not w.running and not w.display_position)
                assert any('STOP_LOSS' in x for x in logs)
                assert sum(t['side']=='BUY' for t in trades) == buys
                snap('stop_loss')
                report['scenarios'].append('rpc_outage_with_position_then_sl_without_duplicate_buy')
                start_buy(); stop()
                assert not w.worker.paper.position
                report['scenarios'].append('stop_closes_open_position')
                start_buy(); market['sell_failure'] = True
                stop()
                assert w.display_position > 0 and w.metrics['state'].text() == 'ERROR'
                assert w.sell.isEnabled() and w.worker.strategy.entry == 96
                assert len(errors) == 1 and 'TimeoutError' in errors[0]
                snap('stop_sale_failed')
                market['sell_failure'] = False
                w.sell.click(); wait(lambda:not w.busy and not w.display_position)
                assert not w.worker.paper.position
                assert w.metrics['state'].text() == 'IDLE' and not w.worker.halt_reason
                snap('manual_sell_recovered')
                report['scenarios'].append('failed_stop_preserves_position_then_manual_sell')
                # Known synthetic fixture for a second, fresh application process.
                w.store.data['positions'] = {owner.lower()+':'+pool.address.lower():
                    {'amount':10**16, 'entry':'96', 'pool':asdict(pool)}}
                w.store.data['operation'] = {'description':'synthetic unknown BUY', 'wallet':owner,
                    'transactions':[{'hash':'0x'+'56'*32, 'status':'pending'}]}
                w.store.save()
                report['scenarios'].append('saved_synthetic_restart_fixture')
                w.quote.setCurrentText('USDT'); w.params['amount'].setText('0.25')
                w.quote.setCurrentText('WBNB'); w.params['amount'].setText('1')
                w.close()
                assert not w.isVisible()
            report['passed'] = True
        finally:
            w.worker.quit_event.set(); assert w.worker.wait(15000)
            report.update(errors=errors, logs=logs, trades=trades)
            (directory/('resume.json' if resume else 'report.json')).write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
            w.hide(); w.deleteLater(); app.processEvents()
    print(json.dumps(report,ensure_ascii=False),flush=True)
    return 0
