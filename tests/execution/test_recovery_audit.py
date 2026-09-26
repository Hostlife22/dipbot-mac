from tests.support.recovery import OWNER, POOL, setup_worker, sweep_worker
"""Independent safety invariants; every wallet/state/chain here is synthetic."""
from dataclasses import asdict
from types import SimpleNamespace
import pytest
from dipbot.market.chain import Pool, WBNB, USDT, address
from dipbot.persistence.storage import Store
from dipbot.domain.strategy import D
from dipbot.application.worker import Worker
from dipbot.execution.errors import UncertainTransaction





@pytest.mark.parametrize('stage', ['configure', 'balance'])
def test_stop_during_converter_preparation_never_begins(tmp_path, stage):
    worker = setup_worker(tmp_path)
    def configure(_):
        if stage == 'configure': worker.stop_event.set()
    def balance(*_):
        if stage == 'balance': worker.stop_event.set()
        return 200
    worker.configure = configure
    worker.chain = SimpleNamespace(balance=balance)
    worker.live.begin = lambda _: pytest.fail('Must not begin after STOP')
    with pytest.raises(ValueError, match='STOP'):
        worker.command('convert', {'buy': False})




def test_sweep_recovers_saved_pool_without_known_catalog_after_restart(tmp_path):
    worker, sent = sweep_worker(tmp_path)
    worker.store = Store(worker.store.path)
    assert not worker.store.data.get('known_pools')
    worker.sweep()
    assert len(sent) == 1 and sent[0].startswith('SWEEP TARGET')
    assert not Store(worker.store.path).data['positions']
    assert worker.strategy.entry is None


def test_skipped_sweep_retains_position_and_tp_sl_reference(tmp_path):
    worker, sent = sweep_worker(tmp_path)
    def absent(*_): raise ValueError('No route')
    worker.chain.verify_pool = absent
    worker.sweep()
    assert sent == []
    assert worker.strategy.entry == D('1.2')
    assert worker.position()['amount'] == 200


def test_stop_during_sweep_quote_never_begins(tmp_path):
    worker, sent = sweep_worker(tmp_path)
    worker.chain.quote = lambda *_: worker.stop_event.set() or 190
    worker.sweep()
    assert sent == [] and worker.position()['amount'] == 200


def test_ambiguous_sweep_stops_without_removing_position(tmp_path):
    worker, sent = sweep_worker(tmp_path)
    def ambiguous(*_, **kwargs): raise UncertainTransaction('Unknown receipt')
    worker.live.swap = ambiguous
    with pytest.raises(UncertainTransaction): worker.sweep()
    assert len(sent) == 1
    assert Store(worker.store.path).data['positions']


def test_restart_does_not_allow_buy_in_other_pool(tmp_path):
    worker = setup_worker(tmp_path)
    worker.store = Store(worker.store.path)
    worker.pool = Pool(address('0x'+'78'*20), 'V2', address(USDT), address(WBNB), 18, 18, True)
    worker.configure = lambda _: None
    worker.open_position = lambda: pytest.fail('Would orphan a recovered position')
    with pytest.raises(ValueError, match='сохранённая позиция'):
        worker.command('buy', {})


def test_add_profile_uses_whole_path_quotes(tmp_path, monkeypatch):
    from dipbot.execution.trader import LiveTrader
    worker = Worker(Store(tmp_path/'state.json'))
    worker.pool = Pool(POOL.address, 'V2', POOL.token, address('0x'+'ab'*20), 18, 18, True)
    calls = []
    monkeypatch.setattr(LiveTrader, 'conversion_route', lambda self, src, dest, amount: [Pool(POOL.address, 'V2', address(dest), address(src), 18, 18, True)])
    worker.chain = SimpleNamespace(verify_pool=lambda *_: worker.pool,
                                  quote_route=lambda route, amount, **kwargs: calls.append(amount) or amount*99//100)
    worker.command('add_profile', {})
    assert calls == [10**15, 99*10**13]
    assert worker.pool.quote in Store(worker.store.path).data['dynamic_profiles'].values()


def test_sweep_dust_is_attempted_once_and_leftover_reported(tmp_path):
    worker,sent=sweep_worker(tmp_path)
    worker.chain.balance=lambda token,owner:1 if token==POOL.token else 0
    attempts=[];reports=[]
    worker.live.swap=lambda *args,**kwargs:attempts.append(args[1])
    worker.event.connect(lambda name,data:reports.append(data) if name=='sweep_report' else None)
    worker.sweep()
    assert attempts==[1] and len(sent)==1
    assert reports[0]['remaining'][POOL.token]==1


def test_sweep_preflight_failure_is_not_retried(tmp_path):
    worker,sent=sweep_worker(tmp_path)
    reads=[];reports=[]
    def quote(*args):
        reads.append(args)
        raise TimeoutError('synthetic timeout')
    worker.chain.quote=quote
    worker.event.connect(lambda name,data:reports.append(data) if name=='sweep_report' else None)
    worker.sweep()
    assert len(reads)==1 and not sent
    assert POOL.token in reports[0]['failed'] and reports[0]['remaining'][POOL.token]==200
    assert worker.position()['amount']==200
