from decimal import Decimal as D
import time
import pytest
from test_app_autopair_flow import window
from test_autopair_dynamic import POOL
from dipbot.usd import select_rate, price_text


def test_usd_rate_uses_base_address_and_liquid_bsc_pair():
    token = '0xabc'
    def row(rate, liquidity, base=token, chain='bsc'):
        return {'baseToken': {'address': base}, 'chainId': chain,
                'priceUsd': rate, 'liquidity': {'usd': liquidity}}
    rows = [row('779.4', 100), row('10000', 1), row('NaN', 999),
            row('1', 10000, 'wrong'), row('2', 100000, chain='ethereum')]
    assert select_rate(rows, token.upper()) == D('779.4')
    with pytest.raises(ValueError):
        select_rate([row('0', 100), row('-1', 20)], token)
    assert price_text('3.223148573590968e-7', D('779.4')) == '$0.0002512122'
    assert price_text('100', D(1)) == '$100'


def test_usd_display_never_changes_strategy_values_and_expires(window):
    w = window
    w.mode.setCurrentText('PAPER')
    w.on_event('selected', POOL)
    w.on_event('price_context', {'source': 'BSC', 'quote': POOL.quote})
    w.usd.token = POOL.quote.lower()
    w.usd.rate = D('779.4'); w.usd.received_at = time.monotonic()
    w.on_event('price', '3.223148573590968e-7')
    levels = {'DIP': '3.126454116383239e-7'}
    w.on_event('status', {'running': True, 'mode': 'PAPER', 'locked': False,
        'position': '0', 'base': '3.223148573590968e-7', 'realized': '0', 'levels': levels})
    assert w.metrics['price'].text() == '$0.0002512122'
    assert w.metrics['base'].text() == '$0.0002512122'
    assert w.metric_captions['price'].text() == 'ЦЕНА, USD'
    assert w.chart.levels == levels  # Native values retained for strategy and audit.
    assert w.chart.values[-1] == float('3.223148573590968e-7')
    assert w.chart.usd_rate == D('779.4')
    assert '3.00%' in w.strategy_status.text()
    w.usd.received_at -= 91
    w.update_quote_age()
    assert not w.metrics['price'].text().startswith('$')
    assert w.chart.usd_rate is None
    assert 'USD недоступен' in w.quote_age.text()
    w.usd.received_at = time.monotonic(); w.refresh_currency()
    w.mode.setCurrentText('DEMO')
    assert w.usd.current() is None and w.chart.usd_rate is None
    assert w.metrics['price'].text() == '—'
