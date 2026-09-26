import time
from decimal import Decimal as D

from dipbot.domain.assets import WBNB
from dipbot.execution.accounting import RateBook, closed_summary, operation_fees, record_close
from dipbot.persistence.storage import Store
from tests.support.execution import Function
from tests.support.markets import POOL


def test_rates_expire_and_saved_marks_are_immutable(monkeypatch):
    now = [10.0]
    monkeypatch.setattr("dipbot.execution.accounting.time.monotonic", lambda: now[0])
    book = RateBook()
    book.update(WBNB, D(800), 10)
    mark = book.snapshot(WBNB)
    book.update(WBNB, D(900), 10)
    assert mark["usd"] == "800"
    now[0] = 101
    assert book.snapshot(WBNB) is None


def test_complete_fees_and_missing_receipts_are_distinct():
    assert operation_fees(None)["usd"] is None
    operation = {
        "transactions": [{"gas_fee_wei": 100, "gas_usd": ".01"}, {"gas_fee_wei": 200, "gas_usd": ".02"}]
    }
    assert operation_fees(operation) == {"wei": "300", "usd": "0.03"}
    operation["transactions"][1].pop("gas_usd")
    assert operation_fees(operation) == {"wei": "300", "usd": None}


def test_receipt_records_gas_usd_in_persistent_ledger(trader):
    trader.rates = RateBook()
    trader.rates.update(WBNB, D(800), time.monotonic())
    wait = trader.chain.w3.eth.wait_for_transaction_receipt
    trader.chain.w3.eth.wait_for_transaction_receipt = lambda *a, **k: (
        wait(*a, **k) | {"gasUsed": 21000, "effectiveGasPrice": 10**8}
    )
    trader.begin("BUY")
    trader.send(Function(), "BUY")
    row = trader.operation["transactions"][0]
    assert D(row["gas_usd"]) == D(".00168")
    saved = Store(trader.store.path).data["gas_ledger"][row["hash"]]
    assert saved["usd"] == row["gas_usd"]


def test_closed_usd_uses_entry_exit_marks_and_is_idempotent(tmp_path):
    store = Store(tmp_path / "state.json")
    owner = "0x123"
    position = {"entry_cost_usd": "80.01", "cost_quote": ".1"}
    operation = {"transactions": [{"hash": "synthetic", "gas_fee_wei": 1, "gas_usd": ".02"}]}
    rate = {"usd": "900"}
    record_close(store, owner, POOL, position, 10**17, operation, rate)
    record_close(store, owner, POOL, position, 10**17, operation, rate)
    assert len(store.data["closed_trades"]) == 1
    assert closed_summary(store, owner)["value"] == "9.97"
    operation["transactions"][0]["hash"] = "missing"
    position["entry_cost_usd"] = None
    record_close(store, owner, POOL, position, 10**17, operation, rate)
    assert closed_summary(store, owner)["value"] is None
    assert closed_summary(store, owner)["missing"] == 1


def test_historical_footer_does_not_change_with_current_rate(window):
    w = window
    w.mode.setCurrentText("LIVE")
    payload = {
        "running": False,
        "mode": "LIVE",
        "locked": False,
        "position": "0",
        "base": "0",
        "realized": "1",
        "pnl_quote": POOL.quote,
        "levels": {},
        "historical_usd": {"value": "9.97", "closed": 1, "missing": 0, "includes_gas": True},
    }
    w.on_event("status", payload)
    assert "+$9.97" in w.footer.text() and "газ BUY/SELL учтён" in w.footer.text()
    w.usd.token = POOL.quote.lower()
    w.usd.rate = D(9999)
    w.usd.received_at = time.monotonic()
    w.refresh_pnl()
    assert "+$9.97" in w.footer.text() and "BNB" not in w.footer.text()
    w.on_event(
        "status",
        payload | {"historical_usd": {"value": None, "closed": 1, "missing": 1, "includes_gas": True}},
    )
    assert "неполный USD" in w.footer.text()


def test_invalid_receipt_gas_is_not_accepted_as_financial_data(trader):
    import pytest

    from dipbot.execution.errors import UncertainTransaction

    trader.begin("BUY")
    wait = trader.chain.w3.eth.wait_for_transaction_receipt
    trader.chain.w3.eth.wait_for_transaction_receipt = lambda *a, **k: (
        wait(*a, **k) | {"gasUsed": "21000", "effectiveGasPrice": 10**8}
    )
    with pytest.raises(UncertainTransaction):
        trader.send(Function(), "BUY")
    assert not trader.store.data.get("gas_ledger") and trader.store.data["operation"]


def test_inventory_shortfall_cannot_produce_complete_usd_result(tmp_path):
    store = Store(tmp_path / "state.json")
    record_close(
        store,
        "owner",
        POOL,
        {"entry_cost_usd": "1", "cost_quote": ".1"},
        10**17,
        {"transactions": [{"hash": "synthetic", "gas_fee_wei": 1, "gas_usd": ".01"}]},
        {"usd": "1"},
        inventory_matches=False,
    )
    assert closed_summary(store, "owner")["missing"] == 1
