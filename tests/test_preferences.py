import pytest
from dipbot import preferences
from dipbot.storage import Store


def values():
    return {'version': 1, 'settings': {'amount': '0.03', 'dip': '4', 'take_profit': '6',
            'stop_loss': '7', 'slippage': '2', 'dynamic': '150'}, 'gas': '0.1', 'interval': '0.103'}


def test_settings_restart_allowlist_excludes_credentials_and_live_mode(tmp_path):
    store = Store(tmp_path/'state.json')
    value = values() | {'rpc': 'synthetic secret', 'private_key': 'synthetic secret', 'mode': 'LIVE'}
    preferences.save(store, value)
    assert Store(store.path).data['ui_preferences'] == values()
    assert 'secret' not in store.path.read_text()


@pytest.mark.parametrize('change', [{'gas': 'NaN'}, {'interval': 'Infinity'}, {'version': 2},
                                    {'settings': {}}, {'gas': '-1'}, {'interval': '0.6'}])
def test_invalid_settings_do_not_replace_previous(tmp_path, change):
    store = Store(tmp_path/'state.json')
    preferences.save(store, values())
    with pytest.raises(ValueError): preferences.save(store, values() | change)
    assert Store(store.path).data['ui_preferences'] == values()


def test_failed_preference_save_preserves_journal_and_memory(tmp_path):
    store = Store(tmp_path/'state.json')
    store.data['operation'] = {'description': 'synthetic pending'}
    preferences.save(store, values())
    def fail(): raise OSError('disk full')
    store.save = fail
    with pytest.raises(OSError): preferences.save(store, values() | {'gas': '2'})
    assert store.data == Store(store.path).data
