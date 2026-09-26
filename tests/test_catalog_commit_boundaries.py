"""Real filesystem failures, synthetic public profiles; no RPC or credentials."""
from copy import deepcopy
import os
import stat

import pytest
from dipbot.persistence import dynamic
from dipbot.persistence import preferences
from dipbot.persistence.storage import Store, SaveAfterReplaceError
from test_autopair_dynamic import POOL, route


@pytest.mark.parametrize('action', ['catalog', 'add', 'remove'])
def test_invalid_registry_is_not_silently_replaced_with_empty_state(tmp_path, action):
    store = Store(tmp_path/'state.json')
    record = dynamic.upsert(store, POOL, route(), 100, symbol='SYNTH')
    store.data['dynamic_registry']['version'] = 999
    store.save()
    disk_before = store.path.read_bytes()
    loaded = Store(store.path)
    memory_before = deepcopy(loaded.data)
    with pytest.raises(ValueError, match='реестр'):
        if action == 'catalog': dynamic.catalog(loaded, 'V2')
        elif action == 'add': dynamic.upsert(loaded, POOL, route(), 100)
        else: dynamic.remove(loaded, record['name'])
    assert loaded.data == memory_before
    assert loaded.path.read_bytes() == disk_before


@pytest.mark.parametrize('action', ['add', 'remove', 'preferences'])
@pytest.mark.parametrize('boundary', ['file_sync', 'replace', 'dir_sync', 'dir_open'])
def test_public_state_matches_visible_file_after_write_failure(tmp_path, monkeypatch, action, boundary):
    store=Store(tmp_path/'state.json')
    record=dynamic.upsert(store,POOL,route(),100,symbol='SYNTH')
    before=deepcopy(store.data)
    original_sync,original_open=os.fsync,os.open
    def sync(fd):
        directory=stat.S_ISDIR(os.fstat(fd).st_mode)
        if boundary==('dir_sync' if directory else 'file_sync'):
            raise OSError('synthetic sync failure')
        return original_sync(fd)
    def open_file(path,flags,*args,**kwargs):
        if boundary=='dir_open' and path==store.path.parent:
            raise OSError('synthetic directory open failure')
        return original_open(path,flags,*args,**kwargs)
    def replace(*args):raise OSError('synthetic replace failure')
    monkeypatch.setattr(os,'fsync',sync)
    monkeypatch.setattr(os,'open',open_file)
    if boundary=='replace':monkeypatch.setattr(os,'replace',replace)
    def mutate():
        if action=='add':dynamic.upsert(store,POOL,route(),125,symbol='SYNTH')
        elif action=='remove':dynamic.remove(store,record['name'])
        else:preferences.save(store,preferences.from_windows_ui({'trade':{'amount_wbnb':'0.123'}}))
    after_replace=boundary in ('dir_sync','dir_open')
    with pytest.raises(SaveAfterReplaceError if after_replace else OSError):mutate()
    restored=Store(store.path)
    assert store.data==restored.data
    assert (store.data!=before)==after_replace
    assert dynamic.catalog(store,'V3')==dynamic.catalog(restored,'V3')
    assert not list(tmp_path.glob('.state-*'))


def test_durability_message_does_not_expose_underlying_error():
    from dipbot.application.worker import safe_error
    message=safe_error(SaveAfterReplaceError('https://synthetic.invalid/private-credential'))
    assert 'Файл заменён' in message
    assert 'synthetic' not in message and 'credential' not in message
