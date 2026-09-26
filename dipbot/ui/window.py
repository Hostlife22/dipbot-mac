from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from decimal import Decimal
from decimal import Decimal as D
from typing import Any

from PySide6.QtCore import QModelIndex, Qt, QTimer
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTableWidget,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from dipbot.application.messages import EventKind
from dipbot.application.worker import Worker
from dipbot.domain.records import ExitRetry
from dipbot.persistence import preferences
from dipbot.persistence.storage import Store
from dipbot.ui import (
    autopair_controller,
    event_handlers,
    position_presenter,
    recovery_controller,
    settings_controller,
)
from dipbot.ui.chart import Chart
from dipbot.ui.layout import build_about, build_bot, build_pairs, build_settings
from dipbot.ui.theme import METRICS
from dipbot.ui.ui_components import MetricLabel
from dipbot.ui.usd_feed import UsdRate


class Window(QMainWindow):
    accounting_text: QPlainTextEdit
    actions: list[QPushButton]  # type: ignore[assignment]  # Historical widget list, not QWidget.actions().
    activity: QPlainTextEdit
    adaptive_rpc: QCheckBox
    age_timer: QTimer
    _monitor_revision: tuple[int, int]
    _recovery_signature: str
    amount_currency: str
    amount_key: str
    amount_unit: QComboBox
    autopair_timer: QTimer
    backup_rpc: QLineEdit
    backup_rpc_preset: QComboBox
    banner: QLabel
    base_price: Decimal | None
    block_age_limit: QDoubleSpinBox
    buy: QPushButton
    candidates: QComboBox
    chart: Chart
    continue_after_exit: QCheckBox
    convert_amount: QLineEdit
    cost_gas: QDoubleSpinBox
    cost_limit: QDoubleSpinBox
    display_position: Decimal
    display_unit: str
    editable: list[QWidget]
    entry_notice: str
    execution_bar: QWidget
    exit_basis: QComboBox
    exit_fields: dict[str, QDoubleSpinBox]
    exit_retry: ExitRetry | None
    exit_status: QLabel
    footer: QLabel
    gas: QLineEdit
    gas_reserve: QLineEdit
    gas_usd: UsdRate
    halt_reason: str
    interval: QDoubleSpinBox
    journal_toggle: QPushButton
    key: QLineEdit
    last_price: Decimal | None
    last_quote_at: float | None
    levels_label: QLabel
    market_block: int | None
    market_block_timestamp: int | None
    market_rpc_source: str
    market_summary: MetricLabel
    market_toggle: QPushButton
    metric_captions: dict[str, QLabel]
    metrics: dict[str, QLabel]
    mode: QComboBox
    mode_badge: QLabel
    pair_amounts: dict[str, str]
    paper_delay: QDoubleSpinBox
    paper_fee: QLineEdit
    paper_gas: QDoubleSpinBox
    params: dict[str, QLineEdit]
    pnl_status: dict[str, Any]
    pool_input: QLineEdit
    pool_label: QLabel
    position_comparison: QLabel
    position_estimate: QLabel
    price_source: str
    quote: QComboBox
    quote_age: QLabel
    quote_unavailable: bool
    receipt_result: QLabel
    record_market: QCheckBox
    recovery_details: QLabel
    recovery_group: QGroupBox
    recovery_notice: QPushButton
    route_comparison: QLabel
    router: QComboBox
    rpc: QLineEdit
    rpc_health_label: QLabel
    rpc_preset: QComboBox
    same_block_cache: bool
    save_rpc: QCheckBox
    saved_positions: QComboBox
    sell: QPushButton
    send_rpc: QLineEdit
    signal_fields: dict[str, QDoubleSpinBox]
    signal_mode: QComboBox
    signal_notice: str
    signal_rebound: QDoubleSpinBox
    signal_volatility: QDoubleSpinBox
    signal_window: QDoubleSpinBox
    start: QPushButton
    stop: QPushButton
    store: Store
    strategy_status: QLabel
    strategy_toggle: QPushButton
    table: QTableWidget
    tabs: QTabWidget
    timing_report: QPlainTextEdit
    timing_timer: QTimer
    token: QLineEdit
    trade_details: QLabel
    trade_toggle: QPushButton
    ui_error: str
    usd: UsdRate
    usd_pair_amounts: dict[str, str]
    wait_reason: str
    wallet: QLineEdit
    worker: Worker
    ws_rpc: QLineEdit

    def __init__(self, store: Store | None = None) -> None:
        super().__init__()
        self.store = store or Store()
        self.worker = Worker(self.store)
        self.worker.log.connect(self.log)
        self.worker.event.connect(self.on_event)
        self.busy = False
        self.searching = False
        self.running = False
        self.stop_pending = False
        self.active_mode = "DEMO"
        self.locked = bool(self.store.data.get("operation"))
        self.editable = []
        self.actions = []
        self.setWindowTitle("DipBot Mac · BSC")
        self.resize(1100, 750)
        self.setMinimumSize(940, 700)
        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(*([METRICS["page"]] * 4))
        layout.setSpacing(METRICS["gap"])
        title_row = QHBoxLayout()
        title = QLabel("DipBot  /  BSC")
        title.setObjectName("title")
        title_row.addWidget(title)
        self.mode_badge = QLabel("DEMO")
        self.mode_badge.setObjectName("modeBadge")
        title_row.addWidget(self.mode_badge)
        title_row.addStretch()
        self.mode = QComboBox()
        self.mode.addItems(["DEMO", "PAPER", "LIVE"])
        self.mode.setAccessibleName("Режим торговли")
        self.mode.setMinimumWidth(100)
        self.mode.currentTextChanged.connect(self.mode_changed)
        self.editable.append(self.mode)
        title_row.addWidget(QLabel("Режим"))
        title_row.addWidget(self.mode)
        layout.addLayout(title_row)
        subtitle = QLabel("PancakeSwap V2 / V3  ·  BNB Smart Chain")
        subtitle.setObjectName("muted")
        title.setToolTip(subtitle.text())
        subtitle.deleteLater()
        self.banner = QLabel()
        self.banner.setObjectName("banner")
        self.banner.setWordWrap(True)
        layout.addWidget(self.banner)
        self.recovery_notice = QPushButton()
        self.recovery_notice.clicked.connect(self.show_recovery)
        layout.addWidget(self.recovery_notice)
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.tabBar().setDrawBase(False)
        self.tabs.tabBar().setExpanding(False)
        layout.addWidget(self.tabs, 1)
        build_bot(self)
        build_settings(self)
        build_pairs(self)
        build_about(self)
        for label in (
            self.pool_label,
            self.market_summary,
            self.route_comparison,
            self.strategy_status,
            self.receipt_result,
        ):
            label.setTextFormat(Qt.TextFormat.PlainText)
        for form in self.findChildren(QFormLayout):
            for row in range(form.rowCount()):
                label_item = form.itemAt(row, QFormLayout.ItemRole.LabelRole)
                field_item = form.itemAt(row, QFormLayout.ItemRole.FieldRole)
                if (
                    label_item
                    and field_item
                    and isinstance(label_item.widget(), QLabel)
                    and field_item.widget()
                ):
                    buddy_label, buddy_field = label_item.widget(), field_item.widget()
                    assert isinstance(buddy_label, QLabel) and buddy_field is not None
                    buddy_label.setBuddy(buddy_field)
                    buddy_field.setAccessibleName(buddy_label.text())
        layout.addWidget(self.execution_bar)
        self.activity = QPlainTextEdit()
        self.activity.setReadOnly(True)
        self.activity.setMaximumBlockCount(600)
        self.activity.setFixedHeight(96)
        self.activity.hide()
        self.journal_toggle = QPushButton("▸  ACTIVITY LOG · журнал событий")
        self.journal_toggle.setObjectName("journal")
        self.journal_toggle.setCheckable(True)
        self.journal_toggle.toggled.connect(self.toggle_journal)
        layout.addWidget(self.journal_toggle)
        layout.addWidget(self.activity)
        self.exit_status = QLabel("")
        self.exit_status.setObjectName("muted")
        self.exit_status.setWordWrap(True)
        self.exit_status.hide()
        layout.addWidget(self.exit_status)
        self.footer = QLabel("Готов к DEMO. Реальные сделки доступны только в LIVE.")
        self.footer.setObjectName("footer")
        self.footer.setWordWrap(True)
        layout.addWidget(self.footer)
        saved_preferences = self.store.data.get("ui_preferences")
        if saved_preferences is not None:
            try:
                saved_preferences = preferences.normalize(saved_preferences)
                for key, value in saved_preferences["settings"].items():
                    self.params[key].setText(value)
                sizing = saved_preferences.get("sizing", {})
                self.amount_unit.setCurrentIndex(self.amount_unit.findData(sizing.get("unit", "quote")))
                self.gas_reserve.setText(sizing.get("reserve_bnb", "0.0001"))
                self.adaptive_rpc.setChecked(saved_preferences.get("adaptive_rpc", False))
                self.record_market.setChecked(saved_preferences.get("record_market", True))
                policy = saved_preferences.get("signal_policy", {})
                self.signal_mode.setCurrentIndex(self.signal_mode.findData(policy.get("mode", "legacy")))
                self.signal_window.setValue(float(policy.get("window_seconds", 60)))
                self.signal_volatility.setValue(float(policy.get("volatility_multiplier", 2)))
                self.signal_rebound.setValue(float(policy.get("rebound_pct", 0)))
                self.block_age_limit.setValue(float(policy.get("max_block_age", 5)))
                paper_model = saved_preferences.get("paper_policy", {})
                self.paper_delay.setValue(float(paper_model.get("latency_seconds", 0.25)))
                self.paper_fee.setText(paper_model.get("fee_quote", "0"))
                self.paper_gas.setValue(paper_model.get("gas_units", 0))
                costs = saved_preferences.get("entry_cost_policy", {})
                self.cost_limit.setValue(float(costs.get("maximum_pct", 0)))
                self.cost_gas.setValue(float(costs.get("roundtrip_gas", 400000)))
                exits = saved_preferences.get("exit_policy", {})
                self.exit_basis.setCurrentIndex(self.exit_basis.findData(exits.get("tp_sl_basis", "spot")))
                for key, field in self.exit_fields.items():
                    field.setValue(float(exits.get(key, 0)))
                self.continue_after_exit.setChecked(exits.get("continue_after_risk_exit", False))
                self.gas.setText(saved_preferences["gas"])
                self.interval.setValue(float(saved_preferences["interval"]))
            except ValueError:
                saved_preferences = None
                self.log("Сохранённые параметры некорректны: использованы значения по умолчанию")
        self.refresh_recovery()
        self.mode_changed()
        self.update_profiles()
        saved_preferences = saved_preferences or {}
        self.pair_amounts = dict(saved_preferences.get("pair_amounts", {}))
        self.usd_pair_amounts = dict(saved_preferences.get("usd_pair_amounts", {}))
        self.amount_currency = self.amount_unit.currentData()
        selection = saved_preferences.get("selection", {})
        self.router.setCurrentText(selection.get("router", "AUTO"))
        if self.quote.findText(selection.get("pair", "WBNB")) >= 0:
            self.quote.setCurrentText(selection.get("pair", "WBNB"))
        self.amount_key = preferences.pair_key(self.router.currentText(), self.quote.currentText())
        if self.amount_key in self.amount_map():
            self.params["amount"].setText(self.amount_map()[self.amount_key])
        self.auto_generation = 0
        self.selection_ready = False
        self.autopair_timer = QTimer(self)
        self.autopair_timer.setSingleShot(True)
        self.autopair_timer.setInterval(220)
        self.autopair_timer.timeout.connect(self.auto_discover)
        self.token.textEdited.connect(self.schedule_autopair)
        self.pool_input.textEdited.connect(lambda: self.invalidate_discovery(clear_pool=False))
        self.router.currentTextChanged.connect(self.market_changed)
        self.quote.currentTextChanged.connect(self.market_changed)
        self.amount_unit.currentIndexChanged.connect(self.amount_unit_changed)
        if self.locked:
            self.log("В журнале есть незавершённая LIVE-операция. Проведите сверку в настройках")
        self.update_controls()
        application = QApplication.instance()
        assert application is not None
        application.aboutToQuit.connect(self.join_worker_at_exit)
        self.worker.start()

    def join_worker_at_exit(self) -> None:
        # QApplication.quit() can bypass closeEvent. Do not let Qt destroy a
        # running worker. Let an in-flight operation finish its durable writes;
        # this is process teardown, not a new STOP/sell command.
        self.worker.quit_event.set()
        self.worker.wait()

    def button(self, text: str, callback: Callable[..., object], kind: str | None = None) -> QPushButton:
        button = QPushButton(text)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.clicked.connect(callback)
        if kind:
            button.setObjectName(kind)
        self.actions.append(button)
        return button

    def field(self, value: str = "", placeholder: str = "") -> QLineEdit:
        field = QLineEdit(value)
        field.setPlaceholderText(placeholder)
        field.setMinimumWidth(110)
        field.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.editable.append(field)
        return field

    def tab(self, name: str) -> QVBoxLayout:
        content = QWidget()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(content)
        self.tabs.addTab(scroll, name)
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 6, 4, 4)
        layout.setSpacing(METRICS["gap"])
        return layout

    def form(self, parent: QWidget) -> QFormLayout:
        layout = QFormLayout(parent)
        layout.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        layout.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        layout.setHorizontalSpacing(14)
        layout.setVerticalSpacing(8)
        layout.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        return layout

    def disclosure(
        self, layout: QVBoxLayout, title: str, content: QWidget, expanded: bool = False
    ) -> QPushButton:
        toggle = QPushButton()
        toggle.setObjectName("disclosure")
        toggle.setCheckable(True)
        toggle.setChecked(expanded)
        toggle.setCursor(Qt.CursorShape.PointingHandCursor)

        def changed(opened: Any) -> None:
            content.setVisible(opened)
            toggle.setText(("▾  " if opened else "▸  ") + title)

        toggle.toggled.connect(changed)
        layout.addWidget(toggle)
        layout.addWidget(content)
        changed(expanded)
        return toggle

    def toggle_journal(self, expanded: bool) -> None:
        self.activity.setVisible(expanded)
        self.journal_toggle.setText(("▾" if expanded else "▸") + "  ACTIVITY LOG · журнал событий")

    def log(self, message: str) -> None:
        self.activity.appendPlainText(datetime.now().strftime("%H:%M:%S") + "  " + message)

    def amount_map(self) -> dict[str, str]:
        return settings_controller.amount_map(self)

    def restore_amount(self) -> None:
        return settings_controller.restore_amount(self)

    def amount_unit_changed(self) -> None:
        return settings_controller.amount_unit_changed(self)

    def remember_amount(self) -> None:
        return settings_controller.remember_amount(self)

    def market_changed(self, *_: object) -> None:
        return autopair_controller.market_changed(self, *_)

    def invalidate_discovery(self, *_: object, clear_pool: bool = True) -> None:
        return autopair_controller.invalidate_discovery(self, clear_pool=clear_pool, *_)

    def schedule_autopair(self, *_: object) -> None:
        return autopair_controller.schedule_autopair(self, *_)

    def auto_discover(self) -> None:
        return autopair_controller.auto_discover(self)

    def send(self, name: str, **data: Any) -> None:
        if self.busy:
            return
        if name in ("discover", "select", "verify", "connect"):
            self.invalidate_discovery()
        if name in ("discover", "verify", "select", "add_profile", "compare_routes"):
            data["generation"] = self.auto_generation
        if name in ("discover", "verify", "select"):
            self.pool_label.setText("Поиск и проверка маршрута…")
        self.searching = name in ("discover", "verify", "select")
        self.busy = True
        self.ui_command = name
        self.ui_error = ""
        self.update_controls()
        self.worker.submit(name, **data)

    def compare_routes(self) -> None:
        return autopair_controller.compare_routes(self)

    def paper_policy(self) -> dict[str, Any]:
        return settings_controller.paper_policy(self)

    def entry_cost_policy(self) -> dict[str, Any]:
        return settings_controller.entry_cost_policy(self)

    def exit_policy(self) -> dict[str, Any]:
        return settings_controller.exit_policy(self)

    def sizing_policy(self) -> dict[str, Any]:
        return settings_controller.sizing_policy(self)

    def signal_policy(self) -> dict[str, Any]:
        return settings_controller.signal_policy(self)

    def trade(self, command: str, **extra: Any) -> None:
        mode = self.mode.currentText()
        amount_unit = "USD" if self.amount_unit.currentData() == "usd" else "базового актива"
        if command in ("convert", "sweep") and mode != "LIVE":
            QMessageBox.information(self, "LIVE", "Converter и Sweep доступны только в LIVE")
            return
        if mode == "LIVE":
            description = (
                "Будут проданы все зарегистрированные target и базовые активы кошелька."
                if command == "sweep"
                else f"Действие: {command.upper()}\nAMOUNT: {self.params['amount'].text()} {amount_unit}."
                if command != "convert"
                else f"Converter: {'BUY за ' + extra['amount'] + ' BNB' if extra['buy'] else 'SELL всего баланса базы → BNB'}"
            )
            if (
                QMessageBox.question(
                    self,
                    "Реальная торговля BSC",
                    description
                    + f"\nTARGET: {self.token.text()}\nPOOL: {self.pool_input.text()}\nWALLET: {self.store.data.get('wallet_address', 'Keychain')}"
                    "\n\nБудут подписаны и отправлены реальные транзакции, включая необходимые approve. Продолжить?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                != QMessageBox.StandardButton.Yes
            ):
                return
        self.send(
            command,
            mode=mode,
            generation=self.auto_generation,
            settings={k: v.text().strip() for k, v in self.params.items()},
            paper_policy=self.paper_policy(),
            entry_cost_policy=self.entry_cost_policy(),
            exit_policy=self.exit_policy(),
            sizing=self.sizing_policy(),
            record_market=self.record_market.isChecked(),
            signal_policy=self.signal_policy(),
            interval=self.interval.value(),
            gas=self.gas.text(),
            token=self.token.text(),
            router=self.router.currentText(),
            pool=self.pool_input.text(),
            **extra,
        )

    def sell_position(self) -> None:
        if (
            self.active_mode == "LIVE"
            and QMessageBox.question(
                self,
                "SELL POSITION",
                "Продать отслеживаемую позицию реальной транзакцией?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        self.send("sell")

    def stop_bot(self) -> None:
        self.stop_pending = True
        if self.searching or not self.selection_ready:
            self.invalidate_discovery()
        else:
            self.autopair_timer.stop()
        self.worker.stop_event.set()
        self.log("STOP запрошен. Если сделка отправлена — ожидается receipt; затем закрытие позиции")
        self.footer.setText("Остановка запрошена · ожидается завершение текущей операции и закрытие позиции")
        self.metrics["state"].setText("STOPPING")
        self.update_strategy_status()
        self.update_controls()

    def select_pool(self) -> None:
        return autopair_controller.select_pool(self)

    def save_wallet(self) -> None:
        return settings_controller.save_wallet(self)

    def refresh_recovery(self) -> None:
        return recovery_controller.refresh_recovery(self)

    def compare_saved_positions(self) -> None:
        return recovery_controller.compare_saved_positions(self)

    def check_receipts(self) -> None:
        return recovery_controller.check_receipts(self)

    def show_recovery(self) -> None:
        return recovery_controller.show_recovery(self)

    def prepare_saved_position(self) -> None:
        return recovery_controller.prepare_saved_position(self)

    def load_rpc(self) -> None:
        return settings_controller.load_rpc(self)

    def add_rpc_presets(
        self, form: QFormLayout, title: str, field: QLineEdit, presets: list[tuple[str, str | None]]
    ) -> QComboBox:
        return settings_controller.add_rpc_presets(self, form, title, field, presets)

    def balances(self) -> None:
        self.send("balance", wallet=self.wallet.text().strip())

    def cancel_pending(self) -> None:
        return recovery_controller.cancel_pending(self)

    def unlock(self) -> None:
        return recovery_controller.unlock(self)

    def remove_profile(self) -> None:
        return autopair_controller.remove_profile(self)

    def pair_clicked(self, index: QModelIndex) -> None:
        return autopair_controller.pair_clicked(self, index)

    def update_profiles(self, dynamic: dict[str, str] | None = None) -> None:
        return autopair_controller.update_profiles(self, dynamic)

    def mode_changed(self) -> None:
        return settings_controller.mode_changed(self)

    def reset_price_display(self) -> None:
        return position_presenter.reset_price_display(self)

    def display_price(self, value: D | str | float | int | None, digits: int = 8) -> str:
        return position_presenter.display_price(self, value, digits)

    def capture_usd_rates(self) -> None:
        return position_presenter.capture_usd_rates(self)

    def refresh_currency(self) -> None:
        return position_presenter.refresh_currency(self)

    def refresh_trade_details(self) -> None:
        return position_presenter.refresh_trade_details(self)

    def refresh_pnl(self) -> None:
        return position_presenter.refresh_pnl(self)

    def update_state_badge(self) -> None:
        return position_presenter.update_state_badge(self)

    def update_strategy_status(self) -> None:
        return position_presenter.update_strategy_status(self)

    def update_quote_age(self) -> None:
        return position_presenter.update_quote_age(self)

    def refresh_timings(self) -> None:
        return position_presenter.refresh_timings(self)

    def update_controls(self) -> None:
        return position_presenter.update_controls(self)

    def on_event(self, name: str | EventKind, payload: Any) -> None:
        event_handlers.dispatch(self, name, payload)

    def closeEvent(self, event: QCloseEvent) -> None:
        if (
            self.busy
            or self.running
            or getattr(self, "stop_pending", False)
            or getattr(self, "display_position", 0) > 0
        ):
            QMessageBox.information(
                self, "Сначала STOP", "Остановите BOT и дождитесь завершения текущей операции перед закрытием"
            )
            event.ignore()
            return
        self.invalidate_discovery()
        self.worker.quit_event.set()
        if not self.worker.wait(1500):
            event.ignore()
            return
        # The worker has exited: saving cannot race its transaction journal.
        try:
            self.remember_amount()
            preferences.save(
                self.store,
                {
                    "version": 1,
                    "selection": {"router": self.router.currentText(), "pair": self.quote.currentText()},
                    "pair_amounts": self.pair_amounts,
                    "usd_pair_amounts": self.usd_pair_amounts,
                    "signal_policy": self.signal_policy(),
                    "exit_policy": self.exit_policy(),
                    "entry_cost_policy": self.entry_cost_policy(),
                    "paper_policy": self.paper_policy(),
                    "sizing": self.sizing_policy(),
                    "record_market": self.record_market.isChecked(),
                    "adaptive_rpc": self.adaptive_rpc.isChecked(),
                    "settings": {key: field.text().strip() for key, field in self.params.items()},
                    "gas": self.gas.text().strip(),
                    "interval": str(self.interval.value()),
                },
            )
        except (ValueError, OSError):
            QMessageBox.warning(
                self,
                "Настройки не сохранены",
                "Не удалось сохранить параметры. Предыдущие настройки сохранены, если запись не была заменена.",
            )
        self.usd.set_token("")
        self.gas_usd.set_token("")
        event.accept()
