from dipbot.application.messages import Event, EventKind
from dipbot.ui import autopair_controller
from dipbot.ui import settings_controller
from dipbot.ui import recovery_controller
from dipbot.ui import position_presenter
from dipbot.ui.chart import Chart
from dipbot.ui.layout import build_bot, build_settings, build_pairs, build_about
import argparse
from collections import deque
import sys
import time
from datetime import datetime
from decimal import Decimal

from PySide6.QtCore import Qt, QLockFile, QTimer, QPointF, QRectF
from PySide6.QtGui import QColor, QPainter, QPen, QPainterPath
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QLabel, QPushButton,
    QLineEdit, QComboBox, QDoubleSpinBox, QFormLayout, QVBoxLayout, QHBoxLayout,
    QGridLayout, QGroupBox, QPlainTextEdit, QTabWidget, QCheckBox, QMessageBox,
    QTableWidget, QTableWidgetItem, QHeaderView, QScrollArea, QSizePolicy)

from dipbot.market.chain import profiles, WBNB
from dipbot.persistence.dynamic import catalog
from dipbot.persistence.storage import Store, data_dir
from dipbot.persistence.vault import Vault
from dipbot.application.worker import Worker
from dipbot.observability.telemetry import TIMINGS
from dipbot.execution.pending import LABELS as PENDING_LABELS
from dipbot.persistence import preferences
from dipbot.ui.usd_feed import UsdRate
from dipbot.domain.usd import price_text


from dipbot.domain.strategy import Settings
from dipbot.ui.theme import STYLE, COLORS, METRICS
from dipbot.ui.ui_components import MetricLabel, set_tone


