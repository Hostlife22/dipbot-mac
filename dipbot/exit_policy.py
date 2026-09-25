"""Opt-in exit controls; existing TP/SL remains the default."""
from dataclasses import dataclass
from decimal import Decimal as D
import math


@dataclass(frozen=True)
class ExitPolicy:
    trailing_pct: D = D(0)
    max_hold_seconds: float = 0
    cooldown_seconds: float = 0

    def __post_init__(self):
        if not self.trailing_pct.is_finite() or not 0 <= self.trailing_pct < 100:
            raise ValueError('Trailing должен быть от 0 до 100% (не включая 100)')
        if not math.isfinite(self.max_hold_seconds) or not 0 <= self.max_hold_seconds <= 86400:
            raise ValueError('Время позиции должно быть от 0 до 86400 секунд')
        if not math.isfinite(self.cooldown_seconds) or not 0 <= self.cooldown_seconds <= 3600:
            raise ValueError('Пауза после выхода должна быть от 0 до 3600 секунд')

    @classmethod
    def parse(cls,value):
        if not isinstance(value,dict): raise ValueError('Повреждены настройки выхода')
        try:
            return cls(D(str(value.get('trailing_pct',0))),float(value.get('max_hold_seconds',0)),
                       float(value.get('cooldown_seconds',0)))
        except (TypeError,ArithmeticError,OverflowError) as exc:
            raise ValueError('Повреждены настройки выхода') from exc

    def export(self):
        return {'trailing_pct':str(self.trailing_pct),'max_hold_seconds':self.max_hold_seconds,
                'cooldown_seconds':self.cooldown_seconds}
