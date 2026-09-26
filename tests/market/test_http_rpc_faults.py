"""Real localhost HTTP transport faults; no upstream network or transaction signing."""

import json
import os
import socket
import sys
import threading
import time
from decimal import Decimal as D
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
import requests

ORIGINAL_REQUEST = requests.Session.request
from eth_abi import encode

from dipbot.application.worker import Worker
from dipbot.domain.assets import USDT, WBNB
from dipbot.market.chain import Chain, Pool, address
from dipbot.persistence.storage import Store


@pytest.fixture
def node(monkeypatch):
    state = {"fault": None, "chain_id": 56, "price": 100, "height": 100, "calls": []}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            method = request["method"]
            state["calls"].append(method)
            if method not in ("eth_chainId", "eth_getBlockByNumber", "eth_call", "eth_getCode"):
                self.send_response(405)
                self.end_headers()
                return
            fault = state["fault"]
            if fault in ("rpc_limit", "rpc_capacity"):
                payload = json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": request["id"],
                        "error": {
                            "code": -32005 if fault == "rpc_limit" else -32016,
                            "message": "request capacity exceeded",
                        },
                    }
                ).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
                return
            if fault in (429, 503):
                self.send_response(fault)
                self.end_headers()
                return
            if fault == "disconnect":
                self.connection.shutdown(socket.SHUT_RDWR)
                self.connection.close()
                return
            if fault == "timeout":
                time.sleep(0.12)
            if method == "eth_chainId":
                value = hex(state["chain_id"])
            elif method == "eth_getBlockByNumber":
                height = state["height"]
                value = {
                    "number": hex(height),
                    "hash": "0x" + format(height, "064x"),
                    "parentHash": "0x" + format(height - 1, "064x"),
                    "transactions": [],
                    "timestamp": hex(int(time.time()) - (120 if fault == "stale" else 0)),
                    "extraData": "0x",
                }
            elif method == "eth_getCode":
                value = "0x6000"
            else:
                value = (
                    "0x"
                    + encode(["uint112", "uint112", "uint32"], [10**18, state["price"] * 10**18, 0]).hex()
                )
            payload = json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": value}).encode()
            try:
                self.send_response(200)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
            except (BrokenPipeError, ConnectionResetError):
                pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    endpoint = f"http://127.0.0.1:{server.server_port}"

    def local_only(session, method, url, *args, **kwargs):
        if url != endpoint or method.upper() != "POST":
            raise AssertionError("Only this test loopback server is permitted")
        session.trust_env = False
        kwargs["allow_redirects"] = False
        return ORIGINAL_REQUEST(session, method, url, *args, **kwargs)

    monkeypatch.setattr(requests.Session, "request", local_only)
    try:
        yield state, endpoint
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)


def worker_at(tmp_path, endpoint):
    worker = Worker(Store(tmp_path / "state.json"))
    worker.mode = "PAPER"
    worker.running = True
    worker.chain = Chain(endpoint, request_timeout=0.05)
    worker.pool = Pool(address("0x" + "12" * 20), "V2", address(USDT), address(WBNB), 18, 18, True)
    return worker


@pytest.mark.parametrize("fault", [429, 503, "timeout", "disconnect", "stale", "rpc_limit", "rpc_capacity"])
def test_persistent_http_fault_and_recovery_cannot_buy_old_dip(tmp_path, node, fault):
    state, endpoint = node
    w = worker_at(tmp_path, endpoint)
    w.open_position = lambda: pytest.fail("No entry from the pre-outage baseline")
    w.observe()
    assert w.strategy.base == 100
    state["fault"] = fault
    for _ in range(12):
        w.observe()
    assert w.quote_unavailable and w.quote_failures == 12 and not w.paper.position
    # Model a long absence without waiting hours. HTTP failures above are real.
    w.strategy.last_time -= 3600
    state.update(fault=None, price=80, height=101)
    w.observe()
    assert not w.quote_unavailable and w.quote_failures == 0 and w.strategy.base == 80
    assert not any("send" in method.lower() for method in state["calls"])


def test_open_position_survives_disconnect_then_rechecks_exit(tmp_path, node):
    state, endpoint = node
    w = worker_at(tmp_path, endpoint)
    w.paper.buy_quoted(D(100), D(1))
    w.strategy.bought(D(100))
    exits = []
    w.close_position = exits.append
    state["fault"] = "disconnect"
    for _ in range(12):
        w.observe()
    assert w.paper.position == 1 and not exits
    state.update(fault=None, price=120, height=101)
    w.observe()
    assert exits == ["TAKE_PROFIT"] and not w.quote_unavailable


def test_network_identity_change_after_fault_blocks_recovery(tmp_path, node):
    state, endpoint = node
    w = worker_at(tmp_path, endpoint)
    w.observe()
    state["fault"] = 503
    w.observe()
    state.update(fault=None, chain_id=1)
    with pytest.raises(ValueError, match="не к BSC"):
        w.observe()
    assert not w.paper.position


def test_actual_worker_backoff_and_stop_during_rate_limit(tmp_path, node):
    state, endpoint = node
    w = worker_at(tmp_path, endpoint)
    state["fault"] = 429
    w.start()
    try:
        time.sleep(2.2)
        assert 1 <= len(state["calls"]) <= 4
        w.stop_event.set()
        deadline = time.monotonic() + 2
        while w.running and time.monotonic() < deadline:
            time.sleep(0.02)
        assert not w.running and not w.paper.position
    finally:
        w.quit_event.set()
        assert w.wait(5000)


