from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QModelIndex
from PySide6.QtWidgets import (
    QTableWidgetItem,
)

from dipbot.market.chain import profiles
from dipbot.persistence import preferences

if TYPE_CHECKING:
    from dipbot.ui.window import Window


def market_changed(view: Window, *_: object) -> None:
    if not view.quote.currentText():
        return
    view.remember_amount()
    view.presentation.amount_key = preferences.pair_key(view.router.currentText(), view.quote.currentText())
    view.restore_amount()
    view.invalidate_discovery()


def invalidate_discovery(view: Window, *_: object, clear_pool: bool = True) -> None:
    view.presentation.auto_generation += 1
    view.worker.discovery_generation = view.presentation.auto_generation
    view.route_comparison.setText("Сравнение маршрутов ещё не выполнено")
    view.autopair_timer.stop()
    view.presentation.selection_ready = False
    view.reset_price_display()
    view.candidates.clear()
    if clear_pool:
        view.pool_input.clear()
    view.pool_label.setText("Пул не проверен · выполните AutoPair или CHECK POOL")
    view.market_summary.setText(view.pool_label.text())
    view.update_controls()


def schedule_autopair(view: Window, *_: object) -> None:
    view.invalidate_discovery()
    if (
        view.worker.chain is not None
        and not view.presentation.running
        and len(view.token.text().strip()) == 42
    ):
        view.autopair_timer.start()


def auto_discover(view: Window) -> None:
    if view.presentation.running or view.worker.chain is None:
        return
    if view.presentation.busy:
        view.autopair_timer.start()
        return
    view.send(
        "discover",
        token=view.token.text().strip(),
        quote=view.quote.currentText(),
        router=view.router.currentText(),
    )


def compare_routes(view: Window) -> None:
    reference = view.candidates.currentData()
    if reference is None:
        view.route_comparison.setText("Сначала найдите пулы через AutoPair")
        return
    view.route_comparison.setText("Сравнение на заданную сумму…")
    view.send(
        "compare_routes",
        reference=reference,
        pools=[view.candidates.itemData(i) for i in range(view.candidates.count())],
        amount=view.params["amount"].text().strip(),
        sizing=view.sizing_policy(),
        maximum=view.params["max_roundtrip_loss"].text().strip(),
        cost_policy=view.entry_cost_policy(),
        gas=view.gas.text().strip(),
    )


def select_pool(view: Window) -> None:
    pool = view.candidates.currentData()
    if pool:
        view.send("select", pool=pool)


def remove_profile(view: Window) -> None:
    row = view.table.currentRow()
    item = view.table.item(row, 0) if row >= 0 else None
    if item is not None:
        view.send("remove_profile", symbol=item.text(), wallet=view.wallet.text())


def pair_clicked(view: Window, index: QModelIndex) -> None:
    if view.presentation.busy or view.presentation.running:
        return
    item = view.table.item(index.row(), 0)
    if item is None:
        return
    view.quote.setCurrentText(item.text())
    view.tabs.setCurrentIndex(0)


def update_profiles(view: Window, dynamic: dict[str, str] | None = None) -> None:
    pairs = profiles() | (dynamic if dynamic is not None else view.store.data.get("dynamic_profiles", {}))
    selected = view.quote.currentText() or "WBNB"
    blocked = view.quote.blockSignals(True)
    view.quote.clear()
    view.quote.addItems(["ALL"] + sorted(pairs, key=str.casefold))
    view.quote.setCurrentText(selected if selected == "ALL" or selected in pairs else "WBNB")
    view.quote.blockSignals(blocked)
    if hasattr(view, "amount_key") and selected != view.quote.currentText():
        view.market_changed()
    view.table.setRowCount(len(pairs))
    for row, (symbol, token) in enumerate(sorted(pairs.items(), key=lambda x: x[0].casefold())):
        for col, value in enumerate([symbol, token, "—"]):
            view.table.setItem(row, col, QTableWidgetItem(value))
