from types import SimpleNamespace as NS
from pathlib import Path
import json
import subprocess
import threading
import pytest
from dipbot import diagnostics


def test_macos_native_count_does_not_retain_command(monkeypatch):
    monkeypatch.setattr(diagnostics.sys,'platform','darwin')
    monkeypatch.setattr(diagnostics.os,'getpid',lambda:123)
    def run(args,**kw):
        assert args==['ps','-M','-p','123'] and kw['timeout']==1
        return NS(returncode=0,stdout='USER PID COMMAND\nadmin 123 SECRET\n      123 0.0 S\nadmin 999 FOREIGN\n')
    monkeypatch.setattr(diagnostics.subprocess,'run',run)
    result=diagnostics.resource_snapshot()
    assert result['native_threads']==2 and 'SECRET' not in str(result)


def test_native_failure_stays_unknown(monkeypatch):
    monkeypatch.setattr(diagnostics.sys,'platform','darwin')
    def run(*a,**kw):raise subprocess.TimeoutExpired('ps',1)
    monkeypatch.setattr(diagnostics.subprocess,'run',run)
    assert diagnostics.native_thread_count() is None


def test_long_checkpoint_gap_is_diagnostic_not_sleep_proof(tmp_path,monkeypatch):
    d=object.__new__(diagnostics.Diagnostics)
    d.directory=tmp_path;d.last_checkpoint=(100,100)
    events=[];d.write=events.append
    monkeypatch.setattr(diagnostics.time,'time',lambda:500)
    monkeypatch.setattr(diagnostics.time,'monotonic',lambda:130)
    monkeypatch.setattr(diagnostics,'resource_snapshot',lambda:{'native_threads':7})
    d.checkpoint()
    result=json.loads((tmp_path/'timings.json').read_text())
    assert result['possible_suspend_or_scheduler_pause']
    assert result['checkpoint_wall_gap_seconds']==400
    assert result['checkpoint_monotonic_gap_seconds']==30
    assert events[0]['event']=='checkpoint_gap'


@pytest.mark.skipif(diagnostics.sys.platform!='darwin',reason='native macOS QThread')
def test_real_qthread_counted_beyond_python_threads():
    from PySide6.QtCore import QThread
    ready=threading.Event();stop=threading.Event()
    class Thread(QThread):
        def run(self):ready.set();stop.wait(5)
    before=diagnostics.native_thread_count()
    thread=Thread();thread.start()
    try:
        assert ready.wait(2)
        after=diagnostics.native_thread_count()
        assert before is not None and after is not None and after>=before+1
    finally:
        stop.set();assert thread.wait(3000)
