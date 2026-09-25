"""Explicit strategy versions, independent from legacy numeric trade settings."""
from dataclasses import dataclass
from decimal import Decimal
import math


@dataclass(frozen=True)
class SignalPolicy:
    mode: str = 'legacy'
    window_seconds: float = 60.0
    rebound_pct: Decimal = Decimal('0')
    max_block_age: float = 5.0

    def __post_init__(self):
        if not math.isfinite(self.max_block_age) or not 1 <= self.max_block_age <= 30:
            raise ValueError("Возраст блока должен быть от 1 до 30 секунд")
        if self.mode not in ('legacy', 'window'):
            raise ValueError('Неизвестная версия стратегии')
        if not math.isfinite(self.window_seconds) or not 1 <= self.window_seconds <= 300:
            raise ValueError('Окно DIP должно быть от 1 до 300 секунд')
        if not self.rebound_pct.is_finite() or not 0 <= self.rebound_pct <= 20:
            raise ValueError('Подтверждение отскока должно быть от 0 до 20%')

    @classmethod
    def parse(cls, value):
        if not isinstance(value, dict):
            raise ValueError('Повреждены параметры версии стратегии')
        try:
            return cls(value.get('mode', 'legacy'), float(value.get('window_seconds', 60)),
                       Decimal(str(value.get('rebound_pct', 0))), float(value.get('max_block_age', 5)))
        except (TypeError, ArithmeticError, OverflowError) as exc:
            raise ValueError('Повреждены параметры версии стратегии') from exc

    def export(self):
        return {'mode': self.mode, 'window_seconds': self.window_seconds,
                'rebound_pct': str(self.rebound_pct), 'max_block_age': self.max_block_age}
