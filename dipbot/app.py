import argparse
from collections import deque
import sys
from datetime import datetime

from PySide6.QtCore import Qt, QLockFile, QTimer, QPointF
from PySide6.QtGui import QColor, QPainter, QPen, QPainterPath
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QLabel, QPushButton,
    QLineEdit, QComboBox, QDoubleSpinBox, QFormLayout, QVBoxLayout, QHBoxLayout,
    QGridLayout, QGroupBox, QPlainTextEdit, QTabWidget, QCheckBox, QMessageBox,
    QTableWidget, QTableWidgetItem, QHeaderView, QScrollArea)

from .chain import profiles
from .storage import Store, Vault, data_dir
from .worker import Worker
from . import preferences


STYLE = """
QWidget { background: #10161e; color: #e7eef7; font-size: 13px; }
QMainWindow { background: #10161e; }
QLabel#title { font-size: 30px; font-weight: 700; letter-spacing: 2px; }
QLabel#muted { color: #93a6bb; }
QLabel#metric { font-size: 23px; font-weight: 600; color: #60e1bb; }
QLabel#banner { background: #183448; color: #9eddfc; border-radius: 8px; padding: 12px; }
QGroupBox { border: 1px solid #293647; border-radius: 10px; margin-top: 15px; padding: 15px; }
QGroupBox::title { subcontrol-origin: margin; left: 15px; padding: 0 6px; color: #93a6bb; }
QLineEdit, QComboBox, QDoubleSpinBox { background: #182330; border: 1px solid #34465c;
    border-radius: 5px; padding: 7px; min-height: 20px; selection-background-color: #245b65; }
QPushButton { background: #243446; border: 1px solid #3b5067; border-radius: 6px;
    padding: 9px 14px; font-weight: 600; }
QPushButton:hover { background: #31495f; }
QPushButton:disabled { color: #607080; background: #19232d; border-color: #263342; }
QPushButton#primary { background: #5de0b8; border-color: #5de0b8; color: #0d2921; }
QPushButton#danger { background: #4a2633; color: #ffc2cf; border-color: #754052; }
QPlainTextEdit, QTableWidget { background: #0b1119; border: 1px solid #293647; border-radius: 6px; }
QHeaderView::section { background: #182330; color: #94a8bd; padding: 8px; border: 0; }
QTabWidget::pane { border: 0; }
QTabBar::tab { background: #182330; padding: 12px 24px; margin-right: 3px; }
QTabBar::tab:selected { color: #60e1bb; border-bottom: 2px solid #60e1bb; }
QScrollArea { border: 0; }
"""


class Chart(QWidget):
    def __init__(self):
        super().__init__()
        self.values = deque(maxlen=180)
        self.setMinimumHeight(140)

    def add(self, value):
        self.values.append(float(value))
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        painter.setPen(QPen(QColor("#263444"), 1))
        for y in range(25, h, 35):
            painter.drawLine(0, y, w, y)
        if len(self.values) < 2:
            painter.setPen(QColor("#93a6bb"))
            painter.drawText(self.rect(), Qt.AlignCenter, "График появится после START / выбора пула")
            return
        lo, hi = min(self.values), max(self.values)
        spread = hi - lo or max(abs(hi)*0.01, 0.000001)
        path = QPainterPath()
        for i, value in enumerate(self.values):
            point = QPointF(8 + (w-16)*i/(len(self.values)-1), h-15-(h-30)*(value-lo)/spread)
            path.moveTo(point) if i == 0 else path.lineTo(point)
        painter.setPen(QPen(QColor("#60e1bb"), 2))
        painter.drawPath(path)


