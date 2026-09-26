"""Offline scenarios: no wallet, signing, or network."""
from types import SimpleNamespace
import pytest
from dipbot.worker import Worker
from dipbot.storage import Store
from dipbot.strategy import D, Settings, Strategy
from dipbot.trader import UncertainTransaction
from test_autopair_dynamic import POOL
from test_worker import config


@pytest.mark.parametrize('code', [-32005, -32016, -32602])
def test_preflight_rpc_failure_never_executes_or_reuses_old_signal(tmp_path, monkeypatch, code):
    from web3.exceptions import Web3RPCError
    clock = [10.0]
    monkeypatch.setattr('dipbot.worker.time.monotonic', lambda: clock[0])
    w = Worker(Store(tmp_path/'state.json'))
    w.mode = 'PAPER'; w.pool = POOL; w.running = True
    calls = []
    def screen(*args):
        calls.append('screen')
        raise Web3RPCError('private provider message', rpc_response={'error': {'code': code}})
    w.chain = SimpleNamespace(entry_quote=screen,
        quote=lambda *args: pytest.fail('Must not execute after failed preflight'))
    price = [D(100)]
    def read():
        w.current_price = price[0]
        return price[0]
    w.read_price = read
    w.observe()
    clock[0] += .1; price[0] = D(89)
    if code == -32602:
        with pytest.raises(Web3RPCError):
            w.observe()
    else:
        w.observe()
        assert w.entry_notice and w.running
        clock[0] += 1
        w.observe()
        clock[0] += 5
        w.observe()
        assert not w.entry_notice and w.strategy.base == 89
    assert calls == ['screen']
    assert not w.paper.position and not w.store.data.get('operation')


def test_rejected_dip_cools_down_reads_prices_and_requires_new_signal(tmp_path, monkeypatch):
    clock = [10.0]
    monkeypatch.setattr('dipbot.worker.time.monotonic', lambda: clock[0])
    w = Worker(Store(tmp_path/'state.json'))
    w.strategy = Strategy(Settings(dip=D(3), take_profit=D(2), stop_loss=D(2)))
    w.mode = 'PAPER'; w.pool = POOL; w.running = True
    quotes = []
    output = [0]
    def quote(p, amount, buy):
        quotes.append((amount, buy))
        return output[0]
    w.chain = SimpleNamespace(quote=quote)
    price = [D(100)]
    def read():
        w.current_price = price[0]
        return price[0]
    w.read_price = read
    w.observe()
    clock[0] += .1; price[0] = D(96)
    w.observe()
    assert w.running and w.entry_notice and not w.paper.position
    assert len(quotes) == 1
    for _ in range(40):
        clock[0] += .1; price[0] -= 1
        w.observe()
    assert len(quotes) == 1 and w.current_price == 56
    clock[0] = 15.2
    w.observe()
    assert not w.entry_notice and w.strategy.base == 56
    assert len(quotes) == 1  # Expiry alone cannot buy.
    output[0] = 10**18
    clock[0] += .1; price[0] = D(50)
    w.observe()
    assert len(quotes) == 2 and w.paper.position == 1 and w.running


@pytest.mark.parametrize('recovered,reason,running', [('97', 'STOP_LOSS', False), ('103', 'TAKE_PROFIT', True)])
def test_open_position_exits_after_rpc_recovers(tmp_path, recovered, reason, running):
    w = Worker(Store(tmp_path/'state.json')); w.running = True
    w.strategy = Strategy(Settings(take_profit=D(2), stop_loss=D(2)))
    w.paper.buy_quoted(D(100), D(1)); w.strategy.bought(D(100))
    logs = []; w.log.connect(logs.append)
    def outage():
        raise TimeoutError()
    w.read_price = outage
    w.observe()
    assert w.running and w.quote_unavailable and w.paper.position == 1
    w.read_price = lambda: D(recovered)
    w.observe()
    assert not w.paper.position and not w.quote_unavailable and w.running == running
    assert any(reason in msg for msg in logs)


def test_start_preserves_open_paper_position_then_stop_closes(tmp_path):
    w = Worker(Store(tmp_path/'state.json'))
    w.command('buy', config())
    amount, entry = w.paper.position, w.strategy.entry
    w.command('start', config())
    assert w.paper.position == amount and w.strategy.entry == entry
    w.stop_event.set()
    original = w.status
    def done():
        original()
        w.quit_event.set()
    w.status = done
    w.run()
    assert not w.running and not w.paper.position and w.strategy.stopped
    w.quit_event.clear()
    w.command('start', config())
    assert w.running and not w.strategy.stopped and w.strategy.entry is None


def test_uncertain_execution_halts_loop_and_blocks_restart_observation(tmp_path):
    w = Worker(Store(tmp_path/'state.json')); w.mode = 'LIVE'; w.running = True
    w.strategy.base = D(100)
    w.read_price = lambda: D(90)
    calls = []
    def uncertain():
        calls.append('send')
        w.store.data['operation'] = {'description': 'BUY pending'}
        w.store.save()
        raise UncertainTransaction('Статус неизвестен; нужна сверка')
    w.open_position = uncertain
    def done():
        w.quit_event.set()
    w.status = done
    w.run()
    assert not w.running and 'неизвестен' in w.halt_reason
    assert Store(w.store.path).data['operation']
    with pytest.raises(UncertainTransaction):
        w.observe()
    assert calls == ['send']


def test_successful_sale_clears_prior_stop_error_only_after_execution(tmp_path):
    w = Worker(Store(tmp_path/'state.json'))
    w.paper.buy_quoted(D(1), D(1)); w.strategy.bought(D(1))
    w.halt_reason = 'Previous STOP failure'
    def offline():
        raise TimeoutError()
    w.read_price = offline
    with pytest.raises(TimeoutError):
        w.close_position('MANUAL')
    assert w.halt_reason and w.paper.position == 1
    w.read_price = lambda: D(1)
    w.close_position('MANUAL')
    assert not w.halt_reason and not w.paper.position
