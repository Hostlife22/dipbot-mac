from __future__ import annotations

import time
from collections.abc import Callable
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QMessageBox,
    QTableWidgetItem,
)

from dipbot.application.messages import Event, EventKind, StatusPayload
from dipbot.domain.assets import WBNB
from dipbot.domain.records import ExitRetry, PriceContext, SweepReport
from dipbot.market.chain import Pool, profiles
from dipbot.persistence import preferences
from dipbot.persistence.dynamic import catalog
from dipbot.ui.theme import COLORS

if TYPE_CHECKING:
    from dipbot.ui.window import Window


def on_discovery_event(self: Window, payload: Any) -> bool:
    generation, event_name, value = payload
    if generation == self.presentation.auto_generation:
        if event_name == EventKind.ERROR:
            self.pool_label.setText("Ошибка RPC/проверки · повторите AutoPair или CHECK POOL")
        self.on_event(event_name, value)
    return False


def on_busy(self: Window, payload: bool) -> bool:
    self.presentation.busy = payload
    if not payload:
        self.presentation.searching = False
    return True


def on_exit_retry(self: Window, payload: ExitRetry | None) -> bool:
    self.presentation.exit_retry = payload
    return True


def on_error(self: Window, payload: str) -> bool:
    self.presentation.ui_error = str(payload)
    self.journal_toggle.setChecked(True)
    self.footer.setText("ОШИБКА: " + payload)
    QMessageBox.warning(self, "Операция прервана", payload)
    return True


def on_price_context(self: Window, payload: PriceContext) -> bool:
    self.presentation.same_block_cache = payload.get("same_block_cache", False)
    if self.presentation.price_source != payload["source"]:
        self.reset_price_display()
    self.presentation.market_block = payload.get("block")
    self.presentation.market_block_timestamp = payload.get("block_timestamp")
    self.presentation.market_rpc_source = payload.get("rpc_source", "BSC")
    self.presentation.price_source = payload["source"]
    self.presentation.display_unit = (
        "условных единиц (DEMO)"
        if payload["source"] == "DEMO"
        else next(
            (name for name, addr in profiles().items() if addr.lower() == payload["quote"].lower()),
            payload["quote"],
        )
    )
    if payload["source"] == "BSC" and self.isVisible():
        self.usd.set_token(payload["quote"])
        self.gas_usd.set_token(
            WBNB
            if (
                self.mode.currentText() == "LIVE"
                or (
                    self.mode.currentText() == "PAPER"
                    and (self.cost_limit.value() > 0 or self.paper_gas.value() > 0)
                )
            )
            and payload["quote"].lower() != WBNB.lower()
            else ""
        )
    return True


def on_price(self: Window, payload: str) -> bool:
    if self.mode.currentText() != "DEMO" and not self.presentation.selection_ready:
        return False
    self.presentation.last_price = Decimal(str(payload))
    self.chart.add(payload)
    self.metrics["price"].setText(self.display_price(payload))
    self.metrics["price"].setToolTip(str(payload))
    self.presentation.last_quote_at = time.monotonic()
    self.update_quote_age()
    return True


def on_trade_marker(self: Window, payload: Any) -> bool:
    if payload["mode"] == self.mode.currentText() and self.chart.values:
        self.chart.mark(payload["side"], payload["price"])
    return True


def on_autopair(self: Window, payload: str) -> bool:
    self.pool_label.setText(
        {
            "PENDING": "PENDING · ожидается ликвидность; повторите AutoPair",
            "NOT_FOUND": "Пулы не найдены",
            "INVALID_CONTRACT": "По адресу нет контракта BSC",
            "CATALOG_TOKEN": "Введён адрес базового профиля; выберите PAIR вручную",
            "UNSUPPORTED_POOL": "Неподдерживаемый пул или базовая пара",
            "AMBIGUOUS": "Найдено несколько пар; выберите маршрут явно",
        }.get(payload, self.pool_label.text())
    )
    return True


def on_route_comparison_error(self: Window, payload: str) -> bool:
    self.route_comparison.setText("Сравнение не выполнено: " + str(payload))
    return True


def on_route_comparison(self: Window, payload: Any) -> bool:
    lines = [f"AMOUNT {payload['amount']} в базе · блок {payload['block']}"]
    for row in payload["rows"]:
        pool = row["pool"]
        prefix = f"{pool.router}/{pool.fee} {pool.address[:8]}…"
        if row["error"]:
            lines.append(f"{prefix}: {row['error']}")
        else:
            cost = row["modeled_cost_pct"]
            suffix = f"; с моделью газа {cost:.3f}%" if cost is not None else "; без газа"
            lines.append(
                f"{prefix}: BUY {row['target_out']:.8g} target; потери цикла {row['loss_pct']:.3f}%{suffix}"
            )
    lines.append(
        "Порядок: меньше потерь цикла. Другие базы исключены. Это котировки, не симуляция token tax."
    )
    self.route_comparison.setText("\n".join(lines))
    return True