class Window(QMainWindow):
    def __init__(self, store=None):
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
        for label in (self.pool_label, self.market_summary, self.route_comparison, self.strategy_status, self.receipt_result):
            label.setTextFormat(Qt.PlainText)
        for form in self.findChildren(QFormLayout):
            for row in range(form.rowCount()):
                label_item = form.itemAt(row, QFormLayout.LabelRole)
                field_item = form.itemAt(row, QFormLayout.FieldRole)
                if label_item and field_item and isinstance(label_item.widget(), QLabel) and field_item.widget():
                    label_item.widget().setBuddy(field_item.widget())
                    field_item.widget().setAccessibleName(label_item.widget().text())
        layout.addWidget(self.execution_bar)
        self.activity = QPlainTextEdit()
        self.activity.setReadOnly(True)
        self.activity.setMaximumBlockCount(600)
        self.activity.setFixedHeight(96)
        self.activity.hide()
        self.journal_toggle = QPushButton("▸  ACTIVITY LOG · журнал событий")
        self.journal_toggle.setObjectName('journal')
        self.journal_toggle.setCheckable(True)
        self.journal_toggle.toggled.connect(self.toggle_journal)
        layout.addWidget(self.journal_toggle)
        layout.addWidget(self.activity)
        self.exit_status = QLabel('')
        self.exit_status.setObjectName('muted')
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
                sizing = saved_preferences.get('sizing', {})
                self.amount_unit.setCurrentIndex(self.amount_unit.findData(sizing.get('unit','quote')))
                self.gas_reserve.setText(sizing.get('reserve_bnb','0.0001'))
                self.adaptive_rpc.setChecked(saved_preferences.get('adaptive_rpc', False))
                self.record_market.setChecked(saved_preferences.get('record_market', True))
                policy = saved_preferences.get('signal_policy', {})
                self.signal_mode.setCurrentIndex(self.signal_mode.findData(policy.get('mode', 'legacy')))
                self.signal_window.setValue(float(policy.get('window_seconds', 60)))
                self.signal_volatility.setValue(float(policy.get('volatility_multiplier',2)))
                self.signal_rebound.setValue(float(policy.get('rebound_pct', 0)))
                self.block_age_limit.setValue(float(policy.get('max_block_age', 5)))
                paper_model = saved_preferences.get('paper_policy',{})
                self.paper_delay.setValue(float(paper_model.get('latency_seconds',.25)))
                self.paper_fee.setText(paper_model.get('fee_quote','0'))
                self.paper_gas.setValue(paper_model.get('gas_units',0))
                costs = saved_preferences.get('entry_cost_policy', {})
                self.cost_limit.setValue(float(costs.get('maximum_pct',0)))
                self.cost_gas.setValue(float(costs.get('roundtrip_gas',400000)))
                exits = saved_preferences.get('exit_policy', {})
                self.exit_basis.setCurrentIndex(self.exit_basis.findData(exits.get('tp_sl_basis','spot')))
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
        QApplication.instance().aboutToQuit.connect(self.join_worker_at_exit)
        self.worker.start()

    def join_worker_at_exit(self):
        # QApplication.quit() can bypass closeEvent. Do not let Qt destroy a
        # running worker. Let an in-flight operation finish its durable writes;
        # this is process teardown, not a new STOP/sell command.
        self.worker.quit_event.set()
        self.worker.wait()

    def button(self, text, callback, kind=None):
        button = QPushButton(text)
        button.setCursor(Qt.PointingHandCursor)
        button.clicked.connect(callback)
        if kind:
            button.setObjectName(kind)
        self.actions.append(button)
        return button

    def field(self, value="", placeholder=""):
        field = QLineEdit(value)
        field.setPlaceholderText(placeholder)
        field.setMinimumWidth(110)
        field.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.editable.append(field)
        return field

    def tab(self, name):
        content = QWidget()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(content)
        self.tabs.addTab(scroll, name)
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 6, 4, 4)
        layout.setSpacing(METRICS["gap"])
        return layout

    def form(self, parent):
        layout = QFormLayout(parent)
        layout.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        layout.setRowWrapPolicy(QFormLayout.WrapLongRows)
        layout.setHorizontalSpacing(14)
        layout.setVerticalSpacing(8)
        layout.setLabelAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        return layout

    def disclosure(self, layout, title, content, expanded=False):
        toggle = QPushButton()
        toggle.setObjectName("disclosure")
        toggle.setCheckable(True)
        toggle.setChecked(expanded)
        toggle.setCursor(Qt.PointingHandCursor)
        def changed(opened):
            content.setVisible(opened)
            toggle.setText(('▾  ' if opened else '▸  ') + title)
        toggle.toggled.connect(changed)
        layout.addWidget(toggle)
        layout.addWidget(content)
        changed(expanded)
        return toggle


    def toggle_journal(self, expanded):
        self.activity.setVisible(expanded)
        self.journal_toggle.setText(("▾" if expanded else "▸") + "  ACTIVITY LOG · журнал событий")

    def log(self, message):
        self.activity.appendPlainText(datetime.now().strftime("%H:%M:%S") + "  " + message)

    def amount_map(self):
        return settings_controller.amount_map(self)

    def restore_amount(self):
        return settings_controller.restore_amount(self)

    def amount_unit_changed(self):
        return settings_controller.amount_unit_changed(self)

    def remember_amount(self):
        return settings_controller.remember_amount(self)

    def market_changed(self, *_):
        return autopair_controller.market_changed(self, *_)

    def invalidate_discovery(self, *_, clear_pool=True):
        return autopair_controller.invalidate_discovery(self, clear_pool=clear_pool, *_)

    def schedule_autopair(self, *_):
        return autopair_controller.schedule_autopair(self, *_)

    def auto_discover(self):
        return autopair_controller.auto_discover(self)

    def send(self, name, **data):
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

    def compare_routes(self):
        return autopair_controller.compare_routes(self)

    def paper_policy(self):
        return settings_controller.paper_policy(self)

    def entry_cost_policy(self):
        return settings_controller.entry_cost_policy(self)

    def exit_policy(self):
        return settings_controller.exit_policy(self)

    def sizing_policy(self):
        return settings_controller.sizing_policy(self)

    def signal_policy(self):
        return settings_controller.signal_policy(self)

    def trade(self, command, **extra):
        mode = self.mode.currentText()
        amount_unit = "USD" if self.amount_unit.currentData() == "usd" else "базового актива"
        if command in ("convert", "sweep") and mode != "LIVE":
            QMessageBox.information(self, "LIVE", "Converter и Sweep доступны только в LIVE")
            return
        if mode == "LIVE":
            description = ("Будут проданы все зарегистрированные target и базовые активы кошелька." if command == "sweep"
                           else f"Действие: {command.upper()}\nAMOUNT: {self.params['amount'].text()} {amount_unit}."
                           if command != "convert" else f"Converter: {'BUY за '+extra['amount']+' BNB' if extra['buy'] else 'SELL всего баланса базы → BNB'}")
            if QMessageBox.question(self, "Реальная торговля BSC", description +
                   f"\nTARGET: {self.token.text()}\nPOOL: {self.pool_input.text()}\nWALLET: {self.store.data.get('wallet_address', 'Keychain')}"
                   "\n\nБудут подписаны и отправлены реальные транзакции, включая необходимые approve. Продолжить?",
                   QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
                return
        self.send(command, mode=mode, generation=self.auto_generation, settings={k: v.text().strip() for k, v in self.params.items()},
                  paper_policy=self.paper_policy(), entry_cost_policy=self.entry_cost_policy(), exit_policy=self.exit_policy(), sizing=self.sizing_policy(), record_market=self.record_market.isChecked(), signal_policy=self.signal_policy(), interval=self.interval.value(), gas=self.gas.text(), token=self.token.text(), router=self.router.currentText(),
                  pool=self.pool_input.text(), **extra)

    def sell_position(self):
        if self.active_mode == "LIVE" and QMessageBox.question(self, "SELL POSITION", "Продать отслеживаемую позицию реальной транзакцией?",
                     QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        self.send("sell")

    def stop_bot(self):
        self.stop_pending = True
        if self.searching or not self.selection_ready:
            self.invalidate_discovery()
        else:
            self.autopair_timer.stop()
        self.worker.stop_event.set()
        self.log("STOP запрошен. Если сделка отправлена — ожидается receipt; затем закрытие позиции")
        self.footer.setText('Остановка запрошена · ожидается завершение текущей операции и закрытие позиции')
        self.metrics['state'].setText('STOPPING')
        self.update_strategy_status()
        self.update_controls()

    def select_pool(self):
        return autopair_controller.select_pool(self)

    def save_wallet(self):
        return settings_controller.save_wallet(self)

    def refresh_recovery(self):
        return recovery_controller.refresh_recovery(self)

    def compare_saved_positions(self):
        return recovery_controller.compare_saved_positions(self)

    def check_receipts(self):
        return recovery_controller.check_receipts(self)

    def show_recovery(self):
        return recovery_controller.show_recovery(self)

    def prepare_saved_position(self):
        return recovery_controller.prepare_saved_position(self)

    def load_rpc(self):
        return settings_controller.load_rpc(self)

    def add_rpc_presets(self, form, title, field, presets):
        return settings_controller.add_rpc_presets(self, form, title, field, presets)

    def balances(self):
        self.send("balance", wallet=self.wallet.text().strip())

    def cancel_pending(self):
        return recovery_controller.cancel_pending(self)

    def unlock(self):
        return recovery_controller.unlock(self)

    def remove_profile(self):
        return autopair_controller.remove_profile(self)

    def pair_clicked(self, index):
        return autopair_controller.pair_clicked(self, index)

    def update_profiles(self, dynamic=None):
        return autopair_controller.update_profiles(self, dynamic)

    def mode_changed(self):
        return settings_controller.mode_changed(self)

    def reset_price_display(self):
        return position_presenter.reset_price_display(self)

    def display_price(self, value, digits=8):
        return position_presenter.display_price(self, value, digits)

    def capture_usd_rates(self):
        return position_presenter.capture_usd_rates(self)

    def refresh_currency(self):
        return position_presenter.refresh_currency(self)

    def refresh_trade_details(self):
        return position_presenter.refresh_trade_details(self)

    def refresh_pnl(self):
        return position_presenter.refresh_pnl(self)

    def update_state_badge(self):
        return position_presenter.update_state_badge(self)

    def update_strategy_status(self):
        return position_presenter.update_strategy_status(self)

    def update_quote_age(self):
        return position_presenter.update_quote_age(self)

    def refresh_timings(self):
        return position_presenter.refresh_timings(self)

    def update_controls(self):
        return position_presenter.update_controls(self)

    def on_event(self, name, payload):
        message = Event.from_wire(name, payload)
        name, payload = message.kind, message.payload
        if name == EventKind.DISCOVERY_EVENT:
            generation, event_name, value = payload
            if generation == self.auto_generation:
                if event_name == EventKind.ERROR:
                    self.pool_label.setText("Ошибка RPC/проверки · повторите AutoPair или CHECK POOL")
                self.on_event(event_name, value)
            return
        if name == EventKind.BUSY:
            self.busy = payload
            if not payload:
                self.searching = False
        elif name == EventKind.EXIT_RETRY:
            self.exit_retry = payload
        elif name == EventKind.ERROR:
            self.ui_error = str(payload)
            self.journal_toggle.setChecked(True)
            self.footer.setText("ОШИБКА: " + payload)
            QMessageBox.warning(self, "Операция прервана", payload)
        elif name == EventKind.PRICE_CONTEXT:
            self.same_block_cache = payload.get('same_block_cache', False)
            if self.price_source != payload['source']:
                self.reset_price_display()
            self.market_block = payload.get('block')
            self.market_block_timestamp = payload.get('block_timestamp')
            self.market_rpc_source = payload.get('rpc_source', 'BSC')
            self.price_source = payload['source']
            self.display_unit = ('условных единиц (DEMO)' if payload['source'] == 'DEMO' else
                next((name for name, addr in profiles().items()
                      if addr.lower() == payload['quote'].lower()), payload['quote']))
            if payload['source'] == 'BSC' and self.isVisible():
                self.usd.set_token(payload['quote'])
                self.gas_usd.set_token(WBNB if (self.mode.currentText() == 'LIVE' or (self.mode.currentText() == 'PAPER' and (self.cost_limit.value() > 0 or self.paper_gas.value() > 0))) and payload['quote'].lower() != WBNB.lower() else '')
        elif name == EventKind.PRICE:
            if self.mode.currentText() != 'DEMO' and not self.selection_ready:
                return
            self.last_price = Decimal(str(payload))
            self.chart.add(payload)
            self.metrics["price"].setText(self.display_price(payload))
            self.metrics['price'].setToolTip(str(payload))
            self.last_quote_at = time.monotonic()
            self.update_quote_age()
        elif name == EventKind.TRADE_MARKER:
            if payload['mode'] == self.mode.currentText() and self.chart.values:
                self.chart.mark(payload['side'], payload['price'])
        elif name == EventKind.AUTOPAIR:
            self.pool_label.setText({"PENDING": "PENDING · ожидается ликвидность; повторите AutoPair",
                                     "NOT_FOUND": "Пулы не найдены",
                                     "INVALID_CONTRACT": "По адресу нет контракта BSC",
                                     "CATALOG_TOKEN": "Введён адрес базового профиля; выберите PAIR вручную",
                                     "UNSUPPORTED_POOL": "Неподдерживаемый пул или базовая пара",
                                     "AMBIGUOUS": "Найдено несколько пар; выберите маршрут явно"}.get(payload, self.pool_label.text()))
        elif name == EventKind.ROUTE_COMPARISON_ERROR:
            self.route_comparison.setText('Сравнение не выполнено: ' + str(payload))
        elif name == EventKind.ROUTE_COMPARISON:
            lines = [f"AMOUNT {payload['amount']} в базе · блок {payload['block']}"]
            for row in payload['rows']:
                pool = row['pool']
                prefix = f'{pool.router}/{pool.fee} {pool.address[:8]}…'
                if row['error']:
                    lines.append(f"{prefix}: {row['error']}")
                else:
                    cost = row['modeled_cost_pct']
                    suffix = f'; с моделью газа {cost:.3f}%' if cost is not None else '; без газа'
                    lines.append(f"{prefix}: BUY {row['target_out']:.8g} target; потери цикла {row['loss_pct']:.3f}%{suffix}")
            lines.append('Порядок: меньше потерь цикла. Другие базы исключены. Это котировки, не симуляция token tax.')
            self.route_comparison.setText('\n'.join(lines))
        elif name == EventKind.POOLS:
            self.route_comparison.setText('Сравнение маршрутов ещё не выполнено')
            self.selection_ready = False
            self.pool_input.clear()
            self.candidates.clear()
            self.pool_label.setText("Выберите проверенный маршрут" if payload else "Пул не выбран")
            for pool in payload:
                self.candidates.addItem(pool.label, pool)
        elif name == EventKind.SELECTED:
            self.reset_price_display()
            self.display_unit = next((name for name, addr in profiles().items()
                                      if addr.lower() == payload.quote.lower()), payload.quote)
            self.selection_ready = True
            self.ui_error = ""
            self.remember_amount()
            self.router.blockSignals(True)
            self.quote.blockSignals(True)
            self.router.setCurrentText(payload.router)
            names = catalog(self.store, payload.router)
            pair = next((name for name, token in names.items() if token.lower() == payload.quote.lower()), "ALL")
            self.quote.setCurrentText(pair)
            self.router.blockSignals(False)
            self.quote.blockSignals(False)
            self.amount_key = preferences.pair_key(payload.router, pair if pair != 'ALL' else payload.quote.lower())
            self.restore_amount()
            self.pool_input.setText(payload.address)
            self.token.setText(payload.token)
            self.pool_label.setText(payload.label + "\nБазовый актив исполнения: " + self.display_unit)
            pair_label = pair if pair != 'ALL' else f'{payload.quote[:8]}…{payload.quote[-6:]}'
            self.market_summary.setText(f"TARGET {payload.token[:8]}…{payload.token[-6:]} / {pair_label} · {payload.router} · пул {payload.address[:8]}…{payload.address[-6:]}")
            self.market_summary.setToolTip(f"TARGET: {payload.token}\nБаза: {pair_label} ({payload.quote})\nПул: {payload.address}")
        elif name == EventKind.SWEEP_REPORT:
            status = {"completed": "завершён", "stopped": "остановлен — частичный результат",
                      "interrupted": "прерван — частичный результат"}.get(payload.get("status"), "результат")
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
                self.log("SWEEP: пропущены цели; проверьте/выберите их для этого кошелька: " + ", ".join(payload["skipped"]))
        elif name == EventKind.WALLET:
            self.wallet.setText(payload)
        elif name == EventKind.PROFILES:
            self.update_profiles(payload)
        elif name == EventKind.PROFILE_REMOVED:
            self.schedule_autopair()
        elif name == EventKind.BALANCES:
            self.log("Балансы: " + "; ".join(f"{k}={v}" for k, v in payload.items() if v not in ("0", "0.0")))
            for row in range(self.table.rowCount()):
                symbol = self.table.item(row, 0).text()
                value = payload.get(symbol, "—")
                item = QTableWidgetItem(value)
                try:
                    if float(value) > 0:
                        item.setForeground(QColor(COLORS["positive"]))
                except ValueError:
                    pass
                self.table.setItem(row, 2, item)
        elif name == EventKind.POSITION_COMPARISON_ERROR:
            self.position_comparison.setText("Сверка не выполнена: " + payload)
        elif name == EventKind.POSITION_COMPARISON:
            lines = [f"Снимок балансов: блок {payload['block']}. Локальные записи не изменены."]
            for row in payload['rows']:
                scale = Decimal(10)**row['decimals']
                lines.append(f"{row['owner']} · {row['token']} · пул {row['pool']}\n" +
                    f"Записано: {Decimal(row['saved_raw'])/scale}; в кошельке: {Decimal(row['actual_raw'])/scale} · " +
                    ('совпадает' if row['matches'] else 'РАСХОЖДЕНИЕ'))
            if not payload['rows']:
                lines.append('Сохранённых позиций нет.')
            self.position_comparison.setText('\n'.join(lines))
        elif name == EventKind.ACCOUNTING_REPORT:
            self.accounting_text.setPlainText(payload)
        elif name == EventKind.RECEIPT_REVIEW:
            self.receipt_result.setText(payload)
            self.refresh_recovery()
        elif name == EventKind.STATUS:
            self.refresh_recovery()
            self.quote_unavailable = payload.get('quote_unavailable', False)
            self.exit_retry = payload.get('exit_retry')
            health = payload.get('rpc_health', [])
            self.rpc_health_label.setText(' · '.join(
                row['source'] + ': ' + (f"{row['median_ms']:.0f} мс" if row['median_ms'] is not None else 'нет замеров') +
                f", ошибок подряд {row['consecutive_errors']}" + (' (предпочтительный)' if row['preferred'] else '')
                for row in health))
            self.signal_notice = payload.get('signal_notice', '')
            self.wait_reason = payload.get('wait_reason', '')
            self.entry_notice = payload.get('entry_notice', '')
            quote_exit = payload.get('exit_basis') == 'quote' and Decimal(payload.get('position', '0')) > 0
            self.exit_status.setVisible(quote_exit)
            result = payload.get('exit_return')
            self.exit_status.setText('TP/SL по продаже позиции: ' +
                (f'{Decimal(result):+.2f}%' if result is not None and not payload.get('quote_unavailable') else 'ожидание свежей котировки') +
                (' · стоимость по модели PAPER, без token tax' if payload.get('mode') == 'PAPER' else ' · без газа и token tax'))
            self.halt_reason = payload.get('halt_reason', '')
            self.running, self.active_mode, self.locked = payload["running"], payload["mode"], payload["locked"]
            if not self.running and not self.busy and not self.worker.stop_event.is_set():
                self.stop_pending = False
            active = (payload['mode'] == self.mode.currentText() and
                      (payload['running'] or float(payload['position']) > 0) and
                      (self.mode.currentText() == 'DEMO' or self.selection_ready))
            self.base_price = Decimal(payload['base']) if active and Decimal(payload['base']) > 0 else None
            self.chart.reference_base = self.base_price
            self.metrics['base'].setText(self.display_price(self.base_price))
            age = payload.get('base_age')
            self.metrics['base'].setToolTip(payload.get('base_reason', '') +
                (f' · возраст {age:.1f} с' if age is not None else ''))
            self.metrics["position"].setText(f"{float(payload['position']):.8g}")
            self.metrics['position'].setToolTip(payload['position'])
            levels = payload.get('levels', {}) if active else {}
            self.chart.levels = {key: value for key, value in levels.items() if float(value) > 0}
            self.display_position = Decimal(payload['position']) if active else Decimal(0)
            self.update_strategy_status()
            self.chart.update()
            self.levels_label.setText(' · '.join(f'{title}: {float(levels[key]):.8g}'
                if key in levels and float(levels[key]) > 0 else f'{title}: —'
                for key, title in [('DIP', 'Вход DIP'), ('ENTRY', 'ENTRY'), ('TP', 'TP'), ('SL', 'SL')]))
            self.pnl_status = dict(payload)
            self.refresh_pnl()
        self.update_controls()

    def closeEvent(self, event):
        if (self.busy or self.running or getattr(self, "stop_pending", False)
                or getattr(self, "display_position", 0) > 0):
            QMessageBox.information(self, "Сначала STOP", "Остановите BOT и дождитесь завершения текущей операции перед закрытием")
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
            preferences.save(self.store, {"version": 1,
                "selection": {"router": self.router.currentText(), "pair": self.quote.currentText()},
                "pair_amounts": self.pair_amounts,
                "usd_pair_amounts": self.usd_pair_amounts,
                "signal_policy": self.signal_policy(),
                "exit_policy": self.exit_policy(),
                "entry_cost_policy":self.entry_cost_policy(),
                "paper_policy":self.paper_policy(),
                "sizing": self.sizing_policy(),
                "record_market": self.record_market.isChecked(),
                "adaptive_rpc": self.adaptive_rpc.isChecked(),
                "settings": {key: field.text().strip() for key, field in self.params.items()},
                "gas": self.gas.text().strip(), "interval": str(self.interval.value())})
        except (ValueError, OSError):
            QMessageBox.warning(self, "Настройки не сохранены",
                                "Не удалось сохранить параметры. Предыдущие настройки сохранены, если запись не была заменена.")
        self.usd.set_token('')
        self.gas_usd.set_token('')
        event.accept()
