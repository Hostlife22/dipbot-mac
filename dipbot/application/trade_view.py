from dipbot.domain.records import TradeDetail

"""Presentation-only cash-flow snapshots. Pool fees are already inside swap amounts."""

from decimal import Decimal as D

from dipbot.execution.accounting import marked_value


def entry_view(quantity, gross, fee_usd, rate, *, total_usd=None) -> TradeDetail:
    gross_usd = marked_value(gross, rate) if gross is not None else None
    if total_usd is None and gross_usd is not None and fee_usd is not None:
        total_usd = str(D(gross_usd) + D(fee_usd))
    return {
        "quantity": str(quantity),
        "entry_gross_usd": gross_usd,
        "entry_fee_usd": fee_usd,
        "entry_total_usd": total_usd,
        "buy_price_usd": marked_value(D(gross) / D(quantity), rate)
        if gross is not None and D(quantity) > 0
        else None,
    }


def exit_view(entry, quantity, gross, fee_usd, rate, *, complete=True):
    result = dict(entry or {})
    gross_usd = marked_value(gross, rate)
    known = complete and entry and entry.get("entry_total_usd") is not None
    result.update(
        sell_price_usd=marked_value(D(gross) / D(quantity), rate) if D(quantity) > 0 else None,
        exit_gross_usd=gross_usd,
        exit_fee_usd=fee_usd,
        complete=bool(complete),
        net_usd=str(D(gross_usd) - D(entry["entry_total_usd"]) - D(fee_usd))
        if known and gross_usd is not None and fee_usd is not None
        else None,
    )
    return result
