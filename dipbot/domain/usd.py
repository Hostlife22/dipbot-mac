"""Indicative USD marks for display/accounting and explicitly selected USD sizing."""

from decimal import Decimal, InvalidOperation


def select_rate(rows, token):
    candidates = []
    for row in rows:
        if (
            row.get("chainId") != "bsc"
            or row.get("baseToken", {}).get("address", "").lower() != token.lower()
        ):
            continue
        try:
            rate = Decimal(str(row.get("priceUsd")))
            liquidity = Decimal(str((row.get("liquidity") or {}).get("usd", 0)))
            if rate.is_finite() and rate > 0 and liquidity.is_finite() and liquidity > 0:
                candidates.append((liquidity, rate))
        except (InvalidOperation, ValueError):
            continue
    if not candidates:
        raise ValueError("USD rate unavailable")
    return max(candidates)[1]


def price_text(value, rate=None, digits=8):
    value = Decimal(str(value))
    if rate is not None:
        value *= rate
    # Keep small token prices readable, without removing significant leading zeros.
    rounded = Decimal(f"{value:.{digits}g}")
    text = format(rounded, "f") if not value or abs(value) >= Decimal("1e-12") else f"{value:.{digits}g}"
    if "." in text and "e" not in text.lower():
        text = text.rstrip("0").rstrip(".")
    return ("$" if rate is not None else "") + text
