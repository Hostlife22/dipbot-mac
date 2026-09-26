"""Explicit USD marks, complete/missing accounting and fresh FX for optional sizing/cost filters."""

import threading
import time
from decimal import Decimal as D


class RateBook:
    def __init__(self):
        self.lock = threading.Lock()
        self.rates = {}

    def update(self, token, value, received_at):
        if not token or value is None or received_at is None:
            return
        rate = D(str(value))
        if not rate.is_finite() or rate <= 0 or not 0 <= time.monotonic() - received_at <= 90:
            return
        with self.lock:
            if len(self.rates) >= 64:
                self.rates = {k: v for k, v in self.rates.items() if time.monotonic() - v["monotonic"] <= 90}
            self.rates[token.lower()] = {
                "usd": str(rate),
                "monotonic": received_at,
                "observed_at": time.time() - (time.monotonic() - received_at),
                "source": "DEX Screener",
            }

    def snapshot(self, token):
        with self.lock:
            row = self.rates.get(token.lower())
            if row is None or not 0 <= time.monotonic() - row["monotonic"] <= 90:
                return None
            return {k: v for k, v in row.items() if k != "monotonic"}


def marked_value(amount, rate):
    return str(D(amount) * D(rate["usd"])) if rate is not None else None


def operation_fees(operation):
    rows = operation.get("transactions", []) if operation else []
    # Empty/missing receipts must never masquerade as zero fees.
    if not rows or any("gas_fee_wei" not in row for row in rows):
        return {"wei": None, "usd": None}
    wei = sum(row["gas_fee_wei"] for row in rows)
    usd = (
        sum((D(row["gas_usd"]) for row in rows), D(0))
        if all(row.get("gas_usd") is not None for row in rows)
        else None
    )
    return {"wei": str(wei), "usd": str(usd) if usd is not None else None}


def record_gas(store, owner, record):
    if "gas_fee_wei" not in record:
        return
    store.ledger("gas_ledger")[record["hash"]] = {
        "wallet": owner.lower(),
        "label": record.get("label", "unknown"),
        "wei": str(record["gas_fee_wei"]),
        "usd": record.get("gas_usd"),
        "rate": record.get("gas_usd_rate"),
        "block": record.get("block"),
        "status": record["status"],
    }


def closed_summary(store, owner):
    ledger = store.ledger("closed_trades")

    def calculate():
        rows = [r for r in ledger.values() if r["wallet"] == owner.lower()]
        missing = sum(row.get("net_usd") is None for row in rows)
        total = sum((D(row["net_usd"]) for row in rows if row.get("net_usd") is not None), D(0))
        return {
            "value": str(total) if rows and not missing else None,
            "closed": len(rows),
            "missing": missing,
            "includes_gas": True,
            "scope": "tracked_positions_since_usd_accounting",
        }

    return ledger.summary(owner.lower(), calculate)


def record_close(store, owner, pool, position, received, operation, exit_rate, *, inventory_matches=True):
    rows = operation.get("transactions", []) if operation else []
    if not rows:
        return  # Compatibility with synthetic execution adapters; no fabricated identifier.
    identifier = rows[-1]["hash"]
    proceeds = D(received) / D(10) ** pool.quote_decimals
    proceeds_usd = marked_value(proceeds, exit_rate)
    fees = operation_fees(operation)
    entry_usd = position.get("entry_cost_usd")
    net = (
        D(proceeds_usd) - D(entry_usd) - D(fees["usd"])
        if inventory_matches
        and proceeds_usd is not None
        and entry_usd is not None
        and fees["usd"] is not None
        else None
    )
    store.ledger("closed_trades").setdefault(
        identifier,
        {
            "wallet": owner.lower(),
            "pool": pool.address,
            "token": pool.token,
            "quote": pool.quote,
            "closed_at": int(time.time()),
            "inventory_matches": inventory_matches,
            "cost_quote": position.get("cost_quote"),
            "proceeds_quote": str(proceeds),
            "entry_cost_usd": entry_usd,
            "entry_gas_hashes": position.get("entry_gas_hashes"),
            "exit_gas_hashes": [row["hash"] for row in rows],
            "proceeds_usd": proceeds_usd,
            "exit_rate": exit_rate,
            "exit_fees": fees,
            "net_usd": str(net) if net is not None else None,
        },
    )


