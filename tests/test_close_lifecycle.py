"""Mac close sequencing with a controlled worker, real public store and no Qt loop."""
from types import SimpleNamespace as NS
import threading
import pytest
from dipbot.app import Window, QMessageBox
from dipbot import preferences
from dipbot.storage import Store


def window_fixture(tmp_path, monkeypatch, *, busy=False, running=False, stopped=False):
    store=Store(tmp_path/'state.json')
    store.data={'operation':{'description':'synthetic pending','transactions':[]}}
    store.save()
    state={'stopped':stopped};calls=[]
    def wait(ms):
        calls.append('wait')
        return state['stopped']
    normalized=preferences.from_windows_ui({})
    field=lambda value:NS(text=lambda:str(value))
    window=NS(busy=busy,running=running,store=store,exit_policy=lambda: {},signal_policy=lambda: {},sizing_policy=lambda: {},record_market=NS(isChecked=lambda:False),adaptive_rpc=NS(isChecked=lambda:False),
        usd=NS(set_token=lambda token:calls.append('usd_stop')),
        gas_usd=NS(set_token=lambda token:None),
        worker=NS(quit_event=threading.Event(),wait=wait),
        invalidate_discovery=lambda:calls.append('invalidate'),
        remember_amount=lambda:calls.append('remember'),
        router=NS(currentText=lambda:'V2'),quote=NS(currentText=lambda:'WBNB'),
        usd_pair_amounts={},pair_amounts=normalized['pair_amounts'],params={k:field(v) for k,v in normalized['settings'].items()},
        gas=field('0.1'),interval=NS(value=lambda:0.1))
    event=NS(ignore=lambda:calls.append('ignore'),accept=lambda:calls.append('accept'))
    monkeypatch.setattr(QMessageBox,'information',lambda *a:calls.append('message'))
    monkeypatch.setattr(QMessageBox,'warning',lambda *a:calls.append('warning'))
    original=store.save
    def save():
        assert state['stopped'], 'preferences must not race worker journal'
        calls.append('save');original()
    store.save=save
    return window,event,state,calls


@pytest.mark.parametrize('busy,running',[(True,False),(False,True),(True,True)])
def test_close_refuses_active_operations_before_quitting_worker(tmp_path,monkeypatch,busy,running):
    window,event,state,calls=window_fixture(tmp_path,monkeypatch,busy=busy,running=running)
    Window.closeEvent(window,event)
    assert calls==['message','ignore']
    assert not window.worker.quit_event.is_set()
    assert 'ui_preferences' not in Store(window.store.path).data


def test_close_wait_timeout_then_retry_preserves_journal(tmp_path,monkeypatch):
    window,event,state,calls=window_fixture(tmp_path,monkeypatch)
    Window.closeEvent(window,event)
    assert 'save' not in calls and calls[-1]=='ignore'
    assert window.worker.quit_event.is_set()
    state['stopped']=True
    Window.closeEvent(window,event)
    assert calls.index('save')>calls.index('wait') and calls[-1]=='accept'
    saved=Store(window.store.path).data
    assert saved['operation']['description']=='synthetic pending'
    assert saved['ui_preferences']['selection']=={'router':'V2','pair':'WBNB'}


@pytest.mark.parametrize('field,value', [('display_position', 1), ('stop_pending', True)])
def test_close_refuses_manual_position_or_pending_stop(tmp_path, monkeypatch, field, value):
    window, event, state, calls = window_fixture(tmp_path, monkeypatch)
    setattr(window, field, value)
    Window.closeEvent(window, event)
    assert calls == ['message', 'ignore']
    assert not window.worker.quit_event.is_set()
