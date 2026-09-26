from __future__ import annotations

from PySide6.QtWidgets import QTableWidgetItem

from dipbot.market.chain import profiles
from dipbot.persistence import preferences


from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from dipbot.ui.window import Window

def market_changed(view: Window, *_):
    if not view.quote.currentText():
        return
    view.remember_amount()
    view.amount_key = preferences.pair_key(view.router.currentText(), view.quote.currentText())
    view.restore_amount()
    view.invalidate_discovery()


def invalidate_discovery(view: Window, *_, clear_pool=True):
    view.auto_generation += 1
    view.worker.discovery_generation = view.auto_generation
    view.route_comparison.setText("Сравнение маршрутов ещё не выполнено")
    view.autopair_timer.stop()
    view.selection_ready = False
    view.reset_price_display()
    view.candidates.clear()
    if clear_pool:
        view.pool_input.clear()
    view.pool_label.setText("Пул не проверен · выполните AutoPair или CHECK POOL")
    view.market_summary.setText(view.pool_label.text())
    view.update_controls()


def schedule_autopair(view: Window, *_):
    view.invalidate_discovery()
    if view.worker.chain is not None and not view.running and len(view.token.text().strip()) == 42:
        view.autopair_timer.start()


def auto_discover(view: Window):
    if view.running or view.worker.chain is None:
        return
    if view.busy:
        view.autopair_timer.start()
        return
    view.send("discover", token=view.token.text().strip(), quote=view.quote.currentText(),
              router=view.router.currentText())


def compare_routes(view: Window):
    reference = view.candidates.currentData()
    if reference is None:
        view.route_comparison.setText('Сначала найдите пулы через AutoPair')
        return
    view.route_comparison.setText('Сравнение на заданную сумму…')
    view.send('compare_routes', reference=reference,
        pools=[view.candidates.itemData(i) for i in range(view.candidates.count())],
        amount=view.params['amount'].text().strip(), sizing=view.sizing_policy(),
        maximum=view.params['max_roundtrip_loss'].text().strip(),
        cost_policy=view.entry_cost_policy(), gas=view.gas.text().strip())


def select_pool(view: Window):
    pool = view.candidates.currentData()
    if pool:
        view.send("select", pool=pool)


def remove_profile(view: Window):
    row = view.table.currentRow()
    if row >= 0:
        view.send("remove_profile", symbol=view.table.item(row, 0).text(), wallet=view.wallet.text())


def pair_clicked(view: Window, index):
    if view.busy or view.running:
        return
    view.quote.setCurrentText(view.table.item(index.row(), 0).text())
    view.tabs.setCurrentIndex(0)


def update_profiles(view: Window, dynamic=None):
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