@pytest.mark.parametrize("fault", [429, 503, "disconnect"])
def test_exit_read_recovers_over_real_http_without_resubmitting(tmp_path, node, fault):
    state, endpoint = node
    w = worker_at(tmp_path, endpoint)
    w.paper.buy_quoted(D(100), D(1))
    w.strategy.bought(D(100))
    state["fault"] = fault
    waits = []

    def recover(delay):
        waits.append(delay)
        state["fault"] = None
        return False

    w.stop_event.wait = recover
    assert w.exit_read(lambda source: source.price(w.pool)) == 100
    assert waits == [0.5] and w.paper.position == 1 and w.exit_retry is None
    assert not any("send" in method.lower() for method in state["calls"])


def test_exit_read_persistent_http_503_is_bounded(tmp_path, node):
    state, endpoint = node
    w = worker_at(tmp_path, endpoint)
    w.paper.buy_quoted(D(100), D(1))
    w.strategy.bought(D(100))
    state["fault"] = 503
    waits = []
    w.stop_event.wait = lambda delay: waits.append(delay)
    with pytest.raises(requests.exceptions.HTTPError):
        w.exit_read(lambda source: source.price(w.pool))
    assert waits == [0.5, 1.0, 2.0] and w.paper.position == 1 and w.exit_retry is None
    assert not any("send" in method.lower() for method in state["calls"])


@pytest.mark.parametrize("fault", [429, 503])
def test_threaded_prolonged_outage_recovery_and_stop(tmp_path, node, qt_application, fault):
    """Real HTTP failures and real QThread; optional extended duration for soak audits."""
    duration = float(os.environ.get("DIPBOT_OUTAGE_SECONDS", "0.7"))
    assert 0 < duration <= 900
    state, endpoint = node
    worker = worker_at(tmp_path, endpoint)
    entry_attempts = []
    worker.open_position = lambda: entry_attempts.append(True)
    worker.interval = 0.1

    def wait(predicate, timeout=5):
        until = time.monotonic() + timeout
        while not predicate():
            assert time.monotonic() < until
            qt_application.processEvents()
            time.sleep(0.01)

    worker.start()
    try:
        wait(lambda: worker.strategy.base == 100)
        state["fault"] = fault
        wait(lambda: worker.quote_unavailable)
        began = time.monotonic()
        while time.monotonic() - began < duration:
            qt_application.processEvents()
            assert worker.isRunning() and worker.running
            assert not worker.paper.position and not entry_attempts
            time.sleep(0.02)
        state.update(fault=None, price=80, height=101)
        wait(lambda: not worker.quote_unavailable and worker.current_price == 80)
        assert worker.strategy.base == 80 and not entry_attempts
        state["fault"] = fault
        wait(lambda: worker.quote_unavailable)
        stopped_at = time.monotonic()
        worker.stop_event.set()
        wait(lambda: not worker.running and not worker.stop_event.is_set())
        assert time.monotonic() - stopped_at < 2
        assert not worker.paper.position and not worker.store.data.get("operation")
        assert not any("send" in method.lower() for method in state["calls"])
    finally:
        worker.quit_event.set()
        assert worker.wait(5000)
        qt_application.processEvents()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX process suspension")
def test_process_pause_discards_pre_pause_dip_anchor(tmp_path, node):
    """SIGSTOP/SIGCONT exercises scheduler pause; this is not an OS sleep test."""
    import select
    import signal
    import subprocess
    import sys

    state, endpoint = node
    script = r"""
import json,sys,time
from pathlib import Path
from PySide6.QtCore import QCoreApplication
from dipbot.application.worker import Worker
from dipbot.persistence.storage import Store
from dipbot.market.chain import Chain,Pool,address
from dipbot.domain.assets import USDT,WBNB
app=QCoreApplication([])
w=Worker(Store(Path(sys.argv[1])))
w.mode='PAPER';w.running=True;w.interval=.1
w.chain=Chain(sys.argv[2],request_timeout=.5)
w.pool=Pool(address('0x'+'12'*20),'V2',address(USDT),address(WBNB),18,18,True)
entries=[];w.open_position=lambda:entries.append(True)
w.start()
try:
 until=time.monotonic()+10
 while w.strategy.base != 100:
  assert time.monotonic()<until
  app.processEvents();time.sleep(.01)
 print('READY',flush=True)
 # Parent suspends the whole process, changes server price, then resumes it.
 until=time.monotonic()+15
 while w.current_price != 80:
  assert time.monotonic()<until
  app.processEvents();time.sleep(.01)
 assert w.strategy.base==80 and not entries
 print('RECOVERED',flush=True)
finally:
 w.quit_event.set();assert w.wait(3000)
"""
    proc = subprocess.Popen(
        [sys.executable, "-c", script, str(tmp_path / "pause.json"), endpoint],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        ready, _, _ = select.select([proc.stdout], [], [], 15)
        assert ready and proc.stdout.readline() == b"READY\n"
        proc.send_signal(signal.SIGSTOP)
        # Wait until the OS confirms the stop before changing the quote.
        _, status = os.waitpid(proc.pid, os.WUNTRACED)
        assert os.WIFSTOPPED(status)
        state.update(price=80, height=101)
        time.sleep(2)
        proc.send_signal(signal.SIGCONT)
        stdout, stderr = proc.communicate(timeout=20)
        assert proc.returncode == 0, stderr.decode()
        assert stdout == b"RECOVERED\n"
        assert not any("send" in method.lower() for method in state["calls"])
    finally:
        if proc.poll() is None:
            proc.send_signal(signal.SIGCONT)
            proc.kill()
            proc.communicate(timeout=5)
