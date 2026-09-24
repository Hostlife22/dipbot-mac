"""Independent safety invariants; every wallet/state/chain here is synthetic."""
from dataclasses import asdict
from types import SimpleNamespace
import pytest
from dipbot.chain import Pool, WBNB, USDT, address
from dipbot.storage import Store
from dipbot.strategy import D
from dipbot.worker import Worker
from dipbot.trader import UncertainTransaction

OWNER = address('0x' + '34'*20)
POOL = Pool(address('0x'+'12'*20), 'V2', address('0x'+'56'*20), address(WBNB), 18, 18, True)


def setup_worker(tmp_path):
    worker = Worker(Store(tmp_path/'state.json'))
    worker.mode = 'LIVE'
    worker.pool = POOL
    worker.live = SimpleNamespace(owner=OWNER)
    worker.set_position(200, D('1.2'))
    worker.strategy.bought(D('1.2'))
    return worker


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


def sweep_worker(tmp_path):
    worker = setup_worker(tmp_path)
    balances = {POOL.token: 200}
    sent = []
    worker.chain = SimpleNamespace(balance=lambda token, owner: balances.get(token, 0),
        verify_pool=lambda *_: POOL, quote=lambda *_: 190)
    worker.live.begin = lambda description: sent.append(description)
    worker.live.finish = lambda: worker.store.save()
    worker.live.swap = lambda *args: balances.update({POOL.token: 0})
    worker.command = lambda *args: None  # Final balance UI report only.
    return worker, sent


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
    def ambiguous(*_): raise UncertainTransaction('Unknown receipt')
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
    from dipbot.trader import LiveTrader
    worker = Worker(Store(tmp_path/'state.json'))
    worker.pool = Pool(POOL.address, 'V2', POOL.token, address('0x'+'ab'*20), 18, 18, True)
    calls = []
    monkeypatch.setattr(LiveTrader, 'conversion_route', lambda self, *args: ['synthetic path'])
    worker.chain = SimpleNamespace(quote_route=lambda route, amount: calls.append(amount) or amount*99//100)
    worker.command('add_profile', {})
    assert calls == [10**16, 99*10**14]
    assert worker.pool.quote in Store(worker.store.path).data['dynamic_profiles'].values()
