"""SIGKILL and real atomic-write fault boundaries, all transport is fake."""
import os
import select
import signal
import subprocess
import sys

import pytest
from dipbot.storage import Store
from dipbot.trader import LiveTrader, UncertainTransaction
from test_process_recovery import SCRIPT
from test_execution import trader, Function


@pytest.mark.skipif(sys.platform == 'win32', reason='POSIX SIGKILL boundaries')
@pytest.mark.parametrize('boundary,records', [('file_sync', 0), ('replace', 0), ('dir_sync', 1)])
def test_sigkill_during_hash_commit_never_broadcasts(tmp_path, boundary, records):
    injection = r'''
import stat, signal
original_sync, original_replace = os.fsync, os.replace
def pause():
 print('BOUNDARY', flush=True)
 signal.pause()
def sync(fd):
 if (boundary=='dir_sync') == stat.S_ISDIR(os.fstat(fd).st_mode) and boundary!='replace':pause()
 return original_sync(fd)
def replace(src,dst):
 if boundary=='replace':pause()
 return original_replace(src,dst)
os.fsync=sync;os.replace=replace
t.chain.w3.eth.send_raw_transaction=lambda raw: (_ for _ in ()).throw(AssertionError('broadcast before boundary'))
'''
    script = SCRIPT.replace("if boundary=='intent':os._exit(70)", injection)
    path = tmp_path/'state.json'
    proc = subprocess.Popen([sys.executable, '-c', script, str(path), boundary],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        ready, _, _ = select.select([proc.stdout], [], [], 20)
        assert ready and proc.stdout.readline() == b'BOUNDARY\n'
        proc.kill()
        _, stderr = proc.communicate(timeout=10)
        assert proc.returncode == -signal.SIGKILL, stderr.decode()
    finally:
        if proc.poll() is None:
            proc.kill(); proc.communicate()
    store = Store(path)
    assert len(store.data['operation']['transactions']) == records
    recovered = object.__new__(LiveTrader); recovered.store = store
    with pytest.raises(UncertainTransaction): recovered.begin('duplicate')


@pytest.mark.parametrize('failure', ['file_sync', 'replace', 'dir_sync'])
def test_atomic_write_error_blocks_broadcast_and_restart(trader, monkeypatch, failure):
    import stat
    trader.begin('disk fault')
    original_sync = os.fsync
    def sync(fd):
        is_dir = stat.S_ISDIR(os.fstat(fd).st_mode)
        if failure == ('dir_sync' if is_dir else 'file_sync'):
            raise OSError('synthetic disk fault')
        return original_sync(fd)
    def replace(*args): raise OSError('synthetic replace fault')
    monkeypatch.setattr(os, 'fsync', sync)
    if failure == 'replace': monkeypatch.setattr(os, 'replace', replace)
    trader.chain.w3.eth.send_raw_transaction = lambda _: pytest.fail('must not broadcast')
    with pytest.raises(OSError): trader.send(Function(), 'disk')
    assert not list(trader.store.path.parent.glob('.state-*'))
    trader.store = Store(trader.store.path)
    assert len(trader.store.data['operation']['transactions']) == (1 if failure == 'dir_sync' else 0)
    with pytest.raises(UncertainTransaction): trader.begin('duplicate')


@pytest.mark.parametrize('status', [0, 1])
def test_restart_reconcile_after_lost_response_never_resends(trader, status):
    trader.begin('lost response')
    trader.chain.w3.eth.fail_send = True
    with pytest.raises(UncertainTransaction): trader.send(Function(), 'send')
    store = Store(trader.store.path)
    recovered = object.__new__(LiveTrader)
    recovered.store, recovered.owner, recovered.chain = store, trader.owner, trader.chain
    recovered.chain.w3.eth.send_raw_transaction = lambda _: pytest.fail('reconciliation must not send')
    def disconnected(*_): raise ConnectionError('synthetic connection loss')
    recovered.chain.w3.eth.get_transaction_receipt = disconnected
    with pytest.raises(ConnectionError): recovered.reconcile()
    assert Store(store.path).data['operation']['transactions'][0]['status'] == 'pending'
    recovered.chain.w3.eth.get_transaction_receipt = lambda h: {'status':status, 'blockNumber':123, 'transactionHash':h}
    recovered.reconcile()
    assert Store(store.path).data['operation']['transactions'][0]['status'] == ('confirmed' if status else 'reverted')
    with pytest.raises(UncertainTransaction): recovered.begin('still needs balance review')
