from decimal import Decimal as D

from dipbot.domain.strategy import Settings, Strategy
from tests.support.markets import POOL


def test_market_and_mode_changes_clear_quotes(window):
    w = window
    w.mode.setCurrentText("PAPER")
    w.on_event("selected", POOL)
    w.on_event("price", "0.00000001234")
    assert len(w.chart.values) == 1
    w.invalidate_discovery()
    assert not w.chart.values and w.metrics["price"].text() == "—"
    w.on_event("price", "42")
    assert not w.chart.values  # obsolete result cannot restore invalidated market
    w.on_event("selected", POOL)
    w.on_event("price", "0.00000001234")
    w.mode.setCurrentText("DEMO")
    assert not w.chart.values and w.last_quote_at is None


def test_display_levels_use_worker_settings_and_position(window):
    w = window
    w.mode.setCurrentText("PAPER")
    w.on_event("selected", POOL)
    worker = w.worker
    worker.mode = "PAPER"
    worker.running = True
    worker.strategy = Strategy(Settings(dip=D(3), take_profit=D(2), stop_loss=D(2)))
    worker.strategy.base = D("1e-12")
    worker.status()
    assert D(w.chart.levels["DIP"]) == D("0.97e-12")
    assert not w.start.isEnabled()
    worker.strategy.entry = D("0.96e-12")
    worker.paper.position = D("123456789.0123456789")
    worker.status()
    assert set(w.chart.levels) == {"ENTRY", "TP", "SL"}
    assert D(w.chart.levels["TP"]) == D("0.9792e-12")
    assert D(w.chart.levels["SL"]) == D("0.9408e-12")
    assert w.metrics["position"].toolTip() == str(worker.paper.position)
    worker.strategy.entry = None
    worker.paper.position = D(0)
    worker.running = False
    worker.status()
    assert not w.chart.levels and w.metrics["position"].text() == "0"
    assert w.start.isEnabled()


def test_quote_source_change_discards_preview_and_age_is_visible(window, monkeypatch):
    w = window
    w.on_event("selected", POOL)
    w.on_event("price_context", {"source": "BSC", "quote": POOL.quote})
    w.on_event("price", "1e-12")
    assert "BSC / RPC" in w.quote_age.text()
    w.on_event("price_context", {"source": "DEMO", "quote": ""})
    assert not w.chart.values
    w.on_event("price", "1")
    w.running = True
    monkeypatch.setattr("dipbot.ui.window.time.monotonic", lambda: w.last_quote_at + 2)
    w.update_quote_age()
    assert "локальная модель DEMO" in w.quote_age.text()
    assert "2.0 с назад" in w.quote_age.text() and "> 0.55" in w.quote_age.text()


def test_strategy_distances_and_stale_quote(window, monkeypatch):
    w = window
    w.on_event("price", "100")
    w.on_event(
        "status",
        {
            "running": True,
            "mode": "DEMO",
            "locked": False,
            "position": "0",
            "base": "100",
            "realized": "0",
            "levels": {"DIP": "97"},
        },
    )
    assert "Ждёт падения" in w.strategy_status.text()
    assert "до DIP: 3.00%" in w.strategy_status.text()
    w.on_event(
        "status",
        {
            "running": True,
            "mode": "DEMO",
            "locked": False,
            "position": "2",
            "base": "100",
            "realized": "0",
            "levels": {"ENTRY": "100", "TP": "102", "SL": "98"},
        },
    )
    assert "Позиция открыта" in w.strategy_status.text()
    assert "до TP: 2.00%" in w.strategy_status.text()
    assert "до SL: 2.00%" in w.strategy_status.text()
    monkeypatch.setattr("dipbot.ui.window.time.monotonic", lambda: w.last_quote_at + 1)
    w.update_quote_age()
    assert "устарела" in w.strategy_status.text()
    assert "до TP" not in w.strategy_status.text()
    w.mode.setCurrentText("PAPER")
    assert not w.chart.levels and w.last_price is None


def test_trade_markers_clear_with_market_and_reject_other_mode(window):
    w = window
    w.on_event("price", "100")
    w.on_event("trade_marker", {"mode": "LIVE", "side": "BUY", "price": "100"})
    assert not w.chart.markers
    w.on_event("trade_marker", {"mode": "DEMO", "side": "BUY", "price": "100"})
    w.on_event("price", "102")
    w.on_event("trade_marker", {"mode": "DEMO", "side": "SELL", "price": "102"})
    assert [m[1] for m in w.chart.markers] == ["BUY", "SELL"]
    w.reset_price_display()
    assert not w.chart.markers and not w.chart.values
