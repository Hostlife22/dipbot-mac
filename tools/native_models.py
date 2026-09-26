"""Pure audit models for recovered Windows rules; not LIVE execution policy."""
import math


def converter_preview(expected, slippage_pct, router):
    # Native float -> round -> int, including Python ties-to-even. Production
    # Mac keeps Decimal bounds and intentionally does not add this tax buffer.
    slip = float(slippage_pct)
    if not math.isfinite(slip):
        raise ValueError('non-finite slippage')
    bps = int(round(slip * 100))
    if not 0 <= bps <= 2000 or expected <= 0 or router not in ('V2', 'V3'):
        raise ValueError('invalid preview')
    tax = 500 if router == 'V2' else 0
    minimum = max(1, expected * (10000-min(9500,bps+tax)) // 10000)
    return {'slippage_bps':bps,'tax_buffer_bps':tax,'min_out_raw':minimum}


def registry_key(router, token):
    from dipbot.market.chain import address
    return router.strip().upper(), address(token).lower()


def purpose_entropy(purpose, *, default_prefix, owner_prefix, owner_purposes):
    # Prefix values are injected for synthetic tests; no vault/license access.
    prefix = owner_prefix if purpose in owner_purposes else default_prefix
    return (prefix + purpose).encode('utf-8')


def roundtrip_loss_bps(amount_in, reverse_out):
    """Recovered integer-input path, not a model of arbitrary Python coercions."""
    if type(amount_in) is not int or type(reverse_out) is not int:
        raise ValueError('audit model accepts integer raw units only')
    reverse_out = max(0, reverse_out)
    if amount_in <= 0:
        return 10000
    if reverse_out >= amount_in:
        return 0
    return (amount_in - reverse_out) * 10000 // amount_in
