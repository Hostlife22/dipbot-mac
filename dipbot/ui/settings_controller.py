from __future__ import annotations

from typing import TYPE_CHECKING, Any

from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QLineEdit,
    QMessageBox,
)

from dipbot.domain.assets import WBNB
from dipbot.persistence import preferences
from dipbot.persistence.vault import Vault

if TYPE_CHECKING:
    from dipbot.ui.window import Window


def amount_map(view: Window) -> dict[str, str]:
    return (
        view.presentation.usd_pair_amounts
        if view.presentation.amount_currency == "usd"
        else view.presentation.pair_amounts
    )


def restore_amount(view: Window) -> None:
    default = "1" if view.presentation.amount_currency == "usd" else "0.02"
    view.params["amount"].setText(view.amount_map().get(view.presentation.amount_key, default))


def amount_unit_changed(view: Window) -> None:
    view.remember_amount()
    view.presentation.amount_currency = view.amount_unit.currentData()
    view.restore_amount()


def remember_amount(view: Window) -> None:
    try:
        view.amount_map()[view.presentation.amount_key] = preferences.positive_amount(
            view.params["amount"].text()
        )
    except ValueError:
        pass  # Invalid edits never replace a previously valid per-pair amount.


def paper_policy(view: Window) -> dict[str, Any]:
    return {
        "gas_units": int(view.paper_gas.value()),
        "latency_seconds": view.paper_delay.value(),
        "fee_quote": view.paper_fee.text().strip(),
    }


def entry_cost_policy(view: Window) -> dict[str, Any]:
    return {"maximum_pct": str(view.cost_limit.value()), "roundtrip_gas": int(view.cost_gas.value())}


def exit_policy(view: Window) -> dict[str, Any]:
    return {
        "continue_after_risk_exit": view.continue_after_exit.isChecked(),
        "tp_sl_basis": view.exit_basis.currentData(),
        **{key: str(field.value()) for key, field in view.exit_fields.items()},
    }


def sizing_policy(view: Window) -> dict[str, Any]:
    return {"unit": view.amount_unit.currentData(), "reserve_bnb": view.gas_reserve.text().strip()}


def signal_policy(view: Window) -> dict[str, Any]:
    return {
        "mode": view.signal_mode.currentData(),
        "window_seconds": view.signal_window.value(),
        "rebound_pct": str(view.signal_rebound.value()),
        "max_block_age": view.block_age_limit.value(),
        "volatility_multiplier": str(view.signal_volatility.value()),
    }


def save_wallet(view: Window) -> None:
    key = view.key.text().strip()
    view.key.clear()
    view.send("wallet", key=key)


def load_rpc(view: Window) -> None:
    try:
        primary = Vault().get("rpc")
        backup = Vault().get("backup_rpc")
        websocket = Vault().get("ws_rpc")
        broadcaster = Vault().get("send_rpc")
        if broadcaster is not None:
            view.send_rpc.setText(broadcaster)
        if websocket is not None:
            view.ws_rpc.setText(websocket)
        if primary is not None:
            view.rpc.setText(primary)
        if backup is not None:
            view.backup_rpc.setText(backup)
    except Exception:
        QMessageBox.warning(view, "Keychain", "Не удалось прочитать RPC из Keychain")


def add_rpc_presets(
    view: Window, form: QFormLayout, title: str, field: QLineEdit, presets: list[tuple[str, str | None]]
) -> QComboBox:
    combo = QComboBox()
    for label, url in presets:
        combo.addItem(label, url)
    view.editable.append(combo)
    form.addRow(title, combo)

    def selected(index: int) -> None:
        url = combo.itemData(index)
        if url is not None:
            field.setText(url)
        else:
            field.setFocus()

    def edited(text: str) -> None:
        index = next((i for i, (_, url) in enumerate(presets) if url == text.strip()), len(presets) - 1)
        combo.blockSignals(True)
        combo.setCurrentIndex(index)
        combo.blockSignals(False)

    combo.currentIndexChanged.connect(selected)
    field.textChanged.connect(edited)
    field.setText(presets[0][1])
    return combo


def mode_changed(view: Window) -> None:
    mode = view.mode.currentText()
    descriptions = {
        "DEMO": "DEMO · Локальный рынок и виртуальный баланс. RPC и кошелёк не нужны.",
        "PAPER": "PAPER · Реальная цена BSC, виртуальные сделки. Выберите пул и нажмите START.",
        "LIVE": "LIVE · Реальные средства. Укажите RPC, сохраните кошелёк и проверьте выбранный пул.",
    }
    view.banner.setText(descriptions[mode])
    view.banner.setVisible(mode == "LIVE")
    view.mode_badge.setToolTip(descriptions[mode])
    view.mode_badge.setText(
        mode
        + (
            " · реальные средства"
            if mode == "LIVE"
            else " · виртуальные сделки"
            if mode == "PAPER"
            else " · модель"
        )
    )
    view.mode_badge.setProperty("mode", mode)
    view.mode_badge.style().unpolish(view.mode_badge)
    view.mode_badge.style().polish(view.mode_badge)
    view.banner.setProperty("mode", mode)
    view.banner.style().unpolish(view.banner)
    view.banner.style().polish(view.banner)
    if mode == "DEMO":
        view.presentation.display_unit = "условных единиц (DEMO)"
    elif hasattr(view, "quote"):
        view.presentation.display_unit = (
            view.quote.currentText() if view.quote.currentText() != "ALL" else "BASE"
        )
    if hasattr(view, "chart"):
        view.reset_price_display()
        if mode != "DEMO" and view.selection_ready and view.isVisible():
            selected = view.store.data.get("last_pool", {})
            quote = selected.get("quote", "")
            view.usd.set_token(quote)
            needs_gas_rate = mode == "LIVE" or view.cost_limit.value() > 0 or view.paper_gas.value() > 0
            view.gas_usd.set_token(WBNB if quote and quote.lower() != WBNB.lower() and needs_gas_rate else "")
    if hasattr(view, "selection_ready"):
        view.update_controls()
    if mode == "DEMO":
        view.market_summary.setText("Рынок: DEMO · локальная модель")
    elif hasattr(view, "pool_label"):
        view.market_summary.setText(
            view.pool_label.text()
            if "DEMO" not in view.pool_label.text()
            else "Рынок не выбран · AutoPair / CHECK POOL"
        )
