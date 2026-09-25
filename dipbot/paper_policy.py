"""Explicit latency and per-fill base-currency cost model for online PAPER."""
from dataclasses import dataclass
from decimal import Decimal as D
import math


@dataclass(frozen=True)
class PaperPolicy:
    latency_seconds: float = .25
    fee_quote: D = D(0)

    def __post_init__(self):
        if not math.isfinite(self.latency_seconds) or not 0 <= self.latency_seconds <= 10:
            raise ValueError('Задержка PAPER должна быть от 0 до 10 секунд')
        if not self.fee_quote.is_finite() or not 0 <= self.fee_quote <= 10**9:
            raise ValueError('Некорректная стоимость операции PAPER')

    @classmethod
    def parse(cls,value):
        if not isinstance(value,dict):raise ValueError('Повреждена модель PAPER')
        try:return cls(float(value.get('latency_seconds',.25)),D(str(value.get('fee_quote',0))))
        except (TypeError,ArithmeticError,OverflowError) as exc:raise ValueError('Повреждена модель PAPER') from exc

    def export(self):
        return {'latency_seconds':self.latency_seconds,'fee_quote':str(self.fee_quote)}
