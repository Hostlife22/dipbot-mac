from dataclasses import asdict
from types import SimpleNamespace
import pytest
from test_app_autopair_flow import window
from test_autopair_dynamic import POOL
from dipbot.ui.window import Window
from dipbot.persistence.storage import Store
from dipbot.persistence.vault import Vault
from dipbot.application.worker import Worker
from dipbot.execution.errors import UncertainTransaction
from web3.exceptions import TransactionNotFound
from web3 import Web3

OWNER = '0x'+'34'*20
HASH = '0x'+'56'*32


def seed(store):
    store.data['positions'] = {OWNER+':'+POOL.address.lower():
        {'amount':10**18, 'entry':'1', 'pool':asdict(POOL)}}
    store.data['operation'] = {'wallet':OWNER,'transactions':[{'hash':HASH,'status':'pending'}]}
    store.save()


def test_startup_shows_unverified_saved_position_without_autostart(window):
    seed(window.store)
    w = Window(Store(window.store.path))
    try:
        assert not w.recovery_notice.isHidden()
        assert OWNER in w.recovery_details.text() and HASH in w.recovery_details.text()
        assert 'TARGET 1' in w.saved_positions.itemText(0)
        assert not w.running and w.mode.currentText() == 'DEMO'
        w.recovery_notice.click()
        assert w.tabs.currentIndex() == 1
        w.prepare_saved_position()
        assert w.token.text() == POOL.token and w.pool_input.text() == POOL.address
        assert w.mode.currentText() == 'LIVE' and not w.selection_ready
        assert not w.start.isEnabled() and w.worker.chain is None
        assert not w.autopair_timer.isActive()
    finally:
        w.autopair_timer.stop(); w.deleteLater()


@pytest.mark.parametrize('missing', [False, True])
def test_receipt_review_requires_no_key_and_preserves_lock(tmp_path, monkeypatch, missing):
    store=Store(tmp_path/'state.json'); seed(store)
    w=Worker(store)
    monkeypatch.setattr(Vault,'get',lambda *a:pytest.fail('Must not access private key'))
    def receipt(tx):
        if missing: raise TransactionNotFound('synthetic pending')
        return {'transactionHash':Web3.to_bytes(hexstr=HASH),'status':1,'blockNumber':7}
    w.chain=SimpleNamespace(check=lambda:7,w3=SimpleNamespace(eth=SimpleNamespace(get_transaction_receipt=receipt)))
    if missing:
        with pytest.raises(UncertainTransaction):w.command('reconcile',{})
    else:
        w.command('reconcile',{})
        assert store.data['operation']['transactions'][0]['status']=='confirmed'
    assert Store(store.path).data['operation'] and store.data['positions']


def test_keychain_load_custom_empty_backup_and_missing_defaults(window,monkeypatch):
    primary,backup=window.rpc.text(),window.backup_rpc.text()
    monkeypatch.setattr(Vault,'get',lambda *a:None)
    window.load_rpc()
    assert (window.rpc.text(),window.backup_rpc.text())==(primary,backup)
    monkeypatch.setattr(Vault,'get',lambda self,name:{'rpc':'https://example.invalid/test','backup_rpc':''}.get(name))
    window.load_rpc()
    assert window.rpc.text()=='https://example.invalid/test' and not window.backup_rpc.text()
    assert not window.running and window.worker.chain is None
    assert 'example.invalid' not in window.store.path.read_text() if window.store.path.exists() else True
