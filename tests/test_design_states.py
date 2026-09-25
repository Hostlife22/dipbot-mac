"""UI presentation regressions: state priority, stale prices, compact window layout."""
import time
from decimal import Decimal
from PySide6.QtCore import QPoint
from PySide6.QtWidgets import QApplication
import pytest
from test_app_autopair_flow import window
from test_autopair_dynamic import POOL
from test_audit_ui_modes import status
from dipbot.theme import STYLE


def test_stale_quote_changes_badge_without_mutating_strategy(window):
    w=window
    status(w,running=True)
    w.last_price=Decimal('0.000000000000015')
    w.last_quote_at=time.monotonic()-2
    before=w.worker.strategy.base
    w.update_quote_age()
    assert w.metrics['state'].text()=='STALE'
    assert 'устарела' in w.strategy_status.text()
    assert w.worker.strategy.base==before
    w.last_quote_at=time.monotonic()
    w.update_quote_age()
    assert w.metrics['state'].text()=='WAIT DIP'


def test_pending_and_search_visible_without_worker_status(window):
    w=window;w.mode.setCurrentText('PAPER')
    w.send('discover',token=POOL.token,quote='ALL',router='AUTO')
    assert w.metrics['state'].text()=='SEARCH'
    w.on_event('autopair','PENDING');w.on_event('busy',False)
    assert w.metrics['state'].text()=='PENDING'
    assert 'PENDING' in w.strategy_status.text()
    assert not w.start.isEnabled()


def test_error_survives_footer_refresh_and_clears_on_retry(window):
    w=window;w.mode.setCurrentText('PAPER')
    w.on_event('error','RPC timeout');w.on_event('busy',False)
    w.update_quote_age()
    assert 'RPC timeout' in w.strategy_status.text()
    w.send('discover',token=POOL.token,quote='ALL',router='AUTO')
    assert 'Поиск' in w.strategy_status.text()
    assert w.metrics['state'].text()=='SEARCH'


def test_live_lock_and_stop_take_priority(window):
    w=window;w.mode.setCurrentText('LIVE');w.on_event('selected',POOL)
    status(w,locked=True)
    w.last_quote_at=time.monotonic()-2;w.update_quote_age()
    assert w.metrics['state'].text()=='LOCKED'
    assert 'сверка' in w.strategy_status.text()
    assert not w.start.isEnabled()
    w.stop_bot()
    assert w.metrics['state'].text()=='STOPPING'
    assert 'Останавливается' in w.strategy_status.text()


@pytest.mark.parametrize('size',[(940,700),(1100,750),(1280,800),(1440,900)])
def test_compact_chart_and_footer_stay_visible(window,size):
    w=window;app=QApplication.instance();app.setStyleSheet(STYLE)
    w.show();w.resize(*size);app.processEvents()
    view=w.tabs.widget(0).viewport()
    top=w.chart.mapTo(view,QPoint(0,0))
    assert top.y()>=0 and top.y()+w.chart.height()<=view.height()
    assert w.footer.isVisible() and w.stop.isVisible()
    assert w.tabs.widget(0).horizontalScrollBar().maximum()==0
    assert w.footer.font().pixelSize()<=13
    w.hide()


def test_market_and_strategy_disclosures_are_independent(window):
    w=window;w.show()
    w.market_toggle.setChecked(True)
    assert w.token.isVisible() and not w.signal_mode.isVisible()
    w.market_toggle.setChecked(False);w.strategy_toggle.setChecked(True)
    assert not w.token.isVisible() and w.signal_mode.isVisible()
    w.hide()
