"""Explicit reconstruction, not a claim of original algorithm equivalence."""

import math
from collections import deque
from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal

from dipbot.domain.exit_policy import ExitPolicy
from dipbot.domain.signal_policy import SignalPolicy
from dipbot.domain.volatility import RollingVolatility

D = Decimal


@dataclass(frozen=True)
class Settings:
    amount: D = D("0.02")
    dip: D = D("10")
    take_profit: D = D("15")
    stop_loss: D = D("15")
    slippage: D = D("5")
    dynamic: D = D("120")
    max_gap: float = 0.55
    max_roundtrip_loss: D = D("3")
    min_swaps: D = D(0)

    def __post_init__(self):
        if (
            not self.min_swaps.is_finite()
            or not 0 <= self.min_swaps <= 10000
            or self.min_swaps != self.min_swaps.to_integral_value()
        ):
            raise ValueError("Минимум Swap должен быть целым числом от 0 до 10000")
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


def snapshot_minimum(amount: int, price: D, quote_decimals: int, token_decimals: int, tolerance: D) -> int:
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
    def __init__(self, settings: Settings, policy=None, exit_policy=None):
        self.exit_policy = exit_policy or ExitPolicy()
        self.entry_time = None
        self.peak_price = None
        self.cooldown_until = None
        self.policy = policy or SignalPolicy()
        self.highs = deque()
        self.trough = None
        self.last_observation_id = None
        self.base_time = None
        self.base_reason = "Ожидание первого наблюдения"
        self.settings = settings
        self.volatility = RollingVolatility()
        self.effective_dip = settings.dip
        self.base: D | None = None
        self.entry: D | None = None
        self.last_time: float | None = None
        self.last_price: D | None = None
        self.down_streak = 0
        self.stopped = False

    def observe(self, price: D, now: float, observation_id=None, exit_return=None) -> str | None:
        if not price.is_finite() or price <= 0 or not math.isfinite(now):
            raise ValueError("Некорректная цена")
        if self.stopped:
            return None
        if self.last_time is not None and now <= self.last_time:
            return None
        if self.entry is None and self.cooldown_until is not None:
            if now < self.cooldown_until:
                self.last_time, self.last_price = now, price
                return None
            self.reset_anchor()
            self.cooldown_until = None
        gap = self.last_time is not None and now - self.last_time > self.settings.max_gap
        previous = self.last_price
        if self.policy.mode == "volatility" and observation_id is None:
            observation_id = int(now)  # Explicit fallback for DEMO/price-only tapes.
        duplicate = observation_id is not None and observation_id == self.last_observation_id
        self.last_observation_id = observation_id
        self.last_time, self.last_price = now, price
        if self.entry is None and self.policy.mode in ("window", "volatility") and duplicate and not gap:
            return None
        if self.entry is not None:
            if self.entry_time is None:
                self.entry_time = now
            self.peak_price = max(self.peak_price or self.entry, price)
            if self.exit_policy.tp_sl_basis == "quote":
                if exit_return is None or not exit_return.is_finite():
                    raise ValueError("Для TP/SL нет свежей котировки выхода")
                change = exit_return
            else:
                change = (price / self.entry - 1) * 100
            if change >= self.settings.take_profit:
                return "TAKE_PROFIT"
            if change <= -self.settings.stop_loss:
                return "STOP_LOSS"
            if (
                self.exit_policy.trailing_pct
                and (1 - price / self.peak_price) * 100 >= self.exit_policy.trailing_pct
            ):
                return "TRAILING_STOP"
            if (
                self.exit_policy.max_hold_seconds
                and now - self.entry_time >= self.exit_policy.max_hold_seconds
            ):
                return "TIME_EXIT"
            return None
        if self.policy.mode in ("window", "volatility"):
            return self.observe_window(price, now, gap)
        if self.base is None or gap:
            self.base_time = now
            self.base_reason = "Разрыв наблюдений" if gap else "Первое наблюдение"
            self.base = price
            self.down_streak = 0
            return None
        if (1 - price / self.base) * 100 >= self.settings.dip:
            return "BUY"
        # Original order: DIP first, then reanchor on growth or two down moves.
        # Flat observations preserve the streak (branch at VA 0x14089b167).
        if previous is not None and price > previous:
            self.base = price
            self.base_time = now
            self.base_reason = "Рост цены (legacy)"
            self.down_streak = 0
        elif previous is not None and price < previous:
            self.down_streak += 1
            if self.down_streak >= 2:
                self.base = price
                self.base_time = now
                self.base_reason = "Два снижения (legacy)"
                self.down_streak = 0
        return None

    def entry_wait(self, now):
        """Read-only explanation of signal state; never advances the strategy."""
        if self.entry is not None or self.stopped:
            return "", ""
        if self.cooldown_until is not None and now < self.cooldown_until:
            return "cooldown", f"Пауза после выхода: {self.cooldown_until - now:.1f} с · затем новый DIP"
        if self.base is None:
            return "baseline", "Получает котировки · формирует базу DIP"
        if self.policy.mode == "volatility" and len(self.volatility.rows) < 10:
            return "warmup", f"Прогрев волатильности: {len(self.volatility.rows)}/10 изменений"
        if self.trough is not None and self.policy.rebound_pct and self.last_price is not None:
            rebound = (self.last_price / self.trough - 1) * 100
            if rebound < self.policy.rebound_pct:
                shown = rebound.quantize(D(".0001"), rounding=ROUND_DOWN)
                return (
                    "rebound",
                    f"DIP достигнут · ждёт отскок {self.policy.rebound_pct:g}% от минимума; сейчас ≈{shown:.4f}%",
                )
        return "dip", "Ждёт падения до DIP"

    def reset_anchor(self):
        self.base = self.last_time = self.last_price = self.base_time = None
        self.down_streak = 0
        self.highs.clear()
        self.trough = None
        self.last_observation_id = None
        self.base_reason = "Ожидание нового сигнала"
        self.volatility.clear()
        self.effective_dip = self.settings.dip

    def observe_window(self, price, now, gap):
        if gap or self.base is None:
            self.volatility.clear()
            self.highs.clear()
            self.trough = None
        if self.policy.mode == "volatility":
            sigma = self.volatility.add(price, now, self.policy.window_seconds)
            self.effective_dip = max(self.settings.dip, min(D(20), sigma * self.policy.volatility_multiplier))
        cutoff = now - self.policy.window_seconds
        while self.highs and self.highs[0][0] < cutoff:
            self.highs.popleft()
        while self.highs and self.highs[-1][1] <= price:
            self.highs.pop()
        if len(self.highs) >= 10000:
            raise ValueError("Слишком много событий в окне DIP; вход остановлен")
        self.highs.append((now, price))
        self.base_time, self.base = self.highs[0]
        self.base_reason = "Разрыв наблюдений" if gap else "Максимум временного окна"
        if gap or (self.policy.mode == "volatility" and len(self.volatility.rows) < 10):
            self.trough = None
            return None
        dip = (1 - price / self.base) * 100
        if dip < self.effective_dip:
            self.trough = None
            return None
        self.trough = min(self.trough, price) if self.trough is not None else price
        rebound = (price / self.trough - 1) * 100
        if rebound >= self.policy.rebound_pct:
            return "BUY"
        return None

    def bought(self, execution_price: D, now=None):
        if self.entry is not None:
            raise ValueError("Позиция уже открыта")
        self.entry = execution_price
        self.peak_price = execution_price
        self.entry_time = self.last_time if now is None else now

    def sold(self, price: D, reason: str, now=None):
        self.entry = None
        self.entry_time = self.peak_price = None
        self.volatility.clear()
        self.effective_dip = self.settings.dip
        now = (self.last_time or 0) if now is None else now
        self.cooldown_until = (
            now + self.exit_policy.cooldown_seconds if self.exit_policy.cooldown_seconds else None
        )
        self.highs.clear()
        self.trough = None
        self.base_time = self.last_time
        self.base_reason = "Закрытие позиции"
        self.base = price
        self.last_price = price
        self.down_streak = 0
        if reason == "STOP" or (
            reason in ("STOP_LOSS", "TRAILING_STOP") and not self.exit_policy.continue_after_risk_exit
        ):
            self.stopped = True
