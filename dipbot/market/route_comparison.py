"""Read-only, amount-specific route comparison at one canonical block."""

import time
from decimal import Decimal as D
from decimal import localcontext

from dipbot.domain.entry_guard import EntryRejected, assess
from dipbot.domain.strategy import raw_amount


def compare(
    chain,
    pools,
    reference,
    amount,
    maximum,
    cost_policy,
    gas_gwei,
    rates,
    *,
    cancelled=lambda: False,
    timeout=15,
):
    if reference is None:
        raise ValueError("Выберите маршрут, чтобы определить TARGET и базу сравнения")
    amount_raw = raw_amount(amount, reference.quote_decimals)
    candidates = {
        p.address.lower(): p
        for p in pools
        if (p.token.lower(), p.quote.lower(), p.token_decimals, p.quote_decimals)
        == (
            reference.token.lower(),
            reference.quote.lower(),
            reference.token_decimals,
            reference.quote_decimals,
        )
    }
    if not candidates or len(candidates) > 32:
        raise ValueError("Для сравнения нужно от 1 до 32 пулов одной пары")
    block = chain.check(force_network=False)
    header = dict(chain.checked_header)
    started = time.monotonic()
    rows = []
    for pool in candidates.values():
        if cancelled():
            raise EntryRejected("Сравнение отменено: STOP или изменился адрес")
        if time.monotonic() - started > timeout:
            raise TimeoutError("Сравнение превысило лимит времени; сузьте список маршрутов")
        row = {"pool": pool, "error": None}
        try:
            output = chain.quote(pool, amount_raw, True, block=block)
            if type(output) is not int or not 0 < output < 2**256:
                raise EntryRejected("Нулевая или некорректная котировка покупки")
            reverse = chain.quote(pool, output, False, block=block)
            result = assess(amount_raw, output, reverse, block, maximum)
            total = cost_policy.assess(result, pool, gas_gwei, rates)
            with localcontext() as ctx:
                ctx.prec = 78
                row.update(
                    target_out=D(output) / D(10) ** pool.token_decimals,
                    loss_pct=result.roundtrip_loss_pct,
                    modeled_cost_pct=total,
                )
        except EntryRejected as exc:
            row["error"] = str(exc)
        except Exception as exc:
            # RPC exception messages may contain credential-bearing URLs.
            row["error"] = "Котировка недоступна: " + type(exc).__name__
        rows.append(row)
    chain.canonical_receipt({"blockNumber": block, "blockHash": header["hash"]})
    if cancelled() or time.monotonic() - started > timeout:
        raise EntryRejected("Результат сравнения устарел или отменён; повторите поиск")
    rows.sort(
        key=lambda r: (
            r["error"] is not None,
            r.get("modeled_cost_pct")
            if r.get("modeled_cost_pct") is not None
            else r.get("loss_pct", D("Infinity")),
            -r.get("target_out", D(0)),
            r["pool"].address.lower(),
        )
    )
    return {
        "block": block,
        "amount": str(amount),
        "rows": rows,
        "excluded_other_pairs": len(pools) - len(candidates),
    }