def on_pools(self: Window, payload: list[Pool]) -> bool:
    self.route_comparison.setText("Сравнение маршрутов ещё не выполнено")
    self.presentation.selection_ready = False
    self.pool_input.clear()
    self.candidates.clear()
    self.pool_label.setText("Выберите проверенный маршрут" if payload else "Пул не выбран")
    for pool in payload:
        self.candidates.addItem(pool.label, pool)
    return True


def on_selected(self: Window, payload: Pool) -> bool:
    self.reset_price_display()
    self.presentation.display_unit = next(
        (name for name, addr in profiles().items() if addr.lower() == payload.quote.lower()),
        payload.quote,
    )
    self.presentation.selection_ready = True
    self.presentation.ui_error = ""
    self.remember_amount()
    self.router.blockSignals(True)
    self.quote.blockSignals(True)
    self.router.setCurrentText(payload.router)
    names = catalog(self.store, payload.router)
    pair = next((name for name, token in names.items() if token.lower() == payload.quote.lower()), "ALL")
    self.quote.setCurrentText(pair)
    self.router.blockSignals(False)
    self.quote.blockSignals(False)
    self.presentation.amount_key = preferences.pair_key(
        payload.router, pair if pair != "ALL" else payload.quote.lower()
    )
    self.restore_amount()
    self.pool_input.setText(payload.address)
    self.token.setText(payload.token)
    self.pool_label.setText(payload.label + "\nБазовый актив исполнения: " + self.presentation.display_unit)
    pair_label = pair if pair != "ALL" else f"{payload.quote[:8]}…{payload.quote[-6:]}"
    self.market_summary.setText(
        f"TARGET {payload.token[:8]}…{payload.token[-6:]} / {pair_label} · {payload.router} · пул {payload.address[:8]}…{payload.address[-6:]}"
    )
    self.market_summary.setToolTip(
        f"TARGET: {payload.token}\nБаза: {pair_label} ({payload.quote})\nПул: {payload.address}"
    )
    return True


def on_sweep_report(self: Window, payload: SweepReport) -> bool:
    status = {
        "completed": "завершён",
        "stopped": "остановлен — частичный результат",
        "interrupted": "прерван — частичный результат",
    }.get(payload.get("status", ""), "результат")
    self.log("SWEEP: " + status)
    if payload["sold"]:
        self.log("SWEEP: обработаны активы: " + ", ".join(payload["sold"]))
    if payload["failed"]:
        self.log("SWEEP: ошибки по активам: " + ", ".join(payload["failed"]))
    if payload.get("error"):
        self.log("SWEEP: " + payload["error"])
    if payload.get("needs_reconciliation"):
        self.log("SWEEP: есть незавершённая операция; перед продолжением нужна сверка транзакций и балансов")
    for token, amount in payload["remaining"].items():
        self.log(f"SWEEP остаток {token}: {amount} raw")
    if payload["unknown"]:
        self.log("SWEEP: балансы не проверены: " + ", ".join(payload["unknown"]))
    if payload["skipped"]:
        self.log(
            "SWEEP: пропущены цели; проверьте/выберите их для этого кошелька: "
            + ", ".join(payload["skipped"])
        )
    return True


def on_wallet(self: Window, payload: str) -> bool:
    self.wallet.setText(payload)
    return True


def on_profiles(self: Window, payload: dict[str, str]) -> bool:
    self.update_profiles(payload)
    return True


def on_profile_removed(self: Window, payload: str) -> bool:
    self.schedule_autopair()
    return True


def on_balances(self: Window, payload: dict[str, str]) -> bool:
    self.log("Балансы: " + "; ".join(f"{k}={v}" for k, v in payload.items() if v not in ("0", "0.0")))
    for row in range(self.table.rowCount()):
        item = self.table.item(row, 0)
        if item is None:
            continue
        symbol = item.text()
        value = payload.get(symbol, "—")
        item = QTableWidgetItem(value)
        try:
            if float(value) > 0:
                item.setForeground(QColor(COLORS["positive"]))
        except ValueError:
            pass
        self.table.setItem(row, 2, item)
    return True


def on_position_comparison_error(self: Window, payload: str) -> bool:
    self.position_comparison.setText("Сверка не выполнена: " + payload)
    return True


def on_position_comparison(self: Window, payload: Any) -> bool:
    lines = [f"Снимок балансов: блок {payload['block']}. Локальные записи не изменены."]
    for row in payload["rows"]:
        scale = Decimal(10) ** row["decimals"]
        lines.append(
            f"{row['owner']} · {row['token']} · пул {row['pool']}\n"
            + f"Записано: {Decimal(row['saved_raw']) / scale}; в кошельке: {Decimal(row['actual_raw']) / scale} · "
            + ("совпадает" if row["matches"] else "РАСХОЖДЕНИЕ")
        )
    if not payload["rows"]:
        lines.append("Сохранённых позиций нет.")
    self.position_comparison.setText("\n".join(lines))
    return True


def on_accounting_report(self: Window, payload: str) -> bool:
    self.accounting_text.setPlainText(payload)
    return True


