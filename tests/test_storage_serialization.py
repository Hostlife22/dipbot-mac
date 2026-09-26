"""Persist exact journal values and retain the old file on serialization failure."""
import pytest
from dipbot.persistence.storage import Store


def test_roundtrip_legacy_json_and_large_raw_values(tmp_path):
    path = tmp_path / 'state.json'
    path.write_text('{\n  "label": "База",\n  "old": true\n}', encoding='utf-8')
    store = Store(path)
    store.data['operation'] = {'amount': 2**256-1, 'pending': None, 'price': '0.000000000000000123'}
    store.save()
    assert Store(path).data == store.data


def test_nonserializable_state_preserves_last_durable_journal(tmp_path):
    store = Store(tmp_path / 'state.json')
    store.data = {'operation': {'status': 'pending'}}
    store.save()
    before = store.path.read_bytes()
    store.data['invalid'] = object()
    with pytest.raises(TypeError):
        store.save()
    assert store.path.read_bytes() == before
    assert not list(tmp_path.glob('.state-*'))
