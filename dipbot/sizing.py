"""Per-entry sizing, distinct from cumulative turnover budgets."""
from dataclasses import dataclass
from decimal import Decimal as D
from .entry_guard import EntryRejected


@dataclass(frozen=True)
class SizingPolicy:
    unit: str = 'quote'
    reserve_bnb: D = D('0.0001')

    def __post_init__(self):
        if self.unit not in ('quote','usd'):
            raise ValueError('Неизвестная единица AMOUNT')
        if not self.reserve_bnb.is_finite() or not 0 <= self.reserve_bnb <= 1:
            raise ValueError('Резерв газа должен быть от 0 до 1 BNB')

    @classmethod
    def parse(cls,value):
        if not isinstance(value,dict): raise ValueError('Повреждены настройки размера позиции')
        try:
            return cls(value.get('unit','quote'),D(str(value.get('reserve_bnb','.0001'))))
        except (TypeError,ArithmeticError) as exc:
            raise ValueError('Повреждены настройки размера позиции') from exc

    def amount_quote(self,requested,token,rates):
        if not requested.is_finite() or requested<=0:
            raise ValueError('AMOUNT должен быть положительным')
        if self.unit=='quote': return requested
        rate=rates.snapshot(token)
        if rate is None:
            raise EntryRejected('Для AMOUNT в USD нужен свежий курс базового актива; дождитесь USD-котировки')
        return requested/D(rate['usd'])

    def export(self):
        return {'unit':self.unit,'reserve_bnb':str(self.reserve_bnb)}
