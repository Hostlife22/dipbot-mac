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
from dipbot.persistence.storage import Store, Vault, data_dir
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
        return self.usd_pair_amounts if self.amount_currency == 'usd' else self.pair_amounts

    def restore_amount(self):
        default = '1' if self.amount_currency == 'usd' else '0.02'
        self.params['amount'].setText(self.amount_map().get(self.amount_key, default))

    def amount_unit_changed(self):
        self.remember_amount()
        self.amount_currency = self.amount_unit.currentData()
        self.restore_amount()

    def remember_amount(self):
        try:
            self.amount_map()[self.amount_key] = preferences.positive_amount(self.params["amount"].text())
        except ValueError:
            pass  # Invalid edits never replace a previously valid per-pair amount.

    def market_changed(self, *_):
        if not self.quote.currentText():
            return
        self.remember_amount()
        self.amount_key = preferences.pair_key(self.router.currentText(), self.quote.currentText())
        self.restore_amount()
        self.invalidate_discovery()

    def invalidate_discovery(self, *_, clear_pool=True):
        self.auto_generation += 1
        self.worker.discovery_generation = self.auto_generation
        self.route_comparison.setText("Сравнение маршрутов ещё не выполнено")
        self.autopair_timer.stop()
        self.selection_ready = False
        self.reset_price_display()
        self.candidates.clear()
        if clear_pool:
            self.pool_input.clear()
        self.pool_label.setText("Пул не проверен · выполните AutoPair или CHECK POOL")
        self.market_summary.setText(self.pool_label.text())
        self.update_controls()

    def schedule_autopair(self, *_):
        self.invalidate_discovery()
        if self.worker.chain is not None and not self.running and len(self.token.text().strip()) == 42:
            self.autopair_timer.start()

    def auto_discover(self):
        if self.running or self.worker.chain is None:
            return
        if self.busy:
            self.autopair_timer.start()
            return
        self.send("discover", token=self.token.text().strip(), quote=self.quote.currentText(),
                  router=self.router.currentText())

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
        reference = self.candidates.currentData()
        if reference is None:
            self.route_comparison.setText('Сначала найдите пулы через AutoPair')
            return
        self.route_comparison.setText('Сравнение на заданную сумму…')
        self.send('compare_routes', reference=reference,
            pools=[self.candidates.itemData(i) for i in range(self.candidates.count())],
            amount=self.params['amount'].text().strip(), sizing=self.sizing_policy(),
            maximum=self.params['max_roundtrip_loss'].text().strip(),
            cost_policy=self.entry_cost_policy(), gas=self.gas.text().strip())

    def paper_policy(self):
        return {'gas_units':int(self.paper_gas.value()),'latency_seconds':self.paper_delay.value(),'fee_quote':self.paper_fee.text().strip()}

    def entry_cost_policy(self):
        return {'maximum_pct':str(self.cost_limit.value()),'roundtrip_gas':int(self.cost_gas.value())}

    def exit_policy(self):
        return {'continue_after_risk_exit':self.continue_after_exit.isChecked(),
                'tp_sl_basis':self.exit_basis.currentData(),
                **{key: str(field.value()) for key, field in self.exit_fields.items()}}

    def sizing_policy(self):
        return {'unit':self.amount_unit.currentData(), 'reserve_bnb':self.gas_reserve.text().strip()}

    def signal_policy(self):
        return {'mode': self.signal_mode.currentData(), 'window_seconds': self.signal_window.value(),
                'rebound_pct': str(self.signal_rebound.value()), 'max_block_age': self.block_age_limit.value(),
                'volatility_multiplier':str(self.signal_volatility.value())}

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
        pool = self.candidates.currentData()
        if pool:
            self.send("select", pool=pool)

    def save_wallet(self):
        key = self.key.text().strip()
        self.key.clear()
        self.send("wallet", key=key)

    def refresh_recovery(self):
        operation = self.store.data.get('operation')
        positions = self.store.data.get('positions', {})
        signature = repr((operation, positions))
        if getattr(self, '_recovery_signature', None) == signature:
            return
        self._recovery_signature = signature
        self.recovery_notice.setVisible(bool(operation or positions))
        self.recovery_notice.setText(f'Восстановление LIVE · сохранённых позиций: {len(positions)}' +
            (' · незавершённая операция · открыть' if operation else ' · открыть'))
        details = ['Локальные записи, не подтверждённый текущий баланс. Торговля автоматически не запускается.']
        if operation:
            details.append('Кошелёк операции: ' + str(operation.get('wallet', 'не указан')))
            for tx in operation.get('transactions', []):
                details.append(str(tx.get('hash', 'hash не записан')) + ' · ' + str(tx.get('status', 'неизвестно')) + ' · ' +
                    {'prepared': 'записана до отправки; отправка могла произойти',
                     'submitted': 'RPC принял отправку; ожидается receipt',
                     'receipt_validated': 'receipt проверен',
                     'same_nonce_resolved': 'nonce занят подтверждённой альтернативой'}.get(tx.get('stage'), 'этап не записан'))
                if tx.get('superseded_by'):
                    details.append('Подтверждённая альтернатива: ' + str(tx['superseded_by']))
                if tx.get('replaces'):
                    details.append('Попытка отмены: ' + str(tx['replaces']))
                if tx.get('broadcast_route') == 'custom':
                    details.append('Маршрут отправки: отдельный RPC (endpoint хранится только в Keychain)')
                review = tx.get('receipt_review')
                if review:
                    replacement = review.get('replacement_search', {})
                    if replacement.get('hash'):
                        details.append('Тот же nonce: ' + replacement['hash'] + ' · блок ' + str(replacement['block']))
                    details.append(PENDING_LABELS.get(review.get('state'), 'Неизвестный результат сверки') +
                                   ' · повторная отправка автоматически запрещена')
            if not operation.get('transactions'):
                details.append('Hash транзакции не записан; перед снятием блокировки проверьте балансы.')
        selected = self.saved_positions.currentData()
        self.saved_positions.clear()
        for key, position in positions.items():
            pool = position['pool']
            amount = Decimal(position['amount']) / Decimal(10)**pool['token_decimals']
            label = f"{key.split(':')[0]} · {pool['router']} · {pool['address']} · TARGET {amount}"
            self.saved_positions.addItem(label, key)
            details.append(label)
        index = self.saved_positions.findData(selected)
        if index >= 0:
            self.saved_positions.setCurrentIndex(index)
        self.recovery_details.setText('\n'.join(details))

    def compare_saved_positions(self):
        self.position_comparison.setText("Чтение балансов сохранённых позиций…")
        self.send("compare_positions")

    def check_receipts(self):
        self.receipt_result.setText("Проверка receipts через RPC…")
        self.send("reconcile", gas=self.gas.text())

    def show_recovery(self):
        self.tabs.setCurrentIndex(1)
        self.tabs.widget(1).ensureWidgetVisible(self.recovery_group)

    def prepare_saved_position(self):
        if self.running or self.busy or self.display_position > 0:
            return
        record = self.store.data.get('positions', {}).get(self.saved_positions.currentData())
        if not record:
            return
        self.mode.setCurrentText('LIVE')
        self.invalidate_discovery()
        self.router.setCurrentText(record['pool']['router'])
        self.token.setText(record['pool']['token'])
        self.pool_input.setText(record['pool']['address'])
        self.autopair_timer.stop()
        self.market_toggle.setChecked(True)
        self.tabs.setCurrentIndex(0)
        self.log('Пул подготовлен. Подключите RPC и выполните CHECK POOL; сохранённая запись не заменяет проверку сети.')

    def load_rpc(self):
        try:
            primary = Vault().get("rpc")
            backup = Vault().get('backup_rpc')
            websocket = Vault().get('ws_rpc')
            broadcaster = Vault().get('send_rpc')
            if broadcaster is not None:
                self.send_rpc.setText(broadcaster)
            if websocket is not None:
                self.ws_rpc.setText(websocket)
            if primary is not None:
                self.rpc.setText(primary)
            if backup is not None:
                self.backup_rpc.setText(backup)
        except Exception:
            QMessageBox.warning(self, "Keychain", "Не удалось прочитать RPC из Keychain")

    def add_rpc_presets(self, form, title, field, presets):
        combo = QComboBox()
        for label, url in presets:
            combo.addItem(label, url)
        self.editable.append(combo)
        form.addRow(title, combo)
        def selected(index):
            url = combo.itemData(index)
            if url is not None:
                field.setText(url)
            else:
                field.setFocus()
        def edited(text):
            index = next((i for i,(_,url) in enumerate(presets) if url == text.strip()), len(presets)-1)
            combo.blockSignals(True)
            combo.setCurrentIndex(index)
            combo.blockSignals(False)
        combo.currentIndexChanged.connect(selected)
        field.textChanged.connect(edited)
        field.setText(presets[0][1])
        return combo

    def balances(self):
        self.send("balance", wallet=self.wallet.text().strip())

    def cancel_pending(self):
        if self.mode.currentText() != 'LIVE':
            QMessageBox.information(self, 'Отмена pending', 'Переключитесь в LIVE: отмена отправляет реальную транзакцию и расходует газ.')
            return
        try:
            from dipbot.execution.cancellation import cancellation_plan
            plan = cancellation_plan(self.store.data.get('operation'), Decimal(self.gas.text()))
        except Exception as exc:
            QMessageBox.warning(self, 'Отмена недоступна', str(exc))
            return
        fee = Decimal(plan['maximum_fee_wei'])/Decimal(10)**18
        gas = Decimal(plan['gas_price'])/Decimal(10)**9
        if QMessageBox.question(self, 'Отмена pending',
            f"Попытка отмены {plan['attempt']}/3: {plan['original_hash']}\nNonce {plan['nonce']}; перевод 0 BNB себе.\n"
            f"GAS {gas} gwei; комиссия до {fee} BNB.\n"
            'Исходная сделка может подтвердиться раньше отмены. Блокировка останется до сверки балансов. Продолжить?',
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes:
            self.send('cancel_pending', mode='LIVE', gas=self.gas.text(),
                expected_hash=plan['original_hash'], expected_gas_price=plan['gas_price'])

    def unlock(self):
        if QMessageBox.question(self, "Сверка", "Вы проверили все receipts и фактические балансы?\n"
            "Кэш позиций будет сброшен. Остатки можно продать через SELL WALLET → BNB.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes:
            self.send("unlock", gas=self.gas.text())

    def remove_profile(self):
        row = self.table.currentRow()
        if row >= 0:
            self.send("remove_profile", symbol=self.table.item(row, 0).text(), wallet=self.wallet.text())

    def pair_clicked(self, index):
        if self.busy or self.running:
            return
        self.quote.setCurrentText(self.table.item(index.row(), 0).text())
        self.tabs.setCurrentIndex(0)

    def update_profiles(self, dynamic=None):
        pairs = profiles() | (dynamic if dynamic is not None else self.store.data.get("dynamic_profiles", {}))
        selected = self.quote.currentText() or "WBNB"
        blocked = self.quote.blockSignals(True)
        self.quote.clear()
        self.quote.addItems(["ALL"] + sorted(pairs, key=str.casefold))
        self.quote.setCurrentText(selected if selected == "ALL" or selected in pairs else "WBNB")
        self.quote.blockSignals(blocked)
        if hasattr(self, "amount_key") and selected != self.quote.currentText():
            self.market_changed()
        self.table.setRowCount(len(pairs))
        for row, (symbol, token) in enumerate(sorted(pairs.items(), key=lambda x: x[0].casefold())):
            for col, value in enumerate([symbol, token, "—"]):
                self.table.setItem(row, col, QTableWidgetItem(value))

    def mode_changed(self):
        mode = self.mode.currentText()
        descriptions = {"DEMO": "DEMO · Локальный рынок и виртуальный баланс. RPC и кошелёк не нужны.",
                        "PAPER": "PAPER · Реальная цена BSC, виртуальные сделки. Выберите пул и нажмите START.",
                        "LIVE": "LIVE · Реальные средства. Укажите RPC, сохраните кошелёк и проверьте выбранный пул."}
        self.banner.setText(descriptions[mode])
        self.banner.setVisible(mode == 'LIVE')
        self.mode_badge.setToolTip(descriptions[mode])
        self.mode_badge.setText(mode + (" · реальные средства" if mode == "LIVE" else " · виртуальные сделки" if mode == "PAPER" else " · модель"))
        self.mode_badge.setProperty('mode', mode)
        self.mode_badge.style().unpolish(self.mode_badge)
        self.mode_badge.style().polish(self.mode_badge)
        self.banner.setProperty('mode', mode)
        self.banner.style().unpolish(self.banner)
        self.banner.style().polish(self.banner)
        if mode == 'DEMO':
            self.display_unit = 'условных единиц (DEMO)'
        elif hasattr(self, 'quote'):
            self.display_unit = self.quote.currentText() if self.quote.currentText() != 'ALL' else 'BASE'
        if hasattr(self, 'chart'):
            self.reset_price_display()
        if hasattr(self, "selection_ready"):
            self.update_controls()
        if mode == "DEMO":
            self.market_summary.setText("Рынок: DEMO · локальная модель")
        elif hasattr(self, "pool_label"):
            self.market_summary.setText(self.pool_label.text() if "DEMO" not in self.pool_label.text() else
                                        "Рынок не выбран · AutoPair / CHECK POOL")

    def reset_price_display(self):
        self.market_block = self.market_block_timestamp = None
        self.market_rpc_source = "BSC"
        self.chart.clear()
        self.usd.set_token('')
        self.gas_usd.set_token('')
        self.base_price = None
        self.last_quote_at = None
        self.last_price = None
        self.display_position = Decimal(0)
        self.price_source = self.mode.currentText()
        self.metrics['price'].setText('—')
        self.metrics['base'].setText('—')
        self.levels_label.setText('Вход DIP: — · ENTRY: — · TP: — · SL: —')
        self.update_quote_age()

    def display_price(self, value, digits=8):
        if value is None:
            return '—'
        if self.price_source in ('DEMO', 'REPLAY'):
            return f'{float(value):.{digits}g}'
        return price_text(value, self.usd.current(), digits)

    def capture_usd_rates(self):
        for source in (self.usd, self.gas_usd):
            self.worker.rates.update(source.token, source.current(), source.received_at)

    def refresh_currency(self):
        if not hasattr(self, 'usd') or not hasattr(self, 'price_source'):
            return
        rate = self.usd.current() if self.price_source not in ('DEMO', 'REPLAY') else None
        self.chart.usd_rate = rate
        self.chart.reference_base = self.base_price
        unit = 'USD' if rate is not None else ('DEMO' if self.price_source == 'DEMO' else self.display_unit)
        if len(unit) > 16:
            unit = unit[:6] + '…' + unit[-4:]
        self.metric_captions['price'].setText('ЦЕНА, ' + unit)
        self.metric_captions['base'].setText('БАЗА DIP, ' + unit)
        self.metrics['price'].setText(self.display_price(getattr(self, 'last_price', None)))
        self.metrics['base'].setText(self.display_price(self.base_price))
        if getattr(self, 'last_price', None) is not None:
            self.metrics['price'].setToolTip(f'{self.last_price} {self.display_unit} / TARGET · USD — ориентировочный пересчёт')
        self.chart.update()
        self.refresh_pnl()

    def refresh_trade_details(self):
        payload = getattr(self, 'pnl_status', {})
        same = payload.get('mode') == self.mode.currentText() and self.mode.currentText() != 'DEMO'
        detail = payload.get('trade_detail') if same else None
        def usd(value):
            if value is None:
                return '— (нет данных)'
            return '≈ $' + price_text(Decimal(value), digits=6)
        if detail:
            rows = [('Средняя цена BUY (без доп. расходов)', 'buy_price_usd'),
                    ('Средняя цена SELL (без доп. расходов)', 'sell_price_usd'),
                    ('Обмен на входе', 'entry_gross_usd'), ('Расходы входа', 'entry_fee_usd'),
                    ('Всего затрачено', 'entry_total_usd'), ('Получено от продажи', 'exit_gross_usd'),
                    ('Расходы выхода', 'exit_fee_usd'), ('Результат закрытия', 'net_usd')]
            self.trade_details.setText('\n'.join(label + ': ' + usd(detail.get(key)) for label, key in rows) +
                '\nКомиссия пула уже в суммах обмена; повторно не вычитается. ' +
                ('Расходы PAPER — модель.' if self.mode.currentText() == 'PAPER' else 'Расходы LIVE — записанный газ; неполный учёт не оценивается.') +
                ('\nЧастичный/неполный выход: итог неизвестен.' if detail.get('complete') is False else ''))
        else:
            self.trade_details.setText('Нет данных об исполнении для выбранного режима/рынка')
        estimate = payload.get('open_estimate') if same else None
        age = time.monotonic()-estimate['at'] if estimate else None
        if not self.display_position:
            text = 'Открытая позиция, USD: —'
        elif estimate and 0 <= age <= .55 and not self.quote_unavailable:
            text = 'Оценка продажи: ' + usd(estimate['value_usd']) + ' · P&L позиции: ' + usd(estimate['pnl_usd'])
            text += ' · без будущего газа SELL' if estimate['excludes_exit_gas'] else ' · по модели PAPER'
            text += f' · {age:.1f} с назад'
        else:
            text = 'Открытая позиция, USD: — (нет свежей котировки продажи)'
        if same and self.display_position and not payload.get('running'):
            text += ' · наблюдение без автоторговли'
            error = payload.get('position_watch_error')
            if error:
                text += ' · ' + error
        self.position_estimate.setText(text)

    def refresh_pnl(self):
        payload = getattr(self, 'pnl_status', None)
        if payload is None:
            return
        self.refresh_trade_details()
        mode = self.mode.currentText()
        text = f"{mode} · " + ('BOT работает' if self.running else 'BOT остановлен')
        historical = payload.get('historical_usd') if mode != 'DEMO' and payload['mode'] == mode else None
        if historical is not None:
            value = historical.get('value')
            if value is None:
                result = '— (неполный USD-учёт)' if historical.get('missing') else '— (нет закрытых сделок)'
            else:
                usd = Decimal(value)
                amount = format(abs(usd), '.2f') if abs(usd) >= Decimal('.01') or not usd else price_text(abs(usd), digits=4)
                result = '≈ ' + ('−' if usd < 0 else '+' if usd > 0 else '') + '$' + amount
            suffix = (' · по модели PAPER' if mode == 'PAPER' else
                      ' · газ BUY/SELL учтён' if historical['includes_gas'] else ' · без газа')
            self.footer.setText(text + ' · Закрытый P&L: ' + result + suffix + (' · LIVE LOCKED' if self.locked else ''))
            self.footer.setToolTip('USD по сохранённым ориентировочным курсам на моменты исполнения (DEX Screener, возраст до 90 с). '
                'Смена текущего курса не пересчитывает закрытый результат. LIVE: отслеживаемые позиции этого кошелька '
                'с начала нового USD-учёта, включая учтённые закрытия Sweep и распределённый газ. Прочие расходы показаны отдельно в USD-учёте; старые неполные записи не восстанавливаются догадкой. '
                f"Закрыто: {historical.get('closed', 0)}, неполных: {historical.get('missing', 0)}. PAPER учитывает заданную стоимость операций по модели; token tax не учтён.")
            return
        if payload['mode'] != mode or payload['realized'] == '—':
            result = '—'
        else:
            value = Decimal(payload['realized'])
            token = payload.get('pnl_quote', '').lower()
            rate = (self.usd.current() if mode != 'DEMO' and self.price_source != 'REPLAY'
                    and token and self.usd.token == token else None)
            if rate is not None:
                usd = value * rate
                amount = format(abs(usd), '.2f') if abs(usd) >= Decimal('0.01') or not usd else price_text(abs(usd), digits=4)
                result = '≈ ' + ('−' if usd < 0 else '+' if usd > 0 else '') + '$' + amount
            else:
                result = '— (USD недоступен)' if mode != 'DEMO' else '— (DEMO без USD)'
        self.footer.setText(text + ' · Закрытый P&L: ' + result +
                            ' · без газа' + (' · LIVE LOCKED' if self.locked else ''))
        self.footer.setToolTip('Результат закрытых сделок. USD — пересчёт по текущему курсу базового актива, '
                              'не исторический долларовый P&L. PAPER не учитывает газ и token tax.')

    def update_state_badge(self):
        """Presentation of existing worker/UI states; never changes trading decisions."""
        tone = ''
        if self.stop_pending:
            text, tone = 'STOPPING', 'warning'
        elif getattr(self, 'exit_retry', None):
            text, tone = 'EXIT RPC', 'warning'
        elif self.locked and self.mode.currentText() == 'LIVE':
            text, tone = 'LOCKED', 'danger'
        elif self.searching:
            text = 'SEARCH'
        elif self.busy:
            text = {'buy': 'BUYING', 'sell': 'SELLING', 'sweep': 'SWEEP',
                    'convert': 'CONVERT'}.get(getattr(self, 'ui_command', ''), 'WAIT')
        elif self.worker.execution_monitor is not None:
            text = 'EXECUTING'
        elif self.running and getattr(self, 'quote_unavailable', False):
            text, tone = 'WAIT RPC', 'warning'
        elif self.running and self.last_quote_at is not None and time.monotonic()-self.last_quote_at > .55:
            text, tone = 'STALE', 'warning'
        elif not self.running and (getattr(self, 'halt_reason', '') or getattr(self, 'ui_error', '')):
            text, tone = 'ERROR', 'danger'
        elif self.display_position > 0:
            text, tone = 'POSITION', 'positive'
        elif self.running:
            text = {'rebound': 'REBOUND', 'cooldown': 'COOLDOWN',
                    'warmup': 'WARMUP', 'baseline': 'BASELINE'}.get(getattr(self, 'wait_reason', ''), 'WAIT DIP')
            tone = 'positive'
        elif 'PENDING' in self.pool_label.text() and self.mode.currentText() != 'DEMO':
            text, tone = 'PENDING', 'warning'
        else:
            text = 'IDLE'
        self.metrics['state'].setText(text)
        set_tone(self.metrics['state'], tone)
        set_tone(self.strategy_status, tone)

    def update_strategy_status(self):
        self.update_state_badge()
        if self.searching and not self.stop_pending:
            self.strategy_status.setText('Поиск · проверяется адрес, ликвидность и доступные маршруты')
            return
        if getattr(self, 'ui_error', '') and not self.running and not self.stop_pending and not self.locked:
            self.strategy_status.setText('Ошибка операции · ' + self.ui_error)
            return
        if not self.running and not self.busy and not self.stop_pending and not self.locked and not getattr(self, 'selection_ready', False) and self.mode.currentText() != 'DEMO':
            self.strategy_status.setText(self.pool_label.text() if 'DEMO' not in self.pool_label.text() else 'Выберите рынок · раскройте AutoPair')
            return
        retry = getattr(self, 'exit_retry', None)
        if retry:
            remaining = max(0, retry['retry_at']-time.monotonic())
            self.strategy_status.setText(('Останавливается · ' if self.stop_pending else '') + f"Позиция открыта, выход ожидает RPC · {retry['error']} · попытка {retry['attempt']}/{retry['limit']} через {remaining:.1f} с")
            return
        if self.stop_pending:
            self.strategy_status.setText('Останавливается · ожидается завершение операции и закрытие позиции')
            return
        if self.busy and getattr(self, 'ui_command', '') in ('buy', 'sell', 'convert', 'sweep'):
            self.strategy_status.setText({'buy': 'Покупка', 'sell': 'Продажа', 'convert': 'Конвертация', 'sweep': 'Продажа остатков'}[self.ui_command] + ' · ожидается результат исполнения')
            return
        if self.worker.execution_monitor is not None and self.mode.currentText() == self.worker.mode:
            self.strategy_status.setText('Сделка выполняется · цена обновляется отдельно; ожидается результат исполнения')
            return
        if getattr(self, 'quote_unavailable', False) and self.running:
            self.strategy_status.setText('Нет котировок · повтор чтения; ' +
                ('позиция открыта, TP/SL временно недоступны' if self.display_position > 0 else 'новые входы запрещены'))
            return
        if self.locked and self.mode.currentText() == 'LIVE':
            text = 'Требуется сверка LIVE · проверьте незавершённую операцию'
        elif getattr(self, 'halt_reason', '') and not self.running:
            action = (' · позиция сохранена; после устранения причины повторите SELL POSITION или STOP'
                      if self.display_position > 0 else ' · проверьте причину перед START')
            text = 'Остановлен из-за ошибки · ' + self.halt_reason + action
        elif self.running and self.last_quote_at is not None and time.monotonic()-self.last_quote_at > .55:
            text = 'Котировка устарела · нет обновлений более 0,55 с'
        elif getattr(self, 'entry_notice', '') and self.running:
            text = self.entry_notice if self.entry_notice.startswith('Пауза после выхода:') else 'Вход пропущен · ' + self.entry_notice
        elif getattr(self, 'signal_notice', '') and self.running and not self.display_position:
            text = self.signal_notice
        elif self.display_position > 0:
            text = 'Позиция открыта' + (' · автоматическая стратегия остановлена' if not self.running else '')
        elif self.running:
            text = 'Ждёт падения до DIP' if 'DIP' in self.chart.levels else 'Получает котировки · формирует базу DIP'
        else:
            text = 'Готов к запуску' if self.mode.currentText() == 'DEMO' or getattr(self, 'selection_ready', False) else 'Выберите рынок · раскройте AutoPair'
        fresh = self.last_quote_at is not None and time.monotonic()-self.last_quote_at <= .55
        if fresh and self.last_price is not None and self.last_price > 0:
            for key in (('TP', 'SL') if self.display_position > 0 else ('DIP',) if self.running else ()):
                if key == 'DIP' and (getattr(self, 'wait_reason', '') in ('rebound', 'cooldown', 'warmup') or getattr(self, 'entry_notice', '')):
                    continue
                if key not in self.chart.levels:
                    continue
                level = Decimal(str(self.chart.levels[key]))
                distance = ((level-self.last_price) if key == 'TP' else (self.last_price-level))/self.last_price*100
                text += f' · до {key}: {distance:.2f}%' if distance > 0 else f' · {key}: уровень достигнут'
        if self.display_position > 0 and fresh:
            entry = Decimal(str(self.chart.levels.get('ENTRY', 0)))
            if entry > 0 and self.last_price is not None:
                text += f' · цена от опорного входа: {(self.last_price / entry - 1) * 100:+.2f}% (не P&L)'
        self.strategy_status.setText(text)
        self.chart.setAccessibleName('График цены и уровней стратегии')
        self.chart.setAccessibleDescription(' · '.join(
            f'{key}: {self.display_price(value)}' for key, value in self.chart.levels.items()))

    def update_quote_age(self):
        self.refresh_trade_details()
        # Pull at UI cadence: no unbounded signal queue while an RPC/receipt blocks the executor.
        monitor = self.worker.execution_monitor
        if monitor is not None and self.mode.currentText() == self.worker.mode and self.selection_ready:
            snapshot = monitor.snapshot()
            current_block = getattr(self, 'market_block', None)
            if (snapshot is not None and snapshot.pool.lower() == self.pool_input.text().lower()
                    and (current_block is None or snapshot.block >= current_block)):
                identity = (id(monitor), snapshot.revision)
                if identity != getattr(self, '_monitor_revision', None):
                    self._monitor_revision = identity
                    self.on_event('price_context', {'source': 'BSC', 'quote': snapshot.quote,
                        'block': snapshot.block, 'block_timestamp': snapshot.block_timestamp})
                    self.on_event('price', str(snapshot.price))
                    self.last_quote_at = snapshot.received_at
        self.update_strategy_status()
        self.refresh_currency()
        if self.last_quote_at is None:
            self.quote_age.setText('Котировок ещё нет')
            set_tone(self.quote_age, '')
            return
        age = max(0, time.monotonic()-self.last_quote_at)
        source = {'DEMO':'локальная модель DEMO', 'REPLAY':'повтор записанных цен'}.get(self.price_source, 'BSC / RPC')
        state = ' · нет новых котировок > 0.55 с' if self.running and age > .55 else ''
        if self.chart.usd_rate is not None:
            conversion = f'USD ≈ · курс {self.display_unit}/USD получен {time.monotonic()-self.usd.received_at:.0f} с назад (DEX Screener)'
        elif self.price_source == 'BSC':
            conversion = f'{self.display_unit} · USD недоступен / курс загружается'
        else:
            conversion = self.display_unit
        if getattr(self, 'market_block', None) is not None:
            source += f' · блок {self.market_block}'
            if self.market_block_timestamp is not None:
                source += f' (возраст {max(0, time.time()-self.market_block_timestamp):.1f} с)'
        if getattr(self, 'market_rpc_source', 'BSC') != 'BSC':
            source += ' · резервный RPC'
        if getattr(self, 'same_block_cache', False):
            source += ' · тот же блок'
        set_tone(self.quote_age, 'warning' if age > .55 else '')
        self.quote_age.setToolTip(f'1 TARGET в {conversion} · {source}')
        self.quote_age.setText(f'1 TARGET в {conversion} · {source} · последняя котировка {age:.1f} с назад{state}')

    def refresh_timings(self):
        if not self.timing_report.isVisible():
            return
        rows = TIMINGS.snapshot()
        self.timing_report.setPlainText('\n'.join(
            f"{name}: {row['p50_ms']:.1f} / {row['p95_ms']:.1f} / {row['p99_ms']:.1f} мс"
            f" · {row['count']} вызовов · {row['errors']} ошибок"
            for name, row in sorted(rows.items())))

    def update_controls(self):
        idle = not self.busy and not self.running and not self.stop_pending
        position_open = self.display_position > 0
        for widget in self.editable + self.actions:
            widget.setEnabled(idle)
        if position_open:
            for widget in (self.mode, self.token, self.pool_input, self.router, self.quote, self.candidates):
                widget.setEnabled(False)
            blocked = {'AutoPair · найти пулы', 'CHECK POOL', 'Выбрать', 'ADD BASE',
                       'Подключить', 'VERIFY WALLET AND SAVE · Keychain',
                       'REMOVE выбранную пользовательскую базу'}
            for button in self.actions:
                if button.text() in blocked:
                    button.setEnabled(False)
        ready = self.mode.currentText() == "DEMO" or self.selection_ready
        live_locked = self.mode.currentText() == 'LIVE' and self.locked
        self.start.setEnabled(idle and ready and not live_locked)
        self.buy.setEnabled(idle and ready and not position_open and not live_locked)
        if self.searching and not self.running and not self.stop_pending and not position_open:
            self.token.setEnabled(True)
        self.sell.setEnabled(not self.busy and not self.stop_pending and position_open and not live_locked)
        self.stop.setEnabled(True)
        self.update_strategy_status()

    def on_event(self, name, payload):
        if name == "discovery_event":
            generation, event_name, value = payload
            if generation == self.auto_generation:
                if event_name == "error":
                    self.pool_label.setText("Ошибка RPC/проверки · повторите AutoPair или CHECK POOL")
                self.on_event(event_name, value)
            return
        if name == "busy":
            self.busy = payload
            if not payload:
                self.searching = False
        elif name == 'exit_retry':
            self.exit_retry = payload
        elif name == "error":
            self.ui_error = str(payload)
            self.journal_toggle.setChecked(True)
            self.footer.setText("ОШИБКА: " + payload)
            QMessageBox.warning(self, "Операция прервана", payload)
        elif name == 'price_context':
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
        elif name == "price":
            if self.mode.currentText() != 'DEMO' and not self.selection_ready:
                return
            self.last_price = Decimal(str(payload))
            self.chart.add(payload)
            self.metrics["price"].setText(self.display_price(payload))
            self.metrics['price'].setToolTip(str(payload))
            self.last_quote_at = time.monotonic()
            self.update_quote_age()
        elif name == 'trade_marker':
            if payload['mode'] == self.mode.currentText() and self.chart.values:
                self.chart.mark(payload['side'], payload['price'])
        elif name == "autopair":
            self.pool_label.setText({"PENDING": "PENDING · ожидается ликвидность; повторите AutoPair",
                                     "NOT_FOUND": "Пулы не найдены",
                                     "INVALID_CONTRACT": "По адресу нет контракта BSC",
                                     "CATALOG_TOKEN": "Введён адрес базового профиля; выберите PAIR вручную",
                                     "UNSUPPORTED_POOL": "Неподдерживаемый пул или базовая пара",
                                     "AMBIGUOUS": "Найдено несколько пар; выберите маршрут явно"}.get(payload, self.pool_label.text()))
        elif name == 'route_comparison_error':
            self.route_comparison.setText('Сравнение не выполнено: ' + str(payload))
        elif name == 'route_comparison':
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
        elif name == "pools":
            self.route_comparison.setText('Сравнение маршрутов ещё не выполнено')
            self.selection_ready = False
            self.pool_input.clear()
            self.candidates.clear()
            self.pool_label.setText("Выберите проверенный маршрут" if payload else "Пул не выбран")
            for pool in payload:
                self.candidates.addItem(pool.label, pool)
        elif name == "selected":
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
        elif name == "sweep_report":
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
        elif name == "wallet":
            self.wallet.setText(payload)
        elif name == "profiles":
            self.update_profiles(payload)
        elif name == "profile_removed":
            self.schedule_autopair()
        elif name == "balances":
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
        elif name == "position_comparison_error":
            self.position_comparison.setText("Сверка не выполнена: " + payload)
        elif name == "position_comparison":
            lines = [f"Снимок балансов: блок {payload['block']}. Локальные записи не изменены."]
            for row in payload['rows']:
                scale = Decimal(10)**row['decimals']
                lines.append(f"{row['owner']} · {row['token']} · пул {row['pool']}\n" +
                    f"Записано: {Decimal(row['saved_raw'])/scale}; в кошельке: {Decimal(row['actual_raw'])/scale} · " +
                    ('совпадает' if row['matches'] else 'РАСХОЖДЕНИЕ'))
            if not payload['rows']:
                lines.append('Сохранённых позиций нет.')
            self.position_comparison.setText('\n'.join(lines))
        elif name == 'accounting_report':
            self.accounting_text.setPlainText(payload)
        elif name == "receipt_review":
            self.receipt_result.setText(payload)
            self.refresh_recovery()
        elif name == "status":
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