class Window(QMainWindow):
    def __init__(self, store=None):
        super().__init__()
        self.store = store or Store()
        self.worker = Worker(self.store)
        self.worker.log.connect(self.log)
        self.worker.event.connect(self.on_event)
        self.busy = False
        self.running = False
        self.active_mode = "DEMO"
        self.locked = bool(self.store.data.get("operation"))
        self.editable = []
        self.actions = []
        self.setWindowTitle("DipBot Mac · BSC")
        self.resize(1190, 910)
        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(25, 20, 25, 18)
        title_row = QHBoxLayout()
        title = QLabel("DIP / BSC")
        title.setObjectName("title")
        title_row.addWidget(title)
        title_row.addStretch()
        self.mode = QComboBox()
        self.mode.addItems(["DEMO", "PAPER", "LIVE"])
        self.mode.currentTextChanged.connect(self.mode_changed)
        self.editable.append(self.mode)
        title_row.addWidget(QLabel("Режим"))
        title_row.addWidget(self.mode)
        layout.addLayout(title_row)
        subtitle = QLabel("macOS · PancakeSwap V2 / V3 · Независимая реализация 0.1")
        subtitle.setObjectName("muted")
        layout.addWidget(subtitle)
        self.banner = QLabel()
        self.banner.setObjectName("banner")
        self.banner.setWordWrap(True)
        layout.addWidget(self.banner)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)
        self.build_bot()
        self.build_settings()
        self.build_pairs()
        self.build_about()
        layout.addWidget(self.execution_bar)
        self.activity = QPlainTextEdit()
        self.activity.setReadOnly(True)
        self.activity.setMaximumBlockCount(600)
        self.activity.setMinimumHeight(120)
        self.activity.setMaximumHeight(155)
        layout.addWidget(QLabel("ACTIVITY LOG"))
        layout.addWidget(self.activity)
        self.footer = QLabel("Готов к DEMO. Реальные сделки доступны только в LIVE.")
        self.footer.setObjectName("muted")
        layout.addWidget(self.footer)
        saved_preferences = self.store.data.get("ui_preferences")
        if saved_preferences is not None:
            try:
                saved_preferences = preferences.normalize(saved_preferences)
                for key, value in saved_preferences["settings"].items():
                    self.params[key].setText(value)
                self.gas.setText(saved_preferences["gas"])
                self.interval.setValue(float(saved_preferences["interval"]))
            except ValueError:
                self.log("Сохранённые параметры некорректны: использованы значения по умолчанию")
        self.mode_changed()
        self.update_profiles()
        if self.locked:
            self.log("В журнале есть незавершённая LIVE-операция. Проведите сверку в настройках")
        self.worker.start()

    def button(self, text, callback, kind=None):
        button = QPushButton(text)
        button.clicked.connect(callback)
        if kind:
            button.setObjectName(kind)
        self.actions.append(button)
        return button

    def field(self, value="", placeholder=""):
        field = QLineEdit(value)
        field.setPlaceholderText(placeholder)
        self.editable.append(field)
        return field

    def tab(self, name):
        content = QWidget()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(content)
        self.tabs.addTab(scroll, name)
        return QVBoxLayout(content)

    def build_bot(self):
        layout = self.tab("Торговля")
        metrics = QHBoxLayout()
        self.metrics = {}
        for key, title in [("price", "ЦЕНА / БАЗОВЫЙ АКТИВ"), ("base", "БАЗА DIP"),
                           ("position", "ПОЗИЦИЯ / TARGET"), ("state", "СОСТОЯНИЕ")]:
            box = QGroupBox(title)
            metric = QLabel("—")
            metric.setObjectName("metric")
            QVBoxLayout(box).addWidget(metric)
            metrics.addWidget(box)
            self.metrics[key] = metric
        layout.addLayout(metrics)
        self.chart = Chart()
        layout.addWidget(self.chart)
        body = QHBoxLayout()
        pool_group = QGroupBox("РЫНОК / AUTOPAIR")
        form = QFormLayout(pool_group)
        saved_pool = self.store.data.get("last_pool", {})
        self.token = self.field(saved_pool.get("token", ""), "Адрес target-токена в BSC")
        form.addRow("TOKEN ADDRESS", self.token)
        selectors = QHBoxLayout()
        self.router = QComboBox()
        self.router.addItems(["AUTO", "V2", "V3"])
        self.quote = QComboBox()
        self.editable += [self.router, self.quote]
        selectors.addWidget(self.router)
        selectors.addWidget(self.quote, 1)
        form.addRow("ROUTER / PAIR", selectors)
        form.addRow(self.button("AutoPair · найти пулы", lambda: self.send("discover", token=self.token.text(),
                         quote=self.quote.currentText(), router=self.router.currentText())))
        self.pool_input = self.field(saved_pool.get("address", ""), "Адрес известного пула PancakeSwap")
        form.addRow("POOL ADDRESS", self.pool_input)
        form.addRow(self.button("CHECK POOL", lambda: self.send("verify", token=self.token.text(), pool=self.pool_input.text())))
        self.candidates = QComboBox()
        self.candidates.setMinimumContentsLength(20)
        self.candidates.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.editable.append(self.candidates)
        form.addRow("Маршрут", self.candidates)
        choose = QHBoxLayout()
        choose.addWidget(self.button("Выбрать", self.select_pool))
        choose.addWidget(self.button("ADD BASE", lambda: self.send("add_profile")))
        form.addRow(choose)
        self.pool_label = QLabel("DEMO использует локальную модель цены")
        self.pool_label.setWordWrap(True)
        self.pool_label.setObjectName("muted")
        form.addRow(self.pool_label)
        body.addWidget(pool_group, 3)
        strategy = QGroupBox("ПАРАМЕТРЫ СТРАТЕГИИ")
        grid = QFormLayout(strategy)
        self.params = {}
        for key, title, value in [("amount", "AMOUNT (базовый актив)", "0.02"),
                 ("dip", "DIP %", "3"), ("take_profit", "TP %", "2"),
                 ("stop_loss", "STOP LOSS %", "5"), ("slippage", "SLIPPAGE %", "2"),
                 ("dynamic", "DYNAMIC", "150")]:
            self.params[key] = self.field(value)
            grid.addRow(title, self.params[key])
        self.interval = QDoubleSpinBox()
        self.interval.setRange(0.1, 0.5)
        self.interval.setDecimals(3)
        self.interval.setValue(0.1)
        self.interval.setToolTip("V3: минимум 0.103 с. Разрыв наблюдений > 0.55 с сбрасывает базу DIP.")
        self.interval.setSuffix(" s")
        self.editable.append(self.interval)
        grid.addRow("Пауза между чтениями", self.interval)
        body.addWidget(strategy, 2)
        layout.addLayout(body)
        self.execution_bar = QWidget()
        controls = QHBoxLayout(self.execution_bar)
        controls.setContentsMargins(0, 0, 0, 0)
        self.start = self.button("START BOT", lambda: self.trade("start"), "primary")
        self.buy = self.button("BUY NOW", lambda: self.trade("buy"))
        self.sell = self.button("SELL POSITION", self.sell_position)
        self.stop = QPushButton("STOP · закрыть позицию")
        self.stop.setObjectName("danger")
        self.stop.clicked.connect(self.stop_bot)
        for button in [self.start, self.buy, self.sell, self.stop]:
            controls.addWidget(button)
        converter = QGroupBox("CONVERTER / WALLET SWEEP · LIVE")
        row = QHBoxLayout(converter)
        self.convert_amount = self.field("0.01")
        self.convert_amount.setMaximumWidth(120)
        row.addWidget(QLabel("BNB"))
        row.addWidget(self.convert_amount)
        row.addWidget(self.button("BUY BASE", lambda: self.trade("convert", buy=True, amount=self.convert_amount.text())))
        row.addWidget(self.button("SELL ALL BASE → BNB", lambda: self.trade("convert", buy=False, amount="0")))
        row.addWidget(self.button("SELL WALLET → BNB", lambda: self.trade("sweep"), "danger"))
        layout.addWidget(converter)
        layout.addStretch()

    def build_settings(self):
        layout = self.tab("RPC и кошелёк")
        group = QGroupBox("ПОДКЛЮЧЕНИЕ BSC")
        form = QFormLayout(group)
        self.rpc = self.field(placeholder="https://… — ваш BSC HTTP RPC")
        self.rpc.setEchoMode(QLineEdit.PasswordEchoOnEdit)
        form.addRow("HTTP RPC", self.rpc)
        self.save_rpc = QCheckBox("Сохранить RPC в macOS Keychain")
        self.editable.append(self.save_rpc)
        form.addRow(self.save_rpc)
        row = QHBoxLayout()
        row.addWidget(self.button("Подключить", lambda: self.send("connect", rpc=self.rpc.text().strip(), save=self.save_rpc.isChecked())))
        row.addWidget(self.button("Загрузить RPC из Keychain", self.load_rpc))
        form.addRow(row)
        self.gas = self.field("0.1")
        form.addRow("GAS GWEI", self.gas)
        form.addRow(QLabel("Лимит: 0.005 BNB газа на транзакцию. Комиссии approve и нескольких шагов суммируются."))
        layout.addWidget(group)
        wallet = QGroupBox("КОШЕЛЁК")
        form = QFormLayout(wallet)
        self.key = self.field(placeholder="Private key отдельного BSC-кошелька")
        self.key.setEchoMode(QLineEdit.Password)
        form.addRow("PRIVATE KEY", self.key)
        form.addRow(self.button("VERIFY WALLET AND SAVE · Keychain", self.save_wallet))
        self.wallet = self.field(self.store.data.get("wallet_address", ""), "Публичный адрес 0x… для просмотра балансов")
        form.addRow("WALLET ADDRESS", self.wallet)
        form.addRow(self.button("Обновить балансы", self.balances))
        layout.addWidget(wallet)
        recovery = QGroupBox("НЕЗАВЕРШЁННЫЕ ТРАНЗАКЦИИ")
        rec = QVBoxLayout(recovery)
        text = QLabel("После таймаута или аварийного закрытия LIVE блокируется. Сначала проверьте receipt и балансы. "
                      "Снятие блокировки сбрасывает кэш позиций; реальные остатки остаются в кошельке.")
        text.setWordWrap(True)
        rec.addWidget(text)
        rec.addWidget(self.button("Проверить receipts", lambda: self.send("reconcile", gas=self.gas.text())))
        rec.addWidget(self.button("Балансы сверены · снять блокировку", self.unlock, "danger"))
        layout.addWidget(recovery)
        layout.addStretch()

    def build_pairs(self):
        layout = self.tab("Активы и балансы")
        row = QHBoxLayout()
        row.addWidget(QLabel("42 адреса извлечены из EXE. Ликвидность и доступность проверяются через RPC."))
        row.addStretch()
        row.addWidget(self.button("Обновить балансы", self.balances))
        layout.addLayout(row)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Актив", "Контракт BSC", "Баланс"])
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.doubleClicked.connect(self.pair_clicked)
        layout.addWidget(self.table)
        layout.addWidget(QLabel("Двойной клик по активу выбирает PAIR для AutoPair. «?» означает ошибку чтения."))
        layout.addWidget(self.button("REMOVE выбранную пользовательскую базу", self.remove_profile))

    def build_about(self):
        layout = self.tab("О реализации")
        text = QLabel(
            "<h2>DipBot Mac 0.1 — экспериментальная сборка</h2>"
            "<p>Это новый открытый исходный код по результатам статического разбора NRNF v1.4.14. "
            "Лицензия и Windows EXE не используются.</p>"
            "<p><b>Стратегия:</b> DIP от максимальной наблюдаемой цены; BUY; TP или STOP LOSS от "
            "фактической цены входа. После SELL база сбрасывается. После STOP LOSS бот останавливается. "
            "Если котировки отсутствовали более 10 секунд, база входа сбрасывается.</p>"
            "<p><b>Dynamic:</b> допуск BUY = SLIPPAGE − DYNAMIC / 100. Этот допуск применяется "
            "к котировке router. Точная формула minOut и правило «2 down moves» оригинала не восстановлены.</p>"
            "<p><b>DEMO:</b> локальная синусоидальная цена. <b>PAPER:</b> реальные котировки, "
            "виртуальные сделки. Модель учитывает заданное проскальзывание, но не газ, token tax и влияние объёма.</p>"
            "<p><b>LIVE:</b> реальные BSC-транзакции, V2/V3, approve точной суммы, "
            "ручные BUY/SELL, Converter и Sweep зарегистрированных активов. "
            "AMOUNT задаётся в базовом ERC-20 активе; для WBNB сначала используйте Converter.</p>"
            "<p>Конвертация через промежуточные токены выполняется несколькими транзакциями. "
            "При сбое промежуточный актив остаётся в кошельке. V3 не поддерживает fee-on-transfer токены.</p>"
            "<p>STOP во время отправки ждёт receipt, затем закрывает позицию. При неизвестном статусе "
            "автоматическое закрытие невозможно; приложение сохраняет hash для сверки.</p>"
            "<p>Частота чтений зависит от RPC; эквивалентность оригиналу и задержка 100 мс не подтверждены. "
            "Реальные сделки разработчиком не выполнялись. Перед использованием средств нужна проверка "
            "на выделенном тестовом кошельке.</p>")
        text.setWordWrap(True)
        text.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(text)
        layout.addStretch()

    def log(self, message):
        self.activity.appendPlainText(datetime.now().strftime("%H:%M:%S") + "  " + message)

    def send(self, name, **data):
        if self.busy:
            return
        self.busy = True
        self.update_controls()
        self.worker.submit(name, **data)

    def trade(self, command, **extra):
        mode = self.mode.currentText()
        if command in ("convert", "sweep") and mode != "LIVE":
            QMessageBox.information(self, "LIVE", "Converter и Sweep доступны только в LIVE")
            return
        if mode == "LIVE":
            description = ("Будут проданы все зарегистрированные target и базовые активы кошелька." if command == "sweep"
                           else f"Действие: {command.upper()}\nAMOUNT: {self.params['amount'].text()} базового актива."
                           if command != "convert" else f"Converter: {'BUY за '+extra['amount']+' BNB' if extra['buy'] else 'SELL всего баланса базы → BNB'}")
            if QMessageBox.question(self, "Реальная торговля BSC", description +
                   f"\nTARGET: {self.token.text()}\nPOOL: {self.pool_input.text()}\nWALLET: {self.store.data.get('wallet_address', 'Keychain')}"
                   "\n\nБудут подписаны и отправлены реальные транзакции, включая необходимые approve. Продолжить?",
                   QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
                return
        self.send(command, mode=mode, settings={k: v.text().strip() for k, v in self.params.items()},
                  interval=self.interval.value(), gas=self.gas.text(), token=self.token.text(),
                  pool=self.pool_input.text(), **extra)

    def sell_position(self):
        if self.active_mode == "LIVE" and QMessageBox.question(self, "SELL POSITION", "Продать отслеживаемую позицию реальной транзакцией?",
                     QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        self.send("sell")

    def stop_bot(self):
        self.worker.stop_event.set()
        self.log("STOP запрошен. Если сделка отправлена — ожидается receipt; затем закрытие позиции")

    def select_pool(self):
        pool = self.candidates.currentData()
        if pool:
            self.send("select", pool=pool)

    def save_wallet(self):
        key = self.key.text().strip()
        self.key.clear()
        self.send("wallet", key=key)

    def load_rpc(self):
        try:
            self.rpc.setText(Vault().get("rpc") or "")
        except Exception:
            QMessageBox.warning(self, "Keychain", "Не удалось прочитать RPC из Keychain")

    def balances(self):
        self.send("balance", wallet=self.wallet.text().strip())

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
        self.quote.clear()
        self.quote.addItems(["ALL"] + sorted(pairs, key=str.casefold))
        self.quote.setCurrentText(selected if selected in pairs else "WBNB")
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

    def update_controls(self):
        for widget in self.editable + self.actions:
            widget.setEnabled(not self.busy and not self.running)
        self.sell.setEnabled(not self.busy)
        self.stop.setEnabled(True)

    def on_event(self, name, payload):
        if name == "busy":
            self.busy = payload
        elif name == "error":
            self.footer.setText("ОШИБКА: " + payload)
            QMessageBox.warning(self, "Операция прервана", payload)
        elif name == "price":
            self.chart.add(payload)
            self.metrics["price"].setText(f"{float(payload):.8g}")
        elif name == "pools":
            self.candidates.clear()
            self.pool_label.setText("Выберите проверенный маршрут" if payload else "Пул не выбран")
            for pool in payload:
                self.candidates.addItem(pool.label, pool)
        elif name == "selected":
            self.pool_input.setText(payload.address)
            self.token.setText(payload.token)
            self.pool_label.setText(payload.label + "\nAMOUNT в активе " + payload.quote)
        elif name == "wallet":
            self.wallet.setText(payload)
        elif name == "profiles":
            self.update_profiles(payload)
        elif name == "balances":
            self.log("Балансы: " + "; ".join(f"{k}={v}" for k, v in payload.items() if v not in ("0", "0.0")))
            for row in range(self.table.rowCount()):
                symbol = self.table.item(row, 0).text()
                value = payload.get(symbol, "—")
                item = QTableWidgetItem(value)
                try:
                    if float(value) > 0:
                        item.setForeground(QColor("#60e1bb"))
                except ValueError:
                    pass
                self.table.setItem(row, 2, item)
        elif name == "status":
            self.running, self.active_mode, self.locked = payload["running"], payload["mode"], payload["locked"]
            self.metrics["state"].setText("LOCKED" if self.locked and self.active_mode == "LIVE" else "RUNNING" if self.running else "IDLE")
            self.metrics["base"].setText(f"{float(payload['base']):.8g}")
            self.metrics["position"].setText(f"{float(payload['position']):.8g}")
            self.footer.setText(f"{self.active_mode} · " + ("BOT работает" if self.running else "BOT остановлен") +
                                f" · PAPER realized P&L: {payload['realized']}" + (" · LIVE LOCKED" if self.locked else ""))
        self.update_controls()

    def closeEvent(self, event):
        if self.busy or self.running:
            QMessageBox.information(self, "Сначала STOP", "Остановите BOT и дождитесь завершения текущей операции перед закрытием")
            event.ignore()
            return
        self.worker.quit_event.set()
        if not self.worker.wait(1500):
            event.ignore()
            return
        # The worker has exited: saving cannot race its transaction journal.
        try:
            preferences.save(self.store, {"version": 1,
                "settings": {key: field.text().strip() for key, field in self.params.items()},
                "gas": self.gas.text().strip(), "interval": str(self.interval.value())})
        except (ValueError, OSError):
            QMessageBox.warning(self, "Настройки не сохранены",
                                "Не удалось сохранить параметры. Предыдущие настройки сохранены, если запись не была заменена.")
        event.accept()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--smoke-test", action="store_true", help="Offline GUI startup test, temporary state")
    args = parser.parse_args()
    app = QApplication(sys.argv[:1])
    app.setApplicationName("DipBot Mac")
    app.setStyleSheet(STYLE)
    if args.smoke_test:
        import tempfile
        from pathlib import Path
        from eth_account import Account
        # Exercise packaged crypto using a temporary, unfunded key. No provider or broadcast.
        account = Account.create()
        signed = account.sign_transaction({"to": account.address, "value": 0, "chainId": 56,
                                            "nonce": 0, "gas": 21000, "gasPrice": 1})
        assert Account.recover_transaction(signed.raw_transaction) == account.address
        assert len(profiles()) == 42
        Vault.backend()  # Instantiate only; do not read/write the real Keychain.
        temp = tempfile.TemporaryDirectory()
        store = Store(Path(temp.name) / "state.json")
        window = Window(store)
        window.show()
        QTimer.singleShot(600, window.close)
        QTimer.singleShot(1500, app.quit)
    else:
        lock = QLockFile(str(data_dir() / "app.lock"))
        if not lock.tryLock(0):
            QMessageBox.warning(None, "DipBot Mac", "Другой экземпляр приложения уже запущен")
            return 1
        try:
            window = Window()
        except Exception:
            QMessageBox.critical(None, "Данные приложения", "Не удалось прочитать state.json. Сохраните его копию для сверки; торговля не запущена")
            return 1
        window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
