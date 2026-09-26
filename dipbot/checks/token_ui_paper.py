"""Visible, isolated PAPER check on a specified live BSC market; no wallet access."""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal as D
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from web3.types import RPCEndpoint

    from dipbot.execution.trader import LiveTrader
    from dipbot.market.chain import Chain, Pool

import json
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from unittest.mock import patch

from PySide6.QtGui import QStandardItemModel
from PySide6.QtWidgets import QApplication, QMessageBox, QPushButton, QScrollArea

from dipbot.checks.read_only import guard_provider
from dipbot.execution.trader import LiveTrader
from dipbot.market.chain import Chain
from dipbot.persistence.storage import Store
from dipbot.persistence.vault import Vault
from dipbot.ui.theme import STYLE
from dipbot.ui.window import Window


def run(
    token: str,
    directory: Path,
    seconds: float,
    pool_address: str | None = None,
    exercise_recovery: bool = False,
    close_after: bool = False,
    modern: bool = False,
    amount_usd: Any = None,
    automatic_only: bool = False,
    fee_usd: str = "0.01",
    adaptive_rpc: bool = False,
    endpoint: str = "https://bsc-dataseed.binance.org",
    take_profit: str = "2",
    stop_loss: str = "2",
    backup_rpc: str = "",
    observe_manual_position: bool = False,
    min_swaps: str = "1",
    dip: Any = None,
    slippage: Any = None,
    dynamic: str | None = None,
    continue_after_sl: Any = None,
    cooldown: Any = None,
    trailing: Any = None,
    preflight_fault: Any = None,
) -> int | None:
    if automatic_only and (exercise_recovery or observe_manual_position):
        raise ValueError("Autonomous audit cannot inject signals or restart the strategy")
    if not D(fee_usd).is_finite() or not 0 <= D(fee_usd) <= 1:
        raise ValueError("Invalid PAPER fee model")
    directory.mkdir(parents=True, exist_ok=False)
    app_instance = QApplication.instance()
    assert app_instance is None or isinstance(app_instance, QApplication)
    app = app_instance or QApplication([])
    app.setStyleSheet(STYLE)
    app.setQuitOnLastWindowClosed(False)
    report: dict[str, Any] = {
        "token": token,
        "utc": datetime.now(timezone.utc).isoformat(),
        "mode": "PAPER",
        "frozen": bool(getattr(sys, "frozen", False)),
        "transactions_sent": 0,
        "checks": 0,
        "mismatches": [],
        "errors": [],
        "samples": [],
        "trades": [],
        "test_restarts": 0,
        "automatic_only": automatic_only,
        "source": "live BSC RPC, no replay",
    }
    report["preflight_fault_requested"] = preflight_fault
    report["rpc_failures"] = []
    logs: list[str] = []
    rpc: list[Counter[str]] = []
    fills: list[dict[str, Any]] = []
    original_quote = Chain.paper_quote

    def paper_quote(chain: Chain, pool: Pool, amount: int, buy: Any) -> Any:
        result = original_quote(chain, pool, amount, buy)
        fills.append(
            {
                "amount_raw": amount,
                "received_raw": result,
                "buy": buy,
                "token_decimals": pool.token_decimals,
                "quote_decimals": pool.quote_decimals,
            }
        )
        return result

    phase = ["setup"]
    original = Chain.__init__

    def init(chain: Chain, endpoint: str, **kwargs: Any) -> None:
        original(chain, endpoint, **kwargs)
        rpc.append(guard_provider(chain.w3.provider))
        request = chain.w3.provider.make_request

        def observed_request(method: RPCEndpoint, params: Any) -> Any:
            def record(*, exc: BaseException | None = None, response: Any = None) -> None:
                item = {"method": str(method), "phase": phase[0], "at": time.monotonic()}
                if exc is not None:
                    item["type"] = type(exc).__name__
                    code = getattr(getattr(exc, "response", None), "status_code", None)
                    if type(code) is int:
                        item["http_status"] = code
                error = response.get("error") if isinstance(response, dict) else None
                code = error.get("code") if isinstance(error, dict) else None
                if type(code) is int:
                    item["rpc_code"] = code
                if len(report["rpc_failures"]) < 200:
                    report["rpc_failures"].append(item)
                else:
                    report["rpc_failures_dropped"] = report.get("rpc_failures_dropped", 0) + 1

            try:
                result = request(method, params)
            except Exception as exc:
                record(exc=exc)
                raise
            if isinstance(result, dict) and result.get("error"):
                record(response=result)
            return result

        setattr(chain.w3.provider, "make_request", observed_request)

    original_entry_quote = Chain.entry_quote

    def entry_quote(chain: Chain, *args: Any, **kwargs: Any) -> Any:
        if (
            preflight_fault
            and phase[0] == "automatic_live_prices"
            and not report.get("preflight_fault_injected")
        ):
            report["preflight_fault_injected"] = {
                "kind": preflight_fault,
                "at": time.monotonic(),
                "synthetic": True,
            }
            if preflight_fault == "http429":
                from requests import Response
                from requests.exceptions import HTTPError

                response = Response()
                response.status_code = 429
                raise HTTPError("Synthetic PAPER preflight limit", response=response)
            from web3.exceptions import Web3RPCError

            raise Web3RPCError(
                "Synthetic PAPER preflight limit",
                rpc_response={"error": {"code": -32005, "message": "synthetic rate limit"}},
            )
        return original_entry_quote(chain, *args, **kwargs)

    def forbidden(*a: Any, **kw: Any) -> None:
        raise RuntimeError("Wallet/LIVE disabled in PAPER test")

    with (
        patch.object(Chain, "__init__", init),
        patch.object(Chain, "paper_quote", paper_quote),
        patch.object(Chain, "entry_quote", entry_quote),
        patch.object(Vault, "get", forbidden),
        patch.object(Vault, "save", forbidden),
        patch.object(LiveTrader, "send", forbidden),
        patch.object(QMessageBox, "warning", lambda *a: report["errors"].append(a[2])),
    ):
        w = Window(Store(directory / "state.json"))
        w.setWindowTitle("DipBot · видимая проверка PAPER · " + token[:10])
        w.mode.setCurrentText("PAPER")
        # This dedicated test window cannot switch to LIVE.
        model = w.mode.model()
        assert isinstance(model, QStandardItemModel)
        model.item(2).setEnabled(False)
        w.worker.log.connect(logs.append)
        w.show()
        w.raise_()
        w.activateWindow()

        def pump() -> None:
            app.processEvents()
            time.sleep(0.01)

        def wait(condition: Callable[[], bool], timeout: float = 120) -> None:
            start = time.monotonic()
            while not condition():
                pump()
                if time.monotonic() - start > timeout:
                    raise TimeoutError("GUI operation timed out")

        def capture(name: str) -> None:
            w.tabs.setCurrentIndex(0)
            tab = w.tabs.widget(0)
            assert isinstance(tab, QScrollArea)
            tab.verticalScrollBar().setValue(0)
            app.processEvents()
            w.grab().save(str(directory / (name + ".png")))

        def check(kind: str, payload: Any) -> None:
            if kind == "price":
                report["checks"] += 1
                report["samples"].append({"time": time.monotonic(), "price": payload, "phase": phase[0]})
                # on_event may consume a newer read-only monitor snapshot while
                # handling this queued event. Compare the displayed snapshot,
                # not an event which has legitimately been superseded.
                displayed = w.last_price
                if displayed != D(payload):
                    report["monitor_superseded_price_events"] = (
                        report.get("monitor_superseded_price_events", 0) + 1
                    )
                if (
                    displayed is None
                    or w.metrics["price"].text() != w.display_price(displayed)
                    or not w.chart.values
                    or w.chart.values[-1] != float(displayed)
                ):
                    report["mismatches"].append(
                        {
                            "kind": "price / chart",
                            "phase": phase[0],
                            "expected": w.display_price(displayed),
                            "actual": w.metrics["price"].text(),
                            "chart": w.chart.values[-1] if w.chart.values else None,
                            "raw": payload,
                            "source": w.price_source,
                            "selected": w.selection_ready,
                        }
                    )
            elif kind == "status":
                if report.get("preflight_fault_injected"):
                    if w.entry_notice:
                        report["preflight_notice_seen"] = True
                    elif report.get("preflight_notice_seen") and payload.get("running"):
                        report["preflight_recovered_without_start"] = True
                report.setdefault("ui_states", {})[w.metrics["state"].text()] = (
                    report.setdefault("ui_states", {}).get(w.metrics["state"].text(), 0) + 1
                )
                report["checks"] += 1
                if payload.get("open_estimate") and D(payload["position"]) > 0:
                    report["open_estimate_observations"] = report.get("open_estimate_observations", 0) + 1
                if w.metrics["position"].text() != f"{float(payload['position']):.8g}":
                    report["mismatches"].append("target quantity")
                expected = payload["levels"] if payload["running"] or D(payload["position"]) > 0 else {}
                expected = {k: v for k, v in expected.items() if D(v) > 0}
                if w.chart.levels != expected:
                    report["mismatches"].append("strategy levels")
                if w.chart.reference_base != w.base_price:
                    report["mismatches"].append("DIP chart reference differs from metric")
            elif kind == "trade_marker":
                report["trades"].append(dict(payload, phase=phase[0]))
                detail = dict(w.worker.trade_detail or {})
                report.setdefault("trade_views", []).append(
                    {
                        "side": payload["side"],
                        "detail": detail,
                        "closed_usd": str(w.worker.paper_usd["value"]),
                        "closed_count": w.worker.paper_usd["closed"],
                        "ui_details": w.trade_details.text(),
                        "ui_open": w.position_estimate.text(),
                        "footer": w.footer.text(),
                    }
                )
                if payload["side"] == "SELL":
                    if (
                        w.worker.open_estimate is not None
                        or w.position_estimate.text() != "Открытая позиция, USD: —"
                    ):
                        report["mismatches"].append("open estimate survived SELL")
                    closes = [r for r in report["trade_views"] if r["side"] == "SELL"]
                    if len(closes) != w.worker.paper_usd["closed"]:
                        report["mismatches"].append("closed counter differs from SELL markers")
                    if all(r["detail"].get("net_usd") is not None for r in closes):
                        if (
                            sum((D(r["detail"]["net_usd"]) for r in closes), D(0))
                            != w.worker.paper_usd["value"]
                        ):
                            report["mismatches"].append("trade USD details differ from ledger")
                if (w.display_position > 0) != (payload["side"] == "BUY"):
                    report["mismatches"].append("trade marker precedes settled position display")
                if payload["side"] == "BUY":
                    assert fills and fills[-1]["buy"]
                    expected = D(fills[-1]["received_raw"]) / D(10) ** fills[-1]["token_decimals"]
                    if w.worker.paper.position != expected:
                        report["mismatches"].append("PAPER amount differs from router quote")
            elif kind == "price_context":
                sources = report.setdefault("price_sources", {})
                sources[payload["source"]] = sources.get(payload["source"], 0) + 1

        w.worker.event.connect(check)
        try:
            w.rpc.setText(endpoint)
            w.save_rpc.setChecked(False)
            w.adaptive_rpc.setChecked(adaptive_rpc)
            w.backup_rpc.setText(backup_rpc)
            next(b for b in w.findChildren(QPushButton) if b.text() == "Подключить").click()
            wait(lambda: not w.busy)
            w.token.setText(token)
            w.router.setCurrentText("AUTO")
            w.quote.setCurrentText("WBNB")
            w.market_toggle.setChecked(True)
            if pool_address:
                w.pool_input.setText(pool_address)
                next(b for b in w.findChildren(QPushButton) if b.text() == "CHECK POOL").click()
            else:
                w.send("discover", token=token, quote="WBNB", router="AUTO")
            wait(lambda: not w.busy)
            report["candidates"] = [w.candidates.itemText(i) for i in range(w.candidates.count())]
            if not w.selection_ready and w.candidates.count():
                w.candidates.setCurrentIndex(0)
                w.select_pool()
                wait(lambda: not w.busy)
            assert w.selection_ready, report["errors"] or w.pool_label.text()
            pool = w.worker.market.selected
            report["pool"] = pool.address
            report["router"] = pool.router
            report["token_decimals"] = pool.token_decimals
            w.market_toggle.setChecked(False)
            if amount_usd is not None:
                if not D(amount_usd).is_finite() or not 0 < D(amount_usd) <= 1:
                    raise ValueError("Audit USD amount must be in (0, 1]")
                w.amount_unit.setCurrentIndex(w.amount_unit.findData("usd"))
                w.params["amount"].setText(str(amount_usd))
            else:
                w.params["amount"].setText("0.00003")
            report["sizing"] = w.sizing_policy()
            report["quote_token"] = pool.quote
            report["paper_policy"] = w.paper_policy()
            w.params["dip"].setText("3")
            w.params["take_profit"].setText(take_profit)
            w.params["stop_loss"].setText(stop_loss)
            w.interval.setValue(0.1)
            if modern:
                w.signal_mode.setCurrentIndex(w.signal_mode.findData("window"))
                w.signal_rebound.setValue(0.1)
                w.params["dip"].setText("0.5")
                w.params["min_swaps"].setText(min_swaps)
                w.exit_basis.setCurrentIndex(w.exit_basis.findData("quote"))
                w.exit_fields["max_hold_seconds"].setValue(60)
                w.exit_fields["cooldown_seconds"].setValue(3)
                if automatic_only:
                    w.continue_after_exit.setChecked(True)
                    w.exit_fields["trailing_pct"].setValue(0.75)
            for field, value in (("dip", dip), ("slippage", slippage), ("dynamic", dynamic)):
                if value is not None:
                    w.params[field].setText(str(value))
            if continue_after_sl is not None:
                w.continue_after_exit.setChecked(continue_after_sl)
            for field, value in (("cooldown_seconds", cooldown), ("trailing_pct", trailing)):
                if value is not None:
                    control = w.exit_fields[field]
                    if not D(str(value)).is_finite() or not control.minimum() <= value <= control.maximum():
                        raise ValueError(f"{field} is outside the UI range")
                    control.setValue(value)
            report["settings"] = {k: v.text() for k, v in w.params.items()}
            report["signal_policy"] = w.signal_policy()
            report["exit_policy"] = w.exit_policy()
            report["adaptive_rpc"] = adaptive_rpc
            report["interval"] = w.interval.value()
            if amount_usd is not None or D(fee_usd):
                wait(lambda: w.worker.rates.snapshot(pool.quote) is not None, 60)
            if D(fee_usd):
                rate_snapshot = w.worker.rates.snapshot(pool.quote)
                assert rate_snapshot is not None, "USD rate expired during PAPER setup"
                rate = D(rate_snapshot["usd"])
                w.paper_fee.setText(str(D(fee_usd) / rate))
            report["paper_policy"] = w.paper_policy()
            report["fee_usd_at_start"] = fee_usd
            # Manual PAPER actions are explicitly separate from natural strategy signals.
            if not automatic_only:
                phase[0] = "manual_paper_buy"
                w.banner.setText("PAPER · ПРОВЕРКА BUY NOW · реальная цена BSC, виртуальная покупка")
                w.buy.click()
                wait(lambda: not w.busy)
                assert w.worker.paper.position > 0, report["errors"]
                capture("manual_buy")
                estimates_before = report.get("open_estimate_observations", 0)
                until = time.monotonic() + 12
                while time.monotonic() < until:
                    pump()
                if observe_manual_position:
                    assert not w.running, "Manual monitoring must not start the strategy"
                    assert report.get("open_estimate_observations", 0) > estimates_before
                    report["idle_position_monitor_passed"] = True
                    capture("manual_open_estimate")
                phase[0] = "manual_paper_sell"
                if observe_manual_position:
                    w.stop.click()
                    wait(lambda: not w.stop_pending and not w.busy)
                else:
                    w.sell.click()
                    wait(lambda: not w.busy)
                assert not w.worker.paper.position, report["errors"]
                report["manual_realized"] = str(w.worker.paper.realized)
                capture("manual_sell")
            phase[0] = "automatic_live_prices"
            w.banner.setText(
                "PAPER · НАБЛЮДЕНИЕ РЕАЛЬНОГО РЫНКА · DIP "
                + w.params["dip"].text()
                + "% / TP "
                + take_profit
                + "% / SL "
                + stop_loss
                + "%"
            )
            w.start.click()
            wait(lambda: not w.busy)
            report["effective_interval"] = w.worker.interval
            began = progress = time.monotonic()
            restart_after = 0.0
            original_price = w.worker.connections.reader.price
            original_backup_price = w.worker.backup_chain.price if w.worker.backup_chain else None
            outage_started = outage_finished = stop_restart_done = False

            def timeout(_: Any) -> None:
                raise TimeoutError("Synthetic read-only failure")

            while time.monotonic() - began < seconds:
                pump()
                elapsed = time.monotonic() - began
                if exercise_recovery and elapsed >= 120 and not outage_started:
                    setattr(w.worker.chain, "price", timeout)
                    if original_backup_price:
                        setattr(w.worker.backup_chain, "price", timeout)
                    outage_started = True
                    report["controlled_rpc_outage"] = "20 seconds; primary and backup market reads only"
                if outage_started and not outage_finished and elapsed >= 140:
                    setattr(w.worker.chain, "price", original_price)
                    if original_backup_price:
                        setattr(w.worker.backup_chain, "price", original_backup_price)
                    outage_finished = True
                if exercise_recovery and elapsed >= 300 and not stop_restart_done:
                    phase[0] = "controlled_stop_restart"
                    w.stop.click()
                    wait(
                        lambda: (
                            not w.running
                            and not w.busy
                            and not w.stop_pending
                            and not w.worker.stop_event.is_set()
                        )
                    )
                    assert not w.worker.paper.position
                    report["stop_restart_passed"] = True
                    capture("midrun_stop")
                    stop_restart_done = True
                    phase[0] = "automatic_live_prices"
                if (
                    not automatic_only
                    and not w.running
                    and not w.busy
                    and not w.worker.paper.position
                    and time.monotonic() >= restart_after
                ):
                    assert not report["errors"], "Unexpected stop must not be hidden by a test restart"
                    w.start.click()
                    wait(lambda: not w.busy)
                    report["test_restarts"] = report.get("test_restarts", 0) + 1
                    restart_after = time.monotonic() + 5
                if time.monotonic() - progress > 30:
                    progress = time.monotonic()
                    capture("automatic")
                    print(
                        json.dumps(
                            {
                                "elapsed": round(progress - began),
                                "samples": len(report["samples"]),
                                "trades": report["trades"],
                                "status": w.strategy_status.text(),
                            },
                            ensure_ascii=False,
                        ),
                        flush=True,
                    )
                    (directory / "progress.json").write_text(
                        json.dumps(
                            {
                                "elapsed": round(progress - began),
                                "samples": len(report["samples"]),
                                "trades": report["trades"],
                                "errors": report["errors"],
                                "mismatches": report["mismatches"],
                                "price_sources": report.get("price_sources", {}),
                                "running": w.running,
                            },
                            ensure_ascii=False,
                            indent=2,
                        )
                    )
            setattr(w.worker.chain, "price", original_price)
            report["observed_seconds"] = round(time.monotonic() - began, 2)
            report["automatic_running_at_end"] = w.running
            capture("automatic_end")
            phase[0] = "stop"
            w.stop.click()
            wait(
                lambda: (
                    not w.running and not w.worker.stop_event.is_set() and not w.busy and not w.stop_pending
                )
            )
            report["clean_stop"] = not w.worker.paper.position
            capture("stopped")
            if exercise_recovery:
                # Separate deterministic test after the natural market observation.
                phase[0] = "synthetic_minout_recovery"
                w.start.click()
                wait(lambda: not w.busy and w.running)
                wait(lambda: not w.worker.quote_unavailable)
                from dipbot.domain.strategy import Strategy

                observe = Strategy.observe
                injected = {"signal": False, "quote": False}

                def signal(strategy: Any, price: D, now: float, **kwargs: Any) -> Any:
                    if strategy is w.worker.strategy and not injected["signal"] and strategy.entry is None:
                        injected["signal"] = True
                        return "BUY"
                    return observe(strategy, price, now, **kwargs)

                def rejected_quote(chain: Chain, pool: Pool, amount: int, buy: Any) -> Any:
                    if buy and not injected["quote"]:
                        injected["quote"] = True
                        return 0
                    return paper_quote(chain, pool, amount, buy)

                with (
                    patch.object(Strategy, "observe", signal),
                    patch.object(Chain, "paper_quote", rejected_quote),
                ):
                    wait(lambda: bool(w.entry_notice))
                    assert w.running and not w.worker.paper.position
                    assert w.metrics["state"].text() == "WAIT DIP"
                    capture("minout_wait")
                    wait(lambda: not w.entry_notice, 30)
                    assert w.running and not report["errors"]
                report["synthetic_minout_resumed_without_start"] = True
                w.stop.click()
                wait(
                    lambda: (
                        not w.running
                        and not w.busy
                        and not w.stop_pending
                        and not w.worker.stop_event.is_set()
                    )
                )
                assert not w.worker.paper.position
            report["passed"] = report["clean_stop"] and not report["errors"] and not report["mismatches"]
        except Exception as exc:
            report["failure"] = str(exc)
            report["passed"] = False
        finally:
            if "original_price" in locals():
                setattr(w.worker.chain, "price", original_price)
                if original_backup_price:
                    setattr(w.worker.backup_chain, "price", original_backup_price)
            w.worker.stop_event.set()
            wait(lambda: not w.worker.running and not w.worker.stop_event.is_set(), 60)
            w.worker.quit_event.set()
            w.worker.wait()
            report["logs"] = logs
            report["router_fills"] = fills
            calls: Counter[str] = Counter()
            for counter in rpc:
                calls.update(counter)
            report["rpc_methods"] = dict(calls)
            prices = [D(s["price"]) for s in report["samples"]]
            report["distinct_prices"] = len(set(prices))
            if prices:
                report["price_range"] = [str(min(prices)), str(max(prices))]
            (directory / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
            print(
                json.dumps(
                    {k: v for k, v in report.items() if k not in {"logs", "samples"}}, ensure_ascii=False
                ),
                flush=True,
            )
        if close_after:
            w.close()
            return 0 if report["passed"] else 1
        # Hand control back to the user; a visible app must not retain dead controls.
        w.worker.event.disconnect(check)
        w.worker.log.disconnect(logs.append)
        w.worker.quit_event.clear()
        w.worker.stop_event.clear()
        w.worker.start()
        w.banner.setText("PAPER · Проверка завершена. Доступно ручное управление; реальные сделки отключены.")
        w.update_controls()
        app.setQuitOnLastWindowClosed(True)
        return app.exec()
