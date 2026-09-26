"""Explicit latency and per-fill base-currency cost model for online PAPER."""

import math
from dataclasses import dataclass
from decimal import Decimal as D


@dataclass(frozen=True)
class PaperPolicy:
    latency_seconds: float = 0.25
    fee_quote: D = D(0)
    gas_units: int = 0

    def __post_init__(self):
        if type(self.gas_units) is not int or not 0 <= self.gas_units <= 2_000_000:
            raise ValueError("Модель газа PAPER: целое число от 0 до 2000000")
        if not math.isfinite(self.latency_seconds) or not 0 <= self.latency_seconds <= 10:
            raise ValueError("Задержка PAPER должна быть от 0 до 10 секунд")
        if not self.fee_quote.is_finite() or not 0 <= self.fee_quote <= 10**9:
            raise ValueError("Некорректная стоимость операции PAPER")

    @classmethod
    def parse(cls, value):
        if not isinstance(value, dict):
            raise ValueError("Повреждена модель PAPER")
        try:
            return cls(
                float(value.get("latency_seconds", 0.25)),
                D(str(value.get("fee_quote", 0))),
                value.get("gas_units", 0),
            )
        except (TypeError, ArithmeticError, OverflowError) as exc:
            raise ValueError("Повреждена модель PAPER") from exc

    def export(self):
        return {
            "gas_units": self.gas_units,
            "latency_seconds": self.latency_seconds,
            "fee_quote": str(self.fee_quote),
        }

    def operation_cost(self, gas_gwei, quote, rates):
        """Fixed extra cost plus explicit gas-unit assumption at current FX."""
        from dipbot.domain.assets import WBNB

        if not self.gas_units:
            return self.fee_quote
        if not gas_gwei.is_finite() or not 0 < gas_gwei <= 1000:
            raise ValueError("Некорректная цена газа PAPER")
        native = D(self.gas_units) * gas_gwei / D(10) ** 9
        if quote.lower() == WBNB.lower():
            return self.fee_quote + native
        base_rate, native_rate = rates.snapshot(quote), rates.snapshot(WBNB)
        if base_rate is None or native_rate is None:
            raise TimeoutError("Для модели газа PAPER нужны свежие курсы базы и BNB")
        return self.fee_quote + native * D(native_rate["usd"]) / D(base_rate["usd"])
