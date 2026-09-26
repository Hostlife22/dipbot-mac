"""Two read-only sources: bounded latency history, backoff and switching hysteresis."""

from collections import deque
from statistics import median


class RpcHealth:
    def __init__(self):
        self.samples = [deque(maxlen=32), deque(maxlen=32)]
        self.failures = [0, 0]
        self.blocked_until = [0.0, 0.0]
        self.preferred = 0
        self.last_probe = float("-inf")
        self.last_switch = float("-inf")

    def choose(self, now):
        if not self.samples[0] and not self.failures[0]:
            return 0
        other = 1 - self.preferred
        if now < self.blocked_until[self.preferred]:
            if now < self.blocked_until[other]:
                raise TimeoutError("Оба RPC на паузе после ошибок")
            self.preferred = other
        other = 1 - self.preferred
        if now - self.last_probe >= 30 and now >= self.blocked_until[other]:
            self.last_probe = now
            return other
        return self.preferred

    def success(self, source, duration, now):
        self.samples[source].append(duration)
        self.failures[source] = 0
        self.blocked_until[source] = 0
        other = 1 - source
        if (
            source != self.preferred
            and len(self.samples[source]) >= 3
            and self.samples[other]
            and median(self.samples[source]) < 0.75 * median(self.samples[other])
            and now - self.last_switch >= 30
        ):
            self.preferred = source
            self.last_switch = now

    def failure(self, source, now):
        self.failures[source] += 1
        self.blocked_until[source] = now + min(120, 15 * 2 ** min(self.failures[source], 3))

    def report(self):
        return [
            {
                "source": name,
                "samples": len(self.samples[i]),
                "median_ms": 1000 * median(self.samples[i]) if self.samples[i] else None,
                "consecutive_errors": self.failures[i],
                "preferred": i == self.preferred,
            }
            for i, name in enumerate(("основной", "резервный"))
        ]
