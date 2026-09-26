from dataclasses import replace
from decimal import Decimal as D
from types import SimpleNamespace
import pytest
from test_app_autopair_flow import window
from test_autopair_dynamic import POOL
from dipbot.ui.window import QMessageBox
from dipbot.application.worker import Worker
from dipbot.persistence.storage import Store
from test_worker import config


def status(w, **changes):
    data = dict(mode=w.mode.currentText(), running=False, locked=False,
                position='0', base='1', realized='0', levels={})
    data.update(changes)
    w.on_event('status', data)


@pytest.mark.parametrize('mode', ['DEMO', 'PAPER', 'LIVE'])
def test_open_position_locks_market_but_allows_resume_and_exit(window, mode):
    w = window; w.mode.setCurrentText(mode);w.on_event('selected', POOL)
    status(w, position='10', levels={'ENTRY':'1','TP':'1.02','SL':'.98'})
    assert not w.mode.isEnabled() and not w.router.isEnabled()
    assert not w.token.isEnabled() and not w.quote.isEnabled()
    assert not w.buy.isEnabled()
    assert w.sell.isEnabled() and w.start.isEnabled() and w.stop.isEnabled()
    status(w)
    assert w.mode.isEnabled() and not w.sell.isEnabled()


def test_stop_feedback_waits_for_worker_confirmation(window):
    w = window
    status(w, running=True)
    w.stop.click()
    assert w.stop_pending and 'Останавливается' in w.strategy_status.text()
    assert not w.start.isEnabled() and not w.buy.isEnabled()
    status(w, running=False)  # The stop flag is still pending.
    assert w.stop_pending
    w.worker.stop_event.clear()
    status(w)
    assert not w.stop_pending and w.start.isEnabled()


def test_live_lock_blocks_new_start_and_buy(window):
    w=window;w.mode.setCurrentText('LIVE');w.on_event('selected',POOL)
    status(w,locked=True)
    assert not w.start.isEnabled() and not w.buy.isEnabled()
    assert w.stop.isEnabled()


@pytest.mark.parametrize('command', ['start','buy','convert','sweep'])
def test_live_confirmation_cancel_never_submits(window, monkeypatch, command):
    w=window;w.mode.setCurrentText('LIVE');w.on_event('selected',POOL)
    monkeypatch.setattr(QMessageBox,'question',lambda *a:QMessageBox.No)
    submitted=[];monkeypatch.setattr(w.worker,'submit',lambda *a,**kw:submitted.append((a,kw)))
    w.trade(command, **({"buy":True,"amount":"0.00003"} if command == "convert" else {}))
    assert submitted == []


def test_paper_ledger_separates_modes_and_markets(tmp_path):
    w=Worker(Store(tmp_path/'state.json'))
    demo=config();w.configure(demo);w.paper.realized=D(12)
    w.pool=POOL;w.chain=SimpleNamespace(verify_pool=lambda *a:POOL)
    paper=demo|{'mode':'PAPER','token':POOL.token,'pool':POOL.address,'router':POOL.router}
    w.configure(paper)
    assert w.paper.realized == 0
    w.paper.realized=D(3);w.configure(paper)
    assert w.paper.realized == 3  # STOP/START on the same market keeps its ledger.
    w.pool=replace(POOL,quote='0x'+'77'*20)
    w.configure(paper)
    assert w.paper.realized == 0
    w.paper.realized=D(4);w.configure(demo)
    assert w.paper.realized == 0