def expense_summary(store, owner):
    closed = [r for r in store.data.get("closed_trades", {}).values() if r["wallet"] == owner.lower()]
    gas = {h: r for h, r in store.data.get("gas_ledger", {}).items() if r["wallet"] == owner.lower()}
    allocated = set()
    complete = all(row.get("net_usd") is not None for row in closed)
    for row in closed:
        entry, exit_ = row.get("entry_gas_hashes"), row.get("exit_gas_hashes")
        if not isinstance(entry, list) or not isinstance(exit_, list) or not entry or not exit_:
            complete = False
            continue
        hashes = set(entry + exit_)
        if not hashes <= gas.keys() or allocated & hashes:
            complete = False
        allocated.update(hashes)
    other = [r for h, r in gas.items() if h not in allocated]
    missing = sum(row.get("usd") is None for row in other)
    known = sum((D(row["usd"]) for row in other if row.get("usd") is not None), D(0))
    realized = sum((D(row["net_usd"]) for row in closed if row.get("net_usd") is not None), D(0))
    return {
        "unallocated_gas_usd": str(known) if complete and not missing else None,
        "unallocated_known_usd": str(known),
        "unallocated_missing": missing,
        "realized_less_other_gas_usd": str(realized - known) if complete and not missing else None,
        "allocation_complete": complete,
        "scope": "tracked_closed_positions_less_all_other_recorded_gas_not_portfolio_nav",
    }


def accounting_report(store, owner):
    summary = closed_summary(store, owner)
    expenses = expense_summary(store, owner)
    rows = [r for r in store.data.get("gas_ledger", {}).values() if r["wallet"] == owner.lower()]
    missing = sum(row.get("usd") is None for row in rows)
    known = sum((D(row["usd"]) for row in rows if row.get("usd") is not None), D(0))
    lines = [
        "USD-учёт с момента его включения; старые сделки не восстановлены.",
        f"Закрытых позиций: {summary['closed']}; неполных: {summary['missing']}.",
        "P&L закрытых позиций: "
        + (
            summary["value"] + " USD (газ BUY/SELL учтён)"
            if summary["value"] is not None
            else "недостаточно данных"
        ),
        f"Газ всех записанных транзакций: {len(rows)} receipts; без USD-курса: {missing}.",
        f"Известная часть расходов газа: {known} USD.",
        "Газ Converter/Sweep/revert/CANCEL и открытых позиций включён в общий журнал.",
        "Прочий газ, не включённый в P&L закрытий: "
        + (
            expenses["unallocated_gas_usd"] + " USD"
            if expenses["unallocated_gas_usd"] is not None
            else "неполное распределение / USD-курсы"
        ),
        "Закрытия минус прочий газ: "
        + (
            expenses["realized_less_other_gas_usd"] + " USD"
            if expenses["realized_less_other_gas_usd"] is not None
            else "недостаточно данных"
        ),
        "Это не NAV портфеля: открытые активы, внешние переводы и изменение стоимости BASE не переоцениваются.",
        "Курсы — ориентировочные отметки DEX Screener, а не точная цена фиатного исполнения.",
    ]
    recent = [r for r in store.data.get("closed_trades", {}).values() if r["wallet"] == owner.lower()][-20:]
    for row in reversed(recent):
        value = row.get("net_usd")
        lines.append(row["token"] + " · " + (value + " USD" if value is not None else "неполный учёт"))
    return "\n".join(lines)


def record_sweep_exit(
    store,
    owner,
    token,
    sold,
    residual,
    proceeds,
    quote,
    quote_decimals,
    operation,
    exit_rate,
    *,
    pool_address="",
):
    """Aggregate tracked lots once per Sweep receipt; mismatches remain incomplete."""
    from types import SimpleNamespace

    positions = [
        p
        for key, p in store.data.get("positions", {}).items()
        if key.startswith(owner.lower() + ":") and p["pool"]["token"].lower() == token.lower()
    ]
    if not positions or type(proceeds) is not int or proceeds <= 0:
        return
    total = sum(p["amount"] for p in positions)
    complete = residual == 0 and total == sold and all(p.get("entry_cost_usd") is not None for p in positions)
    entry_usd = sum((D(p["entry_cost_usd"]) for p in positions), D(0)) if complete else None
    same_quote = all(p["pool"]["quote"].lower() == quote.lower() and "cost_quote" in p for p in positions)
    combined = {
        "entry_cost_usd": str(entry_usd) if entry_usd is not None else None,
        "cost_quote": str(sum((D(p["cost_quote"]) for p in positions), D(0))) if same_quote else None,
    }
    gas_groups = [p.get("entry_gas_hashes") for p in positions]
    combined["entry_gas_hashes"] = (
        sorted({h for group in gas_groups for h in group})
        if all(isinstance(g, list) and g for g in gas_groups)
        else None
    )
    pool = SimpleNamespace(address=pool_address, token=token, quote=quote, quote_decimals=quote_decimals)
    record_close(
        store,
        owner,
        pool,
        combined,
        proceeds,
        operation,
        exit_rate,
        inventory_matches=total == sold and residual == 0,
    )
    rows = operation.get("transactions", []) if operation else []
    if rows:
        close = store.data["closed_trades"][rows[-1]["hash"]]
        close.update(
            source="SWEEP",
            tracked_raw=total,
            sold_raw=sold,
            residual_raw=residual,
            source_pools=[p["pool"]["address"] for p in positions],
        )
