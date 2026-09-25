"""Explicit reconstruction, not a claim of original algorithm equivalence."""
from dataclasses import dataclass
from decimal import Decimal
import math

D = Decimal


@dataclass(frozen=True)
class Settings:
    amount: D = D("0.02")
    dip: D = D("3")
    take_profit: D = D("2")
    stop_loss: D = D("2")
    slippage: D = D("3")
    dynamic: D = D("150")
    max_gap: float = 0.55
    max_roundtrip_loss: D = D("3")

    def __post_init__(self):
        values = (self.amount, self.dip, self.take_profit, self.stop_loss, self.slippage, self.dynamic)
        if any(not x.is_finite() for x in values):
            raise ValueError("Параметры должны быть конечными числами")
        if self.amount <= 0 or not 0 < self.dip < 100 or self.take_profit <= 0:
            raise ValueError("AMOUNT и TP > 0; DIP между 0 и 100")
        if not 0 < self.stop_loss < 100 or not 0 <= self.slippage <= 20:
            raise ValueError("STOP LOSS между 0 и 100; SLIPPAGE от 0 до 20")
        if not self.max_roundtrip_loss.is_finite() or not 0 <= self.max_roundtrip_loss <= 20:
            raise ValueError("Лимит потерь BUY→SELL должен быть от 0 до 20%")
        if self.dynamic < 0:
            raise ValueError("DYNAMIC должен быть неотрицательным")
        if not math.isfinite(self.max_gap) or self.max_gap <= 0:
            raise ValueError("Недопустимый интервал устаревания")

    @property
    def buy_tolerance(self) -> D:
        return max(D(0), min(D(99), self.slippage - self.dynamic / 100))


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


def snapshot_minimum(amount: int, price: D, quote_decimals: int,
                     token_decimals: int, tolerance: D) -> int:
    """BUY guard from the signal's spot price, before pool fees/price impact.

    Reconstructed from Trader.buy_min_out_from_snapshot at VA 0x141e63dc0.
    Integer ratios avoid Decimal context rounding for large raw amounts.
    """
    if amount <= 0 or not price.is_finite() or price <= 0:
        raise ValueError("Некорректная сумма / цена снимка")
    if not 0 <= quote_decimals <= 36 or not 0 <= token_decimals <= 36:
        raise ValueError("Некорректные decimals")
    numerator, denominator = price.as_integer_ratio()
    expected = amount * denominator * 10**token_decimals // (numerator * 10**quote_decimals)
    return minimum_out(expected, tolerance)


class Strategy:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.base: D | None = None
        self.entry: D | None = None
        self.last_time: float | None = None
        self.last_price: D | None = None
        self.down_streak = 0
        self.stopped = False

    def observe(self, price: D, now: float) -> str | None:
        if not price.is_finite() or price <= 0 or not math.isfinite(now):
            raise ValueError("Некорректная цена")
        if self.stopped:
            return None
        if self.last_time is not None and now <= self.last_time:
            return None
        gap = self.last_time is not None and now - self.last_time > self.settings.max_gap
        previous = self.last_price
        self.last_time, self.last_price = now, price
        if self.entry is not None:
            change = (price / self.entry - 1) * 100
            if change >= self.settings.take_profit:
                return "TAKE_PROFIT"
            if change <= -self.settings.stop_loss:
                return "STOP_LOSS"
            return None
        if self.base is None or gap:
            self.base = price
            self.down_streak = 0
            return None
        if (1 - price / self.base) * 100 >= self.settings.dip:
            return "BUY"
        # Original order: DIP first, then reanchor on growth or two down moves.
        # Flat observations preserve the streak (branch at VA 0x14089b167).
        if previous is not None and price > previous:
            self.base = price
            self.down_streak = 0
        elif previous is not None and price < previous:
            self.down_streak += 1
            if self.down_streak >= 2:
                self.base = price
                self.down_streak = 0
        return None

    def bought(self, execution_price: D):
        if self.entry is not None:
            raise ValueError("Позиция уже открыта")
        self.entry = execution_price

    def sold(self, price: D, reason: str):
        self.entry = None
        self.base = price
        self.last_price = price
        self.down_streak = 0
        if reason in ("STOP_LOSS", "STOP"):
            self.stopped = True
