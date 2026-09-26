from decimal import Decimal as D
from types import SimpleNamespace as NS

import pytest

from dipbot.market.rpc_health import RpcHealth
from dipbot.persistence.storage import Store
from dipbot.application.worker import Worker
from test_autopair_dynamic import POOL


def test_switch_requires_three_samples_and_meaningful_advantage():
    h = RpcHealth()
    assert h.choose(0) == 0
    for i in range(3):
        h.success(0, .2, i)
    h.success(1, .1, 1)
    h.success(1, .1, 2)
    assert h.preferred == 0
    h.success(1, .1, 3)
    assert h.preferred == 1
    # A single extreme measurement does not reverse the median.
    h.success(0, .001, 40)
    assert h.preferred == 1
    assert all('url' not in row for row in h.report())


def test_circuit_breaker_bounds_retries_and_history():
    h = RpcHealth()
    h.failure(0, 0)
    assert h.choose(1) == 1
    h.failure(1, 1)
    with pytest.raises(TimeoutError):
        h.choose(2)
    for i in range(100):
        h.success(0, .1, 40+i)
    assert len(h.samples[0]) == 32
    assert h.failures[0] == 0


def test_adaptive_source_rejects_regression_without_rebinding_executor(tmp_path):
    w = Worker(Store(tmp_path/'state.json'))
    w.adaptive_rpc = True
    w.pool = POOL
    primary = NS(price=lambda p:D(2), price_block={'number':11,'hash':b'b'})
    backup = NS(check=lambda:10, verify_pool=lambda *args:POOL,
                price=lambda p:D(1),price_block={'number':10,'hash':b'a'})
    w.chain, w.backup_chain = primary, backup
    assert w.market_price() == 2
    # First alternative probe is behind; retry the primary.
    assert w.market_price() == 2
    assert w.chain is primary
    assert w.market_source == 'BSC'
    assert w.rpc_health.failures[1] == 1


def test_adaptive_primary_failure_uses_backup(tmp_path):
    w = Worker(Store(tmp_path/'state.json'))
    w.adaptive_rpc = True
    w.pool = POOL
    def fail(*args):
        raise TimeoutError()
    w.chain = NS(price=fail)
    w.backup_chain = NS(check=lambda:12, verify_pool=lambda *args:POOL,
                       price=lambda p:D(3), price_block={'number':12,'hash':b'c'})
    assert w.market_price() == 3
    assert w.rpc_health.failures[0] == 1
    assert 'резервный' in w.market_source


@pytest.mark.parametrize('adaptive', [False, True])
@pytest.mark.parametrize('code', [-32005, -32016, -32602])
def test_market_rpc_codes_only_fail_over_for_transient_reads(tmp_path, adaptive, code):
    from web3.exceptions import Web3RPCError
    w = Worker(Store(tmp_path/'state.json'))
    w.adaptive_rpc = adaptive
    w.pool = POOL
    def fail(*args):
        raise Web3RPCError('provider error', rpc_response={'error': {'code': code}})
    w.chain = NS(price=fail)
    calls = []
    w.backup_chain = NS()
    def backup():
        calls.append('read')
        return D(3)
    w.backup_price = backup
    if code == -32602:
        with pytest.raises(Web3RPCError):
            w.market_price()
        assert not calls
    else:
        assert w.market_price() == 3
        assert calls == ['read']
    assert w.chain.price is fail
