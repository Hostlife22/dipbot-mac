"""Bounded, process-local timing statistics. Never retain arguments or responses."""

from __future__ import annotations

import math
import threading
import time
from collections import deque
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from functools import wraps
from typing import Any, ParamSpec, TypeVar


class Timings:
    def __init__(self, capacity: int = 2048, max_series: int = 128) -> None:
        self.capacity = capacity
        self.max_series = max_series
        self._series: dict[str, list[Any]] = {}
        self._lock = threading.Lock()

    def record(self, name: str, seconds: float, failed: bool = False) -> None:
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
    def measure(self, name: str) -> Iterator[None]:
        start = time.perf_counter()
        failed = False
        try:
            yield
        except BaseException:
            failed = True
            raise
        finally:
            self.record(name, time.perf_counter() - start, failed)

    def snapshot(self) -> Any:
        with self._lock:
            rows = {k: (v[0], v[1], sorted(v[2])) for k, v in self._series.items()}
        result = {}
        for name, (count, errors, samples) in rows.items():
            result[name] = {"count": count, "errors": errors, "window": len(samples)}
            for label, fraction in [("p50_ms", 0.50), ("p95_ms", 0.95), ("p99_ms", 0.99)]:
                result[name][label] = samples[max(0, math.ceil(len(samples) * fraction) - 1)]
        return result


TIMINGS = Timings()


P = ParamSpec("P")
R = TypeVar("R")


def timed(name: str) -> Callable[[Callable[P, R]], Callable[P, R]]:
    def decorate(function: Callable[P, R]) -> Callable[P, R]:
        @wraps(function)
        def wrapped(*args: P.args, **kwargs: P.kwargs) -> R:
            with TIMINGS.measure(name):
                return function(*args, **kwargs)

        return wrapped

    return decorate
