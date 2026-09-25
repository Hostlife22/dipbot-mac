import time
from decimal import Decimal as D
from dipbot.strategy import Settings, Strategy
from dipbot.signal_policy import SignalPolicy
from dipbot.exit_policy import ExitPolicy
from test_app_autopair_flow import window
from test_audit_ui_modes import status


def test_wait_explains_rebound_without_changing_signal():
    s = Strategy(Settings(), SignalPolicy(mode='window', rebound_pct=D('.1')),
                 ExitPolicy(cooldown_seconds=2))
    assert s.entry_wait(1)[0] == 'baseline'
    s.observe(D(100), 1)
    assert s.entry_wait(1)[0] == 'dip'
    assert s.observe(D(96), 1.1) is None
    assert s.entry_wait(1.1)[0] == 'rebound'
    assert s.trough == D(96)
    assert s.observe(D('96.1'), 1.2) == 'BUY'
    s.bought(D('96.1'), 1.2)
    assert s.entry_wait(1.2) == ('', '')
    s.sold(D(97), 'TAKE_PROFIT', 1.3)
    assert s.entry_wait(1.4)[0] == 'cooldown'


def test_stale_and_rpc_override_rebound_and_rejection(window):
    w = window
    w.last_quote_at = time.monotonic()
    status(w, running=True, wait_reason='rebound', signal_notice='Ждёт отскок')
    assert w.metrics['state'].text() == 'REBOUND'
    assert 'отскок' in w.strategy_status.text()
    status(w, running=True, wait_reason='rebound', signal_notice='Ждёт отскок',
           entry_notice='Недостаточная активность: 0 Swap, нужно минимум 1')
    assert 'Недостаточная активность' in w.strategy_status.text()
    w.last_quote_at = time.monotonic() - 2
    w.update_strategy_status()
    assert w.metrics['state'].text() == 'STALE'
    assert 'устарела' in w.strategy_status.text()
    status(w, running=True, quote_unavailable=True, signal_notice='Ждёт отскок')
    assert 'Нет котировок' in w.strategy_status.text()
