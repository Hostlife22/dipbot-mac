"""Optional latest-only BSC head hints. HTTP remains authoritative for quotes."""

import json
import logging
import re
import threading
import time
from dataclasses import dataclass
from urllib.parse import urlsplit

from websockets.sync.client import connect


@dataclass(frozen=True)
class Head:
    number: int
    hash: str
    parent: str
    received_at: float
    revision: int
    discontinuity: int


class HeadFeed:
    def __init__(self, endpoint, connector=connect):
        url = urlsplit(endpoint)
        local = url.scheme == "ws" and url.hostname in ("localhost", "127.0.0.1", "::1")
        if (
            (not local and url.scheme != "wss")
            or not url.hostname
            or url.username
            or url.password
            or url.fragment
        ):
            raise ValueError("WebSocket должен быть WSS (WS допустим только для localhost)")
        self.endpoint = endpoint
        self.connector = connector
        self.stop_event = threading.Event()
        self.lock = threading.Lock()
        self.head = None
        self.connected = False
        self.reconnects = 0
        self.error_type = ""
        self.thread = None
        self.socket = None
        self.logger = logging.getLogger("dipbot.private_websocket")
        # Library DEBUG logging can contain credential-bearing handshake paths.
        self.logger.disabled = True

    def start(self):
        self.thread = threading.Thread(target=self.run, name="bsc-heads", daemon=True)
        self.thread.start()
        return self

    def snapshot(self):
        with self.lock:
            return self.head if self.connected else None

    def accept(self, value, now=None):
        number = int(value["number"], 16)
        block_hash, parent = value["hash"].lower(), value["parentHash"].lower()
        if number < 0 or any(not re.fullmatch(r"0x[0-9a-f]{64}", h) for h in (block_hash, parent)):
            raise ValueError("Malformed head")
        now = time.monotonic() if now is None else now
        with self.lock:
            previous = self.head
            if previous and previous.number == number and previous.hash == block_hash:
                return False  # Replays do not refresh the feed's health clock.
            discontinuity = previous.discontinuity if previous else 0
            if previous and (number != previous.number + 1 or parent != previous.hash):
                discontinuity += 1
            self.head = Head(
                number, block_hash, parent, now, previous.revision + 1 if previous else 1, discontinuity
            )
            return True

    @staticmethod
    def request(ws, identifier, method, params):
        ws.send(json.dumps({"jsonrpc": "2.0", "id": identifier, "method": method, "params": params}))
        reply = json.loads(ws.recv(timeout=5))
        if reply.get("id") != identifier or "error" in reply or "result" not in reply:
            raise ValueError("Invalid subscription response")
        return reply["result"]

    def run(self):
        delay = 0.5
        while not self.stop_event.is_set():
            try:
                with self.connector(
                    self.endpoint,
                    open_timeout=5,
                    close_timeout=0.2,
                    max_size=65536,
                    max_queue=4,
                    logger=self.logger,
                ) as ws:
                    self.socket = ws
                    if self.request(ws, 1, "eth_chainId", []) != "0x38":
                        raise ValueError("Wrong network")
                    subscription = self.request(ws, 2, "eth_subscribe", ["newHeads"])
                    if not isinstance(subscription, str) or not subscription:
                        raise ValueError("Invalid subscription")
                    with self.lock:
                        self.connected = True
                        self.error_type = ""
                    delay = 0.5
                    while not self.stop_event.is_set():
                        try:
                            message = json.loads(ws.recv(timeout=0.25))
                        except TimeoutError:
                            continue
                        if message.get("method") != "eth_subscription":
                            continue
                        params = message.get("params", {})
                        if params.get("subscription") == subscription:
                            self.accept(params["result"])
            except Exception as exc:
                with self.lock:
                    self.error_type = type(exc).__name__
                    self.reconnects += 1
            finally:
                with self.lock:
                    self.connected = False
                self.socket = None
            if self.stop_event.wait(delay):
                break
            delay = min(10, delay * 2)

    def stop(self):
        self.stop_event.set()
        socket = self.socket
        if socket is not None:
            try:
                socket.close()
            except Exception:
                pass
        if self.thread is not None:
            self.thread.join(timeout=0.5)


class HeadSchedule:
    """Hints can wake reads early; they can defer HTTP by at most 300 ms."""

    def __init__(self):
        self.revision = 0
        self.discontinuity = 0
        self.last_read = float("-inf")
        self.last_head = None

    def due(self, head, now, next_poll):
        healthy = head is not None and 0 <= now - head.received_at < 1
        if not healthy:
            return now >= next_poll
        return head.revision != self.revision or now >= max(next_poll, self.last_read + 0.3)

    def consume(self, head, now):
        reset = False
        if head is not None:
            reset = head.discontinuity != self.discontinuity
            self.last_head = head
            self.revision = head.revision
            self.discontinuity = head.discontinuity
        self.last_read = now
        return reset
