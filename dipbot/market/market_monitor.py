"""Independent read-only price monitoring while the serial executor is occupied."""
from dataclasses import dataclass
from functools import wraps
import threading
import time

from dipbot.market.chain import Chain
from dipbot.observability.telemetry import TIMINGS


@dataclass(frozen=True)
class MarketSnapshot:
    price: object
    received_at: float
    revision: int
    block: int
    pool: str
    quote: str
    block_timestamp: int | None = None


class MarketMonitor:
    def __init__(self, endpoint, pool, factory=None, max_block_age=5):
        self.endpoint, self.pool = endpoint, pool
        self.factory = factory or (lambda url: Chain(url, request_timeout=2, max_block_age=max_block_age))
        self.stop_event = threading.Event()
        self.lock = threading.Lock()
        self.latest = None
        self.error_type = ''
        self.thread = None

    def start(self):
        self.thread = threading.Thread(target=self.run, name='execution-market-monitor', daemon=True)
        self.thread.start()
        return self

    def snapshot(self, now=None):
        now = time.monotonic() if now is None else now
        with self.lock:
            value = self.latest
            if value is None or self.error_type or not 0 <= now-value.received_at <= .55:
                return None
            return value

    def run(self):
        revision = 0
        failures = 0
        try:
            chain = self.factory(self.endpoint)
            chain.restrict_to_reads()
            if isinstance(chain, Chain):
                provider = chain.w3.provider
                request = provider.make_request
                def cancellable(method, params):
                    if self.stop_event.is_set():
                        raise RuntimeError('Monitor stopped')
                    return request(method, params)
                provider.make_request = cancellable
            pool = chain.verify_pool(self.pool.address, self.pool.token)
            if pool != self.pool:
                raise ValueError('Monitor returned a different pool')
            while not self.stop_event.is_set():
                started = time.monotonic()
                try:
                    with TIMINGS.measure('monitor.price'):
                        price = chain.price(pool)
                    revision += 1
                    snapshot = MarketSnapshot(price, time.monotonic(), revision,
                                              chain.price_block['number'], pool.address, pool.quote,
                                              chain.price_block.get('timestamp'))
                    with self.lock:
                        if not self.stop_event.is_set():
                            self.latest = snapshot
                            self.error_type = ''
                    failures = 0
                except Exception as exc:
                    failures += 1
                    with self.lock:
                        self.error_type = type(exc).__name__
                delay = min(5, .5 * 2**min(failures, 3)) if failures else .3
                if self.stop_event.wait(max(0, started + delay - time.monotonic())):
                    break
        except Exception as exc:
            with self.lock:
                self.error_type = type(exc).__name__
        finally:
            with self.lock:
                self.latest = None

    def stop(self):
        self.stop_event.set()
        if self.thread is not None:
            self.thread.join(timeout=.1)


def monitor_execution(function):
    @wraps(function)
    def wrapped(worker, *args, **kwargs):
        if worker.mode == 'DEMO' or not isinstance(worker.chain, Chain) or worker.pool is None:
            return function(worker, *args, **kwargs)
        monitor = MarketMonitor(str(worker.chain.w3.provider.endpoint_uri), worker.pool,
                                max_block_age=worker.strategy.policy.max_block_age)
        worker.execution_monitor = monitor.start()
        try:
            return function(worker, *args, **kwargs)
        finally:
            worker.execution_monitor = None
            monitor.stop()
    return wrapped
