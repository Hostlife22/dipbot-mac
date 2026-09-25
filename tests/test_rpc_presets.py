from test_app_autopair_flow import window
from dipbot.rpc_presets import MAIN, BACKUP


def test_public_defaults_require_explicit_connect(window):
    assert window.rpc.text() == MAIN[0][1]
    assert window.backup_rpc.text() == BACKUP[0][1]
    assert window.worker.chain is None


def test_custom_url_is_not_overwritten_by_selector_sync(window):
    custom='https://example.invalid/private-test-token'
    window.rpc.setText(custom)
    assert window.rpc_preset.currentData() is None
    assert window.rpc.text()==custom
    window.rpc_preset.setCurrentIndex(1)
    assert window.rpc.text()==MAIN[1][1]


def test_backup_can_be_disabled(window):
    window.backup_rpc_preset.setCurrentIndex(2)
    assert window.backup_rpc.text()==''
