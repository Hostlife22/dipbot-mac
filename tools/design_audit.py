"""Offline native Qt visual matrix. Synthetic data; network, wallet and execution disabled."""

import argparse
import json
import math
import tempfile
import time
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QScrollArea

from dipbot.application.worker import Worker
from dipbot.domain.assets import WBNB
from dipbot.execution.trader import LiveTrader
from dipbot.market.chain import Chain, Pool, address
from dipbot.persistence.storage import Store
from dipbot.persistence.vault import Vault
from dipbot.ui.theme import STYLE
from dipbot.ui.usd_feed import UsdRate
from dipbot.ui.window import Window


def run(output):
    output.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    app.setStyleSheet(STYLE)
    report = {
        "synthetic": True,
        "platform": app.platformName(),
        "transactions_sent": 0,
        "screens": [],
        "checks": [],
    }

    def forbidden(*args, **kwargs):
        raise AssertionError("Network, keys and execution forbidden in design audit")

    pool = Pool(address("0x" + "12" * 20), "V2", address("0x" + "cd" * 20), address(WBNB), 18, 18, True)
    with (
        tempfile.TemporaryDirectory() as directory,
        patch.object(Worker, "start", lambda self: None),
        patch.object(Worker, "submit", forbidden),
        patch.object(Chain, "__init__", forbidden),
        patch.object(Vault, "get", forbidden),
        patch.object(Vault, "save", forbidden),
        patch.object(LiveTrader, "send", forbidden),
        patch.object(UsdRate, "refresh", lambda self: None),
    ):
        w = Window(Store(Path(directory) / "state.json"))
        w.setWindowTitle("DipBot · синтетическая проверка интерфейса")
        w.age_timer.stop()

        def events():
            for _ in range(3):
                app.processEvents()
                QTest.qWait(10)

        def status(**changes):
            data = dict(
                mode=w.mode.currentText(),
                running=True,
                locked=False,
                position="0",
                base="0.000000425",
                realized="0",
                levels={"DIP": "0.00000041225"},
                historical_usd={"value": "0.32", "closed": 2, "missing": 0, "includes_gas": False},
            )
            data.update(changes)
            w.on_event("status", data)

        def seed(mode="PAPER"):
            w.running = w.busy = w.stop_pending = w.searching = False
            w.display_position = Decimal(0)
            w.ui_error = ""
            w.worker.stop_event.clear()
            w.mode.setCurrentText(mode)
            w.on_event("selected", pool)
            w.price_source = "BSC" if mode != "DEMO" else "DEMO"
            w.display_unit = "WBNB"
            w.usd.token = WBNB.lower()
            w.usd.rate = Decimal("600")
            w.usd.received_at = time.monotonic()
            w.chart.clear()
            for i in range(150):
                w.chart.values.append(0.00000042 * (1 + 0.008 * math.sin(i / 9)))
                w.chart.times.append(time.monotonic() - (150 - i) * 0.1)
            w.last_price = Decimal(str(w.chart.values[-1]))
            w.last_quote_at = time.monotonic()
            status()
            w.update_quote_age()

        def snap(name, widget=None):
            events()
            target = widget or w
            path = output / (name + ".png")
            assert target.grab().save(str(path))
            report["screens"].append(name)
            assert w.footer.isVisible() and w.stop.isVisible()

        def geometry():
            viewport = w.tabs.widget(0).viewport()
            for widget in (w.chart, w.strategy_status):
                top = widget.mapTo(viewport, QPoint(0, 0))
                assert top.y() >= 0 and top.y() + widget.height() <= viewport.height(), (
                    w.size(),
                    widget.objectName(),
                    top.y(),
                    widget.height(),
                    viewport.height(),
                )
            for scroll in w.findChildren(QScrollArea):
                if scroll.isVisible():
                    assert scroll.horizontalScrollBar().maximum() == 0
            assert w.footer.font().pixelSize() <= 13

        w.show()
        events()
        for width, height in ((940, 700), (1100, 750), (1280, 800), (1440, 900)):
            w.resize(width, height)
            seed()
            events()
            w.tabs.widget(0).verticalScrollBar().setValue(0)
            events()
            geometry()
            snap(f"paper-{width}x{height}")
            report["checks"].append(f"chart/status/footer visible; no horizontal scroll {width}x{height}")
        w.resize(940, 700)
        seed()
        for offset in range(5, 60, 4):
            for shift, side in ((0, "BUY"), (1, "SELL")):
                i = -offset - shift
                w.chart.markers.append((w.chart.times[i], side, w.chart.values[i]))
        events()
        geometry()
        snap("dense-trades")
        for reason, notice in (
            ("rebound", "DIP достигнут · ждёт отскок 0.1% от минимума; сейчас ≈0.0999%"),
            ("cooldown", "Пауза после выхода: 3.0 с · затем новый DIP"),
        ):
            seed()
            status(
                wait_reason=reason, signal_notice=notice, base="0.00000044", levels={"DIP": "0.0000004268"}
            )
            geometry()
            snap("wait-" + reason)
        seed()
        status(
            entry_notice="Недостаточная активность: 0 Swap, нужно минимум 1; пауза 5 с, затем новый сигнал DIP"
        )
        geometry()
        snap("wait-activity")
        seed()
        from dipbot.application.trade_view import entry_view, exit_view

        detail = exit_view(
            entry_view(Decimal(1000000), Decimal(1), ".01", {"usd": "1"}),
            Decimal(1000000),
            Decimal("1.0059"),
            ".01",
            {"usd": "1"},
        )
        status(trade_detail=detail)
        w.trade_toggle.setChecked(True)
        events()
        snap("trade-costs")
        w.trade_toggle.setChecked(False)
        seed()
        status(
            position="1000000",
            levels={"ENTRY": ".000000416"},
            open_estimate={
                "at": time.monotonic(),
                "value_usd": "1.1",
                "pnl_usd": ".09",
                "excludes_exit_gas": False,
            },
        )
        events()
        geometry()
        snap("open-position-usd")
        status(
            running=False,
            position="100",
            open_estimate={
                "at": time.monotonic(),
                "value_usd": "1.01",
                "pnl_usd": "-.01",
                "excludes_exit_gas": False,
            },
        )
        events()
        geometry()
        snap("idle-position-usd")
        status(
            running=False,
            position="100",
            quote_unavailable=True,
            position_watch_error="Ошибка RPC · повтор чтения с паузой до 5 с",
        )
        events()
        geometry()
        snap("idle-position-rpc")
        seed("DEMO")
        status(running=False, base="0", levels={})
        snap("demo-stopped")
        seed()
        status(
            position="123456789.123456789",
            levels={"ENTRY": ".000000416", "TP": ".00000042432", "SL": ".00000040768", "TRAIL": ".000000415"},
        )
        snap("position")
        w.last_quote_at = time.monotonic() - 3
        w.update_quote_age()
        snap("stale")
        assert w.metrics["state"].text() == "STALE"
        status(quote_unavailable=True, position="123")
        snap("rpc-unavailable")
        assert w.metrics["state"].text() == "WAIT RPC"
        status(
            quote_unavailable=False,
            position="123",
            levels={"ENTRY": ".000000416", "TP": ".00000042432", "SL": ".00000040768"},
            exit_retry={"error": "HTTPError", "attempt": 2, "limit": 3, "retry_at": time.monotonic() + 2},
        )
        events()
        geometry()
        snap("exit-rpc")
        assert w.metrics["state"].text() == "EXIT RPC"
        status(exit_retry=None)
        w.stop_bot()
        snap("stopping")
        assert w.metrics["state"].text() == "STOPPING"
        seed()
        status(running=False, base="0", levels={})
        w.invalidate_discovery()
        w.searching = w.busy = True
        w.update_controls()
        snap("searching")
        w.searching = w.busy = False
        w.on_event("autopair", "PENDING")
        snap("pending")
        assert w.metrics["state"].text() == "PENDING"
        seed()
        status(running=False, base="0", levels={})
        w.market_toggle.setChecked(True)
        events()
        snap("autopair")
        w.market_toggle.setChecked(False)
        w.strategy_toggle.setChecked(True)
        events()
        scroll = w.tabs.widget(0)
        scroll.ensureWidgetVisible(w.signal_mode)
        snap("strategy")
        scroll.ensureWidgetVisible(w.exit_basis)
        snap("exit-settings")
        w.strategy_toggle.setChecked(False)
        for index, name in ((1, "rpc-settings"), (2, "assets-add-remove"), (3, "about")):
            w.tabs.setCurrentIndex(index)
            events()
            snap(name)
            assert w.tabs.widget(index).horizontalScrollBar().maximum() == 0
        w.tabs.setCurrentIndex(0)
        seed("LIVE")
        status(running=False, base="0", levels={})
        events()
        snap("live-ready")

        def capture_dialog(name):
            def capture():
                dialog = app.activeModalWidget()
                if dialog is None:
                    raise AssertionError("Expected real confirmation dialog")
                snap(name, dialog)
                dialog.reject()

            QTimer.singleShot(100, capture)

        capture_dialog("confirm-live-buy")
        w.trade("buy")
        capture_dialog("confirm-live-sweep")
        w.trade("sweep")
        capture_dialog("rpc-error-dialog")
        w.on_event("error", "Синтетический таймаут RPC. Проверьте подключение и повторите поиск.")
        snap("rpc-error")
        w.journal_toggle.setChecked(False)
        w.ui_error = ""
        w.store.data["operation"] = {
            "wallet": address("0x" + "34" * 20),
            "transactions": [{"hash": "0x" + "56" * 32, "status": "pending", "stage": "submitted"}],
        }
        status(running=False, locked=True)
        snap("live-locked")
        assert not w.start.isEnabled()
        w.show_recovery()
        events()
        snap("recovery")
        # UI-only profile creation/removal, no worker execution or chain mutation.
        from dataclasses import replace

        from dipbot.persistence import dynamic

        custom = replace(pool, quote=address("0x" + "ab" * 20))
        conversion = [replace(pool, token=custom.quote)]
        row = dynamic.upsert(w.store, custom, conversion, 100, symbol="UI_TEST")
        w.on_event("profiles", w.store.data["dynamic_profiles"])
        w.tabs.setCurrentIndex(2)
        snap("custom-profile-added")
        dynamic.remove(w.store, row["name"])
        w.on_event("profiles", w.store.data["dynamic_profiles"])
        snap("custom-profile-removed")
        # Keyboard focus is tested in the real native widget tree.
        w.store.data.pop("operation", None)
        w.tabs.setCurrentIndex(0)
        seed("DEMO")
        status(running=False, base="0", levels={})
        w.raise_()
        w.activateWindow()
        events()
        QTest.qWait(200)
        w.mode.setFocus()
        events()
        QTest.keyClick(w.mode, Qt.Key_Tab)
        events()
        assert app.focusWidget() is not None and app.focusWidget() != w.mode
        report["checks"].append("keyboard Tab focus advances")
        w.running = w.busy = w.stop_pending = False
        w.display_position = Decimal(0)
        w.close()
        events()
        reopened = Window(Store(Path(directory) / "state.json"))
        assert reopened.params["dip"].text() == w.params["dip"].text()
        reopened.close()
        reopened.deleteLater()
        w.deleteLater()
        events()
        report["checks"].append("settings survive reopen")
        report["passed"] = True
        (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False), flush=True)
    app.quit()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    run(parser.parse_args().output)
