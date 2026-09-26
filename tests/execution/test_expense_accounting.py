from dipbot.execution.accounting import expense_summary
from dipbot.persistence.storage import Store

OWNER='synthetic_owner'


def test_other_gas_is_counted_once_and_opening_cost_is_not_lost(tmp_path):
    store=Store(tmp_path/'state.json')
    store.data['closed_trades']={'sell':{'wallet':OWNER,'net_usd':'9.97',
        'entry_gas_hashes':['buy'],'exit_gas_hashes':['sell']}}
    store.data['gas_ledger']={h:{'wallet':OWNER,'usd':fee} for h,fee in
        [('buy','.01'),('sell','.02'),('converter','.03'),('cancel','.04'),('open_position','.05')]}
    result=expense_summary(store,OWNER)
    assert result['realized_less_other_gas_usd']=='9.85'
    assert result['unallocated_gas_usd']=='0.12'
    # Another wallet cannot affect the result.
    store.data['gas_ledger']['other']={'wallet':'other','usd':'999'}
    assert expense_summary(store,OWNER)==result


def test_old_allocation_and_missing_fx_cannot_claim_complete_net(tmp_path):
    store=Store(tmp_path/'state.json')
    store.data['closed_trades']={'old':{'wallet':OWNER,'net_usd':'1'}}
    assert expense_summary(store,OWNER)['realized_less_other_gas_usd'] is None
    store.data['closed_trades'].clear()
    store.data['gas_ledger']={'unknown':{'wallet':OWNER,'usd':None}}
    assert expense_summary(store,OWNER)['realized_less_other_gas_usd'] is None


def test_gas_before_first_completed_position_is_still_an_expense(tmp_path):
    store=Store(tmp_path/'state.json')
    store.data['gas_ledger']={'failed':{'wallet':OWNER,'usd':'.02'}}
    assert expense_summary(store,OWNER)['realized_less_other_gas_usd']=='-0.02'
