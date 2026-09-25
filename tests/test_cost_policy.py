from dataclasses import replace
from decimal import Decimal as D
import time
import pytest
from dipbot.accounting import RateBook
from dipbot.chain import WBNB, USDT
from dipbot.cost_policy import CostPolicy
from dipbot.entry_guard import EntryQuote, EntryRejected
from test_autopair_dynamic import POOL


def test_cost_ceiling_includes_model_gas_for_small_position():
    q=EntryQuote(10**14,1,995*10**11,1,D('.5'))
    pool=replace(POOL,quote=WBNB,quote_decimals=18)
    with pytest.raises(EntryRejected,match='40.50'):
        CostPolicy(D(3)).assess(q,pool,D('.1'),RateBook())
    assert CostPolicy().assess(q,pool,D('.1'),RateBook()) is None


def test_cost_conversion_requires_fresh_rates_and_exact_boundary():
    q=EntryQuote(10**18,1,995*10**15,1,D('.5'))
    pool=replace(POOL,quote=USDT,quote_decimals=18)
    book=RateBook()
    with pytest.raises(EntryRejected,match='USD'):
        CostPolicy(D('3.7')).assess(q,pool,D('.1'),book)
    book.update(WBNB,D(800),time.monotonic())
    book.update(USDT,D(1),time.monotonic())
    assert CostPolicy(D('3.7')).assess(q,pool,D('.1'),book)==D('3.7')


@pytest.mark.parametrize('data',[{'maximum_pct':'NaN'},{'roundtrip_gas':'1.5'},{'roundtrip_gas':0}])
def test_invalid_model(data):
    with pytest.raises(ValueError):CostPolicy.parse(data)


def test_cost_policy_preferences_survive_restart(tmp_path):
    from dipbot import preferences
    from dipbot.storage import Store
    store=Store(tmp_path/'state.json')
    value=preferences.from_windows_ui({})
    value['entry_cost_policy']=CostPolicy(D('1.5'),500000).export()
    preferences.save(store,value)
    assert preferences.normalize(Store(store.path).data['ui_preferences'])['entry_cost_policy']==value['entry_cost_policy']
