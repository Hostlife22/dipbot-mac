from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from PySide6.QtWidgets import (
    QMessageBox,
    QScrollArea,
)

from dipbot.execution.pending import LABELS as PENDING_LABELS

if TYPE_CHECKING:
    from dipbot.ui.window import Window


def refresh_recovery(view: Window) -> None:
    operation = view.store.data.get("operation")
    positions = view.store.data.get("positions", {})
    signature = repr((operation, positions))
    if view.presentation._recovery_signature == signature:
        return
    view.presentation._recovery_signature = signature
    view.recovery_notice.setVisible(bool(operation or positions))
    view.recovery_notice.setText(
        f"Восстановление LIVE · сохранённых позиций: {len(positions)}"
        + (" · незавершённая операция · открыть" if operation else " · открыть")
    )
    details = ["Локальные записи, не подтверждённый текущий баланс. Торговля автоматически не запускается."]
    if operation:
        details.append("Кошелёк операции: " + str(operation.get("wallet", "не указан")))
        for tx in operation.get("transactions", []):
            details.append(
                str(tx.get("hash", "hash не записан"))
                + " · "
                + str(tx.get("status", "неизвестно"))
                + " · "
                + {
                    "prepared": "записана до отправки; отправка могла произойти",
                    "submitted": "RPC принял отправку; ожидается receipt",
                    "receipt_validated": "receipt проверен",
                    "same_nonce_resolved": "nonce занят подтверждённой альтернативой",
                }.get(tx.get("stage"), "этап не записан")
            )
            if tx.get("superseded_by"):
                details.append("Подтверждённая альтернатива: " + str(tx["superseded_by"]))
            if tx.get("replaces"):
                details.append("Попытка отмены: " + str(tx["replaces"]))
            if tx.get("broadcast_route") == "custom":
                details.append("Маршрут отправки: отдельный RPC (endpoint хранится только в Keychain)")
            review = tx.get("receipt_review")
            if review:
                replacement = review.get("replacement_search", {})
                if replacement.get("hash"):
                    details.append(
                        "Тот же nonce: " + replacement["hash"] + " · блок " + str(replacement["block"])
                    )
                details.append(
                    PENDING_LABELS.get(review.get("state"), "Неизвестный результат сверки")
                    + " · повторная отправка автоматически запрещена"
                )
        if not operation.get("transactions"):
            details.append("Hash транзакции не записан; перед снятием блокировки проверьте балансы.")
    selected = view.saved_positions.currentData()
    view.saved_positions.clear()
    for key, position in positions.items():
        pool = position["pool"]
        amount = Decimal(position["amount"]) / Decimal(10) ** pool["token_decimals"]
        label = f"{key.split(':')[0]} · {pool['router']} · {pool['address']} · TARGET {amount}"
        view.saved_positions.addItem(label, key)
        details.append(label)
    index = view.saved_positions.findData(selected)
    if index >= 0:
        view.saved_positions.setCurrentIndex(index)
    view.recovery_details.setText("\n".join(details))


def compare_saved_positions(view: Window) -> None:
    view.position_comparison.setText("Чтение балансов сохранённых позиций…")
    view.send("compare_positions")


def check_receipts(view: Window) -> None:
    view.receipt_result.setText("Проверка receipts через RPC…")
    view.send("reconcile", gas=view.gas.text())


def show_recovery(view: Window) -> None:
    view.tabs.setCurrentIndex(1)
    tab = view.tabs.widget(1)
    if isinstance(tab, QScrollArea):
        tab.ensureWidgetVisible(view.recovery_group)


def prepare_saved_position(view: Window) -> None:
    if view.presentation.running or view.presentation.busy or view.presentation.display_position > 0:
        return
    record = view.store.data.get("positions", {}).get(view.saved_positions.currentData())
    if not record:
        return
    view.mode.setCurrentText("LIVE")
    view.invalidate_discovery()
    view.router.setCurrentText(record["pool"]["router"])
    view.token.setText(record["pool"]["token"])
    view.pool_input.setText(record["pool"]["address"])
    view.autopair_timer.stop()
    view.market_toggle.setChecked(True)
    view.tabs.setCurrentIndex(0)
    view.log(
        "Пул подготовлен. Подключите RPC и выполните CHECK POOL; сохранённая запись не заменяет проверку сети."
    )


def cancel_pending(view: Window) -> None:
    if view.mode.currentText() != "LIVE":
        QMessageBox.information(
            view,
            "Отмена pending",
            "Переключитесь в LIVE: отмена отправляет реальную транзакцию и расходует газ.",
        )
        return
    try:
        from dipbot.execution.cancellation import cancellation_plan

        plan = cancellation_plan(view.store.data.get("operation"), Decimal(view.gas.text()))
    except Exception as exc:
        QMessageBox.warning(view, "Отмена недоступна", str(exc))
        return
    fee = Decimal(plan["maximum_fee_wei"]) / Decimal(10) ** 18
    gas = Decimal(plan["gas_price"]) / Decimal(10) ** 9
    if (
        QMessageBox.question(
            view,
            "Отмена pending",
            f"Попытка отмены {plan['attempt']}/3: {plan['original_hash']}\nNonce {plan['nonce']}; перевод 0 BNB себе.\n"
            f"GAS {gas} gwei; комиссия до {fee} BNB.\n"
            "Исходная сделка может подтвердиться раньше отмены. Блокировка останется до сверки балансов. Продолжить?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        == QMessageBox.StandardButton.Yes
    ):
        view.send(
            "cancel_pending",
            mode="LIVE",
            gas=view.gas.text(),
            expected_hash=plan["original_hash"],
            expected_gas_price=plan["gas_price"],
        )


def unlock(view: Window) -> None:
    if (
        QMessageBox.question(
            view,
            "Сверка",
            "Вы проверили все receipts и фактические балансы?\n"
            "Кэш позиций будет сброшен. Остатки можно продать через SELL WALLET → BNB.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        == QMessageBox.StandardButton.Yes
    ):
        view.send("unlock", gas=view.gas.text())