def on_receipt_review(self: Window, payload: str) -> bool:
    self.receipt_result.setText(payload)
    self.refresh_recovery()
    return True


def on_status(self: Window, payload: StatusPayload) -> bool:
    self.refresh_recovery()
    self.presentation.quote_unavailable = payload.get("quote_unavailable", False)
    self.presentation.exit_retry = payload.get("exit_retry")
    health = payload.get("rpc_health", [])
    self.rpc_health_label.setText(
        " · ".join(
            row["source"]
            + ": "
            + (f"{row['median_ms']:.0f} мс" if row["median_ms"] is not None else "нет замеров")
            + f", ошибок подряд {row['consecutive_errors']}"
            + (" (предпочтительный)" if row["preferred"] else "")
            for row in health
        )
    )
    self.presentation.signal_notice = payload.get("signal_notice", "")
    self.presentation.wait_reason = payload.get("wait_reason", "")
    self.presentation.entry_notice = payload.get("entry_notice", "")
    quote_exit = payload.get("exit_basis") == "quote" and Decimal(payload.get("position", "0")) > 0
    self.exit_status.setVisible(quote_exit)
    result = payload.get("exit_return")
    self.exit_status.setText(
        "TP/SL по продаже позиции: "
        + (
            f"{Decimal(result):+.2f}%"
            if result is not None and not payload.get("quote_unavailable")
            else "ожидание свежей котировки"
        )
        + (
            " · стоимость по модели PAPER, без token tax"
            if payload.get("mode") == "PAPER"
            else " · без газа и token tax"
        )
    )
    self.presentation.halt_reason = payload.get("halt_reason", "")
    self.presentation.running, self.presentation.active_mode, self.presentation.locked = (
        payload["running"],
        payload["mode"],
        payload["locked"],
    )
    if not self.presentation.running and not self.presentation.busy and not self.worker.stop_event.is_set():
        self.presentation.stop_pending = False
    active = (
        payload["mode"] == self.mode.currentText()
        and (payload["running"] or float(payload["position"]) > 0)
        and (self.mode.currentText() == "DEMO" or self.presentation.selection_ready)
    )
    self.presentation.base_price = (
        Decimal(payload["base"]) if active and Decimal(payload["base"]) > 0 else None
    )
    self.chart.reference_base = self.presentation.base_price
    self.metrics["base"].setText(self.display_price(self.presentation.base_price))
    age = payload.get("base_age")
    self.metrics["base"].setToolTip(
        payload.get("base_reason", "") + (f" · возраст {age:.1f} с" if age is not None else "")
    )
    self.metrics["position"].setText(f"{float(payload['position']):.8g}")
    self.metrics["position"].setToolTip(payload["position"])
    levels = payload.get("levels", {}) if active else {}
    self.chart.levels = {key: value for key, value in levels.items() if float(value) > 0}
    self.presentation.display_position = Decimal(payload["position"]) if active else Decimal(0)
    self.update_strategy_status()
    self.chart.update()
    self.levels_label.setText(
        " · ".join(
            f"{title}: {float(levels[key]):.8g}"
            if key in levels and float(levels[key]) > 0
            else f"{title}: —"
            for key, title in [("DIP", "Вход DIP"), ("ENTRY", "ENTRY"), ("TP", "TP"), ("SL", "SL")]
        )
    )
    self.presentation.pnl_status = payload.copy()
    self.refresh_pnl()
    return True


HANDLERS: dict[EventKind, Callable[[Window, Any], bool]] = {
    EventKind.DISCOVERY_EVENT: on_discovery_event,
    EventKind.BUSY: on_busy,
    EventKind.EXIT_RETRY: on_exit_retry,
    EventKind.ERROR: on_error,
    EventKind.PRICE_CONTEXT: on_price_context,
    EventKind.PRICE: on_price,
    EventKind.TRADE_MARKER: on_trade_marker,
    EventKind.AUTOPAIR: on_autopair,
    EventKind.ROUTE_COMPARISON_ERROR: on_route_comparison_error,
    EventKind.ROUTE_COMPARISON: on_route_comparison,
    EventKind.POOLS: on_pools,
    EventKind.SELECTED: on_selected,
    EventKind.SWEEP_REPORT: on_sweep_report,
    EventKind.WALLET: on_wallet,
    EventKind.PROFILES: on_profiles,
    EventKind.PROFILE_REMOVED: on_profile_removed,
    EventKind.BALANCES: on_balances,
    EventKind.POSITION_COMPARISON_ERROR: on_position_comparison_error,
    EventKind.POSITION_COMPARISON: on_position_comparison,
    EventKind.ACCOUNTING_REPORT: on_accounting_report,
    EventKind.RECEIPT_REVIEW: on_receipt_review,
    EventKind.STATUS: on_status,
}


def dispatch(window: Window, name: str | EventKind, payload: Any) -> None:
    message = Event.from_wire(name, payload)
    handler = HANDLERS.get(message.kind)
    if handler is None or handler(window, message.payload):
        window.update_controls()
