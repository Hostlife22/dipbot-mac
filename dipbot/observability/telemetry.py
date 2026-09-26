"""Bounded, process-local timing statistics. Never retain arguments or responses."""

import math
import threading
import time
from collections import deque
from contextlib import contextmanager
from functools import wraps


class Timings:
    def __init__(self, capacity=2048, max_series=128):
        self.capacity = capacity
        self.max_series = max_series
        self._series = {}
        self._lock = threading.Lock()

    def record(self, name, seconds, failed=False):
        if not math.isfinite(seconds) or seconds < 0:
            return
        with self._lock:
            if name not in self._series:
                if len(self._series) >= self.max_series:
                    return
                self._series[name] = [0, 0, deque(maxlen=self.capacity)]
            row = self._series[name]
            row[0] += 1
            row[1] += int(failed)
            row[2].append(seconds * 1000)

    @contextmanager
    def measure(self, name):
        start = time.perf_counter()
        failed = False
        try:
            yield
        except BaseException:
            failed = True
            raise
        finally:
            self.record(name, time.perf_counter() - start, failed)

    def snapshot(self):
        with self._lock:
            rows = {k: (v[0], v[1], sorted(v[2])) for k, v in self._series.items()}
        result = {}
        for name, (count, errors, samples) in rows.items():
            result[name] = {"count": count, "errors": errors, "window": len(samples)}
            for label, fraction in [("p50_ms", 0.50), ("p95_ms", 0.95), ("p99_ms", 0.99)]:
                result[name][label] = samples[max(0, math.ceil(len(samples) * fraction) - 1)]
        return result


TIMINGS = Timings()


def timed(name):
    def decorate(function):
        @wraps(function)
        def wrapped(*args, **kwargs):
            with TIMINGS.measure(name):
                return function(*args, **kwargs)

        return wrapped

    return decorate
