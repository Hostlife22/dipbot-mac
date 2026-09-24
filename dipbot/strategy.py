"""Explicit reconstruction, not a claim of original algorithm equivalence."""
from dataclasses import dataclass
from decimal import Decimal

D = Decimal


@dataclass(frozen=True)
class Settings:
    amount: D = D("0.02")
    dip: D = D("3")
    take_profit: D = D("2")
    stop_loss: D = D("5")
    slippage: D = D("2")
    dynamic: D = D("150")
    max_gap: float = 10.0

    def __post_init__(self):
        values = (self.amount, self.dip, self.take_profit, self.stop_loss, self.slippage, self.dynamic)
        if any(not x.is_finite() for x in values):
            raise ValueError("Параметры должны быть конечными числами")
        if self.amount <= 0 or not 0 < self.dip < 100 or self.take_profit <= 0:
            raise ValueError("AMOUNT и TP > 0; DIP между 0 и 100")
        if not 0 < self.stop_loss < 100 or not 0 <= self.slippage <= 20:
            raise ValueError("STOP LOSS между 0 и 100; SLIPPAGE от 0 до 20")
        if not 0 <= self.dynamic <= self.slippage * 100:
            raise ValueError("DYNAMIC должен быть от 0 до SLIPPAGE × 100")
        if self.max_gap <= 0:
            raise ValueError("Недопустимый интервал устаревания")

    @property
    def buy_tolerance(self) -> D:
        return self.slippage - self.dynamic / 100


def raw_amount(amount: D, decimals: int) -> int:
    if not amount.is_finite() or amount <= 0 or not 0 <= decimals <= 36:
        raise ValueError("Некорректное количество или decimals")
    numerator, denominator = amount.as_integer_ratio()
    result = numerator * 10**decimals // denominator
    if result <= 0 or result >= 2**256:
        raise ValueError("Количество вне диапазона токена")
    return result


def minimum_out(quoted: int, tolerance: D) -> int:
    if quoted <= 0 or not tolerance.is_finite() or not 0 <= tolerance <= 20:
        raise ValueError("Некорректная котировка / slippage")
    numerator, denominator = tolerance.as_integer_ratio()
    result = quoted * (100 * denominator - numerator) // (100 * denominator)
    if result <= 0:
        raise ValueError("Минимальный выход округлился до нуля")
    return result


class Strategy:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.base: D | None = None
        self.entry: D | None = None
        self.last_time: float | None = None
        self.last_price: D | None = None
        self.stopped = False

    def observe(self, price: D, now: float) -> str | None:
        if not price.is_finite() or price <= 0:
            raise ValueError("Некорректная цена")
        if self.stopped:
            return None
        if self.last_time is not None and now <= self.last_time:
            return None
        gap = self.last_time is not None and now - self.last_time > self.settings.max_gap
        self.last_time, self.last_price = now, price
        if self.entry is not None:
            change = (price / self.entry - 1) * 100
            if change <= -self.settings.stop_loss:
                return "STOP_LOSS"
            if change >= self.settings.take_profit:
                return "TAKE_PROFIT"
            return None
        # Our documented assumption: rolling high water mark, reset after feed gaps.
        if self.base is None or gap or price > self.base:
            self.base = price
            return None
        if (1 - price / self.base) * 100 >= self.settings.dip:
            return "BUY"
        return None

    def bought(self, execution_price: D):
        if self.entry is not None:
            raise ValueError("Позиция уже открыта")
        self.entry = execution_price

    def sold(self, price: D, reason: str):
        self.entry = None
        self.base = price
        if reason in ("STOP_LOSS", "STOP"):
            self.stopped = True
