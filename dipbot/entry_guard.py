"""Conservative same-state router screening, not a buy/sell simulation."""
from dataclasses import dataclass
from decimal import Decimal, localcontext


class EntryRejected(ValueError):
    """A market check rejected entry before any transaction intent exists."""


@dataclass(frozen=True)
class EntryQuote:
    amount_in: int
    target_out: int
    reverse_out: int
    block: int
    roundtrip_loss_pct: Decimal


def assess(amount, target, reverse, block, maximum):
    if any(type(x) is not int or x <= 0 or x >= 2**256 for x in (amount, target, reverse)):
        raise EntryRejected('Вход пропущен: нулевая или некорректная котировка BUY/SELL')
    if not maximum.is_finite() or not 0 <= maximum <= 20:
        raise ValueError('Лимит потерь BUY→SELL должен быть от 0 до 20%')
    # Integer ratio comparison preserves the boundary regardless of Decimal precision.
    numerator, denominator = maximum.as_integer_ratio()
    if (amount - reverse) * 100 * denominator > amount * numerator:
        raise EntryRejected('Вход пропущен: потери по котировкам BUY→SELL превышают лимит')
    with localcontext() as ctx:
        ctx.prec = 78
        loss = (Decimal(amount) - Decimal(reverse)) * 100 / Decimal(amount)
    return EntryQuote(amount, target, reverse, block, loss)
