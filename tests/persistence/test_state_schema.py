import json
import pytest
from dipbot.persistence.schema import load_state
from dipbot.persistence.storage import Store


def test_legacy_migration_preserves_unfinished_operation_without_writing(tmp_path):
    legacy = {'operation': {'wallet': 'synthetic', 'transactions': [{'hash': 'pending', 'status': 'broadcast'}]},
              'positions': {'synthetic': {'amount': 7}}, 'custom_extension': {'value': 3}}
    path = tmp_path / 'state.json'
    path.write_text(json.dumps(legacy))
    original = path.read_bytes()
    store = Store(path)
    assert path.read_bytes() == original
    assert store.data == {**legacy, 'state_version': 1}
    store.save()
    assert Store(path).data == store.data
    assert store.data['operation'] == legacy['operation']


@pytest.mark.parametrize('version', [True, -1, 2, '1', None])
def test_unknown_version_is_never_overwritten(tmp_path, version):
    path = tmp_path / 'state.json'
    path.write_text(json.dumps({'state_version': version, 'operation': {'pending': True}}))
    before = path.read_bytes()
    with pytest.raises(ValueError):
        Store(path)
    assert path.read_bytes() == before


@pytest.mark.parametrize('field,value', [('positions', []), ('operation', 'pending'), ('history', {}), ('gas_ledger', [])])
def test_corrupt_containers_fail_before_write(tmp_path, field, value):
    store = Store(tmp_path / 'state.json')
    store.save()
    before = store.path.read_bytes()
    store.data[field] = value
    with pytest.raises(ValueError):
        store.save()
    assert store.path.read_bytes() == before


def test_migration_is_idempotent_and_does_not_mutate_input():
    old = {'positions': {}}
    migrated = load_state(old)
    assert old == {'positions': {}}
    assert load_state(migrated) == migrated
