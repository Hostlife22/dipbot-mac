"""Optional per-entry estimated cost ceiling; never a cumulative turnover budget."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal as D
from decimal import localcontext

from dipbot.domain.assets import WBNB
from dipbot.domain.entry_guard import EntryQuote, EntryRejected
from dipbot.domain.ports import QuoteAsset, RateSource
from dipbot.domain.records import CostSettings


@dataclass(frozen=True)
class CostPolicy:
    maximum_pct: D = D(0)
    roundtrip_gas: int = 400000

    def __post_init__(self) -> None:
        if not self.maximum_pct.is_finite() or not 0 <= self.maximum_pct <= 100:
            raise ValueError("Лимит расчётных расходов должен быть от 0 до 100%")
        if type(self.roundtrip_gas) is not int or not 21000 <= self.roundtrip_gas <= 2000000:
            raise ValueError("Модель газа должна быть от 21000 до 2000000 единиц")

    @classmethod
    def parse(cls, data: object) -> CostPolicy:
        if not isinstance(data, dict):
            raise ValueError("Повреждена модель расходов")
        try:
            raw = D(str(data.get("roundtrip_gas", 400000)))
            if not raw.is_finite() or raw != raw.to_integral_value():
                raise ValueError("Газ должен быть целым числом")
            return cls(D(str(data.get("maximum_pct", 0))), int(raw))
        except (TypeError, ArithmeticError, OverflowError) as exc:
            raise ValueError("Повреждена модель расходов") from exc

    def export(self) -> CostSettings:
        return {"maximum_pct": str(self.maximum_pct), "roundtrip_gas": self.roundtrip_gas}

    def assess(self, entry_quote: EntryQuote, pool: QuoteAsset, gas_gwei: D, rates: RateSource) -> D | None:
        if not self.maximum_pct:
            return None
        if not gas_gwei.is_finite() or not 0 < gas_gwei <= 1000:
            raise ValueError("Недопустимый gas price")
        with localcontext() as ctx:
            ctx.prec = 78
            gas_base = D(self.roundtrip_gas) * gas_gwei / D(10) ** 9
            if pool.quote.lower() != WBNB.lower():
                native, base = rates.snapshot(WBNB), rates.snapshot(pool.quote)
                if native is None or base is None:
                    raise EntryRejected("Для модели расходов нет свежего USD-курса газа/базы")
                gas_base *= D(native["usd"]) / D(base["usd"])
            amount = D(entry_quote.amount_in) / D(10) ** pool.quote_decimals
            total = entry_quote.roundtrip_loss_pct + gas_base * 100 / amount
            if total > self.maximum_pct:
                raise EntryRejected(f"Расчётные расходы BUY→SELL с моделью газа {total:.2f}% превышают лимит")
            return total
