"""Mac safety comparisons prompted by registry pop/save and settings read paths."""
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace
import pytest
from dipbot import dynamic, preferences
from dipbot.storage import Store
from dipbot.worker import Worker
from tools.protected_format import decode, ProtectedFormatError
from test_autopair_dynamic import POOL, route
from test_phase3 import OWNER


def test_remove_one_router_keeps_other_registration_after_restart(tmp_path):
    store=Store(tmp_path/'state.json')
    first=dynamic.upsert(store,POOL,route(),100,symbol='SYNTH')
    second=dynamic.upsert(store,replace(POOL,router='V2',fee=0),route(),100,symbol='SYNTH')
    worker=Worker(store);worker.chain=SimpleNamespace(balance=lambda *_:0)
    worker.command('remove_profile',{'symbol':first['name'],'wallet':OWNER})
    restored=Store(store.path)
    assert list(dynamic.records(restored).values())==[second]
    assert second['name'] in dynamic.catalog(restored,'V2')
    assert first['name'] not in dynamic.catalog(restored,'V3')


def test_registry_save_failure_preserves_both_router_records(tmp_path):
    store=Store(tmp_path/'state.json')
    first=dynamic.upsert(store,POOL,route(),100,symbol='SYNTH')
    dynamic.upsert(store,replace(POOL,router='V2',fee=0),route(),100,symbol='SYNTH')
    before=deepcopy(store.data)
    def fail():raise OSError('synthetic write failure')
    store.save=fail
    with pytest.raises(OSError):dynamic.remove(store,first['name'])
    assert store.data==Store(store.path).data==before


@pytest.mark.parametrize('plain',[b'[]',b'null',b'{',b'"legacy text"'])
def test_protected_non_object_payload_is_rejected_without_leaking_data(plain):
    envelope=b'{"format":"NRNF-DPAPI","version":1,"payload":"c3ludGhldGlj"}'
    with pytest.raises(ProtectedFormatError,match='damaged or unavailable'):
        decode(envelope,'synthetic',lambda *_:plain)


def test_public_settings_import_does_not_mutate_legacy_payload():
    payload={'trade_router':'v2','pair':'SYNTH','trade':{'amount_wbnb':'0.02'},
             'pair_amounts':{'V2:SYNTH':'0.04'}}
    before=deepcopy(payload)
    normalized=preferences.from_windows_ui(payload)
    normalized['pair_amounts']['V2:SYNTH']='0.05'
    assert payload==before and normalized['settings']['amount']=='0.04'
