"""Bounded rolling dispersion of returns; a model, not an optimized signal."""

from collections import deque
from decimal import Decimal as D


class RollingVolatility:
    def __init__(self):
        self.clear()

    def clear(self):
        self.rows = deque()
        self.total = self.squared = D(0)
        self.previous = None

    def add(self, price, now, window):
        while self.rows and self.rows[0][0] < now - window:
            _, value = self.rows.popleft()
            self.total -= value
            self.squared -= value * value
        if self.previous is not None:
            if len(self.rows) >= 10000:
                raise ValueError("Слишком много наблюдений волатильности")
            change = (price / self.previous - 1) * 100
            self.rows.append((now, change))
            self.total += change
            self.squared += change * change
        self.previous = price
        n = len(self.rows)
        return max(D(0), (self.squared - self.total * self.total / n) / (n - 1)).sqrt() if n > 1 else D(0)
