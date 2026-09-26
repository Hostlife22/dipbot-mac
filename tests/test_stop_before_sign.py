from dipbot.application.messages import Command
import pytest
from types import SimpleNamespace as NS
from test_execution import trader,Function
from dipbot.domain.entry_guard import EntryRejected
from dipbot.execution.errors import UncertainTransaction
from dipbot.persistence.storage import Store


def test_stop_after_confirmed_approve_never_signs_buy(trader):
    trader.begin('BUY target')
    trader.send(Function(),'APPROVE EXACT AMOUNT')
    trader.stop_requested=lambda:True
    trader.account=NS(sign_transaction=lambda _:pytest.fail('must not sign BUY'))
    with pytest.raises(EntryRejected,match='STOP'):trader.send(Function(),'BUY')
    saved=Store(trader.store.path).data
    assert 'operation' not in saved
    assert saved['history'][-1]['outcome']=='stopped_before_buy'
    assert [r['label'] for r in saved['history'][-1]['transactions']]==['APPROVE EXACT AMOUNT']


def test_stop_during_gas_estimation_cancels_before_signature(trader):
    stop=[False]
    trader.begin('BUY target');trader.stop_requested=lambda:stop[0]
    class SlowFunction(Function):
        def estimate_gas(self,tx):stop[0]=True;return 21000
    trader.account=NS(sign_transaction=lambda _:pytest.fail('must not sign'))
    with pytest.raises(EntryRejected):trader.send(SlowFunction(),'BUY')
    assert not trader.store.data.get('operation')
    assert not trader.store.data['history'][-1]['transactions']


def test_stop_never_clears_pending_or_failed_save(trader):
    trader.begin('BUY target');trader.stop_requested=lambda:True
    trader.operation['transactions'].append({'status':'pending'})
    with pytest.raises(UncertainTransaction):trader.send(Function(),'BUY')
    assert trader.store.data['operation']
    trader.operation['transactions'].clear()
    def fail():raise OSError('disk failed')
    trader.store.save=fail
    with pytest.raises(OSError):trader.send(Function(),'BUY')
    assert trader.store.data['operation']


def test_stop_does_not_cancel_the_sell_needed_to_exit(trader):
    trader.begin('SELL target');trader.stop_requested=lambda:True
    trader.send(Function(),'SELL')
    assert trader.operation['transactions'][0]['status']=='confirmed'


def test_manual_entry_cancel_is_stop_not_error_dialog(tmp_path):
    import queue
    from dipbot.application.worker import Worker
    w=Worker(Store(tmp_path/'state.json'))
    events=[];w.event.connect(lambda name,value:events.append((name,value)))
    calls=[0]
    class Commands:
        def get(self,timeout):
            calls[0]+=1
            if calls[0]==1:return Command.from_wire('buy', {})
            w.quit_event.set();raise queue.Empty
        def get_nowait(self):raise queue.Empty
    def command(*args):
        w.stop_event.set();raise EntryRejected('STOP before fill')
    w.commands=Commands();w.command=command;w.status=lambda:None
    w.run_loop()
    assert not any(name=='error' for name,_ in events)
    assert not w.stop_event.is_set() and not w.running
