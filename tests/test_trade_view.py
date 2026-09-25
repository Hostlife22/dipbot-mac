import time
from decimal import Decimal as D
from dipbot.trade_view import entry_view, exit_view
from test_app_autopair_flow import window
from test_audit_ui_modes import status


def test_price_rises_but_fees_make_loss_without_double_pool_fee():
    entry=entry_view(D(100), D(1), '.01', {'usd':'1'})
    result=exit_view(entry,D(100),D('1.0059'),'.01',{'usd':'1'})
    assert D(result['sell_price_usd'])>D(result['buy_price_usd'])
    assert D(result['net_usd'])==D('-.0141')
    assert D(result['entry_total_usd'])==D('1.01')


def test_missing_fx_and_partial_exit_never_fabricate_profit():
    entry=entry_view(D(100),D(1),None,None)
    assert entry['buy_price_usd'] is None and entry['entry_total_usd'] is None
    assert exit_view(entry,D(100),D(2),'.01',{'usd':'1'})['net_usd'] is None
    entry=entry_view(D(100),D(1),'.01',{'usd':'1'})
    result=exit_view(entry,D(50),D('.6'),'.01',{'usd':'1'},complete=False)
    assert result['net_usd'] is None and result['sell_price_usd']=='0.012'


def test_entry_fx_is_historical_and_gas_counted_once():
    entry=entry_view(D(100),D(1),'.1',{'usd':'10'})
    result=exit_view(entry,D(100),D(1),'.2',{'usd':'11'})
    assert D(result['net_usd'])==D('.7')


def test_ui_separates_closed_result_and_stale_open_estimate(window):
    from test_autopair_dynamic import POOL
    w=window;w.mode.setCurrentText('PAPER');w.on_event('selected',POOL)
    detail=exit_view(entry_view(D(100),D(1),'.01',{'usd':'1'}),D(100),D('1.0059'),'.01',{'usd':'1'})
    status(w,position='100',trade_detail=detail,open_estimate={'at':time.monotonic(),'value_usd':'1.1','pnl_usd':'.09','excludes_exit_gas':True})
    assert 'Средняя цена BUY' in w.trade_details.text()
    assert '-0.0141' in w.trade_details.text()
    assert 'без будущего газа SELL' in w.position_estimate.text()
    before=w.footer.text()
    w.pnl_status['open_estimate']['at']-=1
    w.refresh_trade_details()
    assert 'нет свежей' in w.position_estimate.text() and w.footer.text()==before
    w.mode.setCurrentText('DEMO');w.refresh_trade_details()
    assert 'Нет данных' in w.trade_details.text()


def test_worker_breakdown_matches_paper_ledger(tmp_path):
    from types import SimpleNamespace
    from dipbot.worker import Worker
    from dipbot.storage import Store
    from dipbot.paper_policy import PaperPolicy
    from test_autopair_dynamic import POOL
    w=Worker(Store(tmp_path/'state.json'));w.mode='PAPER';w.pool=POOL
    from dataclasses import replace
    w.strategy.settings=replace(w.strategy.settings,amount=D(1))
    w.current_price=D('.01');w.read_price=lambda:D('.01')
    w.paper_policy=PaperPolicy(fee_quote=D('.01'),latency_seconds=0)
    w.rates.update(POOL.quote,D(1),time.monotonic())
    w.chain=SimpleNamespace(quote=lambda pool,amount,buy:100*10**18 if buy else 10059*10**14)
    w.open_position()
    assert D(w.trade_detail['buy_price_usd'])==D('.01')
    assert D(w.trade_detail['entry_total_usd'])==D('1.01')
    w.close_position('TIME_EXIT')
    assert D(w.trade_detail['sell_price_usd'])==D('.010059')
    assert D(w.trade_detail['net_usd'])==w.paper.realized==w.paper_usd['value']==D('-.0141')


def test_missing_legacy_live_cost_is_not_zero(tmp_path):
    from types import SimpleNamespace
    from dipbot.worker import Worker
    from dipbot.storage import Store
    from test_autopair_dynamic import POOL
    w=Worker(Store(tmp_path/'state.json'));w.mode='LIVE';w.pool=POOL
    w.live=SimpleNamespace(owner='0x'+'34'*20)
    w.set_position(100,D(1))
    detail=w.current_trade_detail()
    assert detail['entry_gross_usd'] is None and detail['entry_total_usd'] is None
