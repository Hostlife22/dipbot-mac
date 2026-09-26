"""Keep Mac accounting conservative when a Sweep report has no known residuals."""
from dipbot.persistence.storage import Store
from test_expanded_scenarios import multi_worker, POOL


def test_empty_known_residuals_with_failed_balance_do_not_flatten_position(tmp_path):
    worker,other,balances,sent,reports=multi_worker(tmp_path)
    def balance(token,owner):
        if token==POOL.token:raise TimeoutError('synthetic unavailable balance')
        return balances.get(token,0)
    worker.chain.balance=balance
    worker.sweep()
    assert len(sent)==1 and other.token in sent[0]
    assert reports[0]['remaining']=={}
    assert POOL.token in reports[0]['unknown'] and POOL.token in reports[0]['failed']
    saved=Store(worker.store.path).data['positions']
    assert len(saved)==1 and next(iter(saved.values()))['amount']==200
    assert worker.strategy.entry is not None
