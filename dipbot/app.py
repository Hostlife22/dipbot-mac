import argparse
from collections import deque
import sys
import time
from datetime import datetime
from decimal import Decimal

from PySide6.QtCore import Qt, QLockFile, QTimer, QPointF
from PySide6.QtGui import QColor, QPainter, QPen, QPainterPath
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QLabel, QPushButton,
    QLineEdit, QComboBox, QDoubleSpinBox, QFormLayout, QVBoxLayout, QHBoxLayout,
    QGridLayout, QGroupBox, QPlainTextEdit, QTabWidget, QCheckBox, QMessageBox,
    QTableWidget, QTableWidgetItem, QHeaderView, QScrollArea, QSizePolicy)

from .chain import profiles
from .dynamic import catalog
from .storage import Store, Vault, data_dir
from .worker import Worker
from . import preferences
from .usd import UsdRate, price_text


from .theme import STYLE


class Chart(QWidget):
    def __init__(self):
        super().__init__()
        self.values = deque(maxlen=180)
        self.times = deque(maxlen=180)
        self.levels = {}
        self.usd_rate = None
        self.markers = deque(maxlen=180)
        self.hover = None
        self.setToolTip("BUY/SELL отмечают завершённые операции по цене сигнала/наблюдения, не цене исполнения LIVE. Наведите курсор для просмотра цены и относительного времени.")
        self.setMouseTracking(True)
        self.setMinimumHeight(240)

    def mark(self, label, price):
        if self.times:
            self.markers.append((self.times[-1], label, float(price)))
            self.update()

    def mouseMoveEvent(self, event):
        self.hover = event.position()
        self.update()

    def leaveEvent(self, event):
        self.hover = None
        self.update()

    def add(self, value):
        self.values.append(float(value))
        self.times.append(time.monotonic())
        self.update()

    def clear(self):
        self.values.clear()
        self.times.clear()
        self.levels.clear()
        self.markers.clear()
        self.hover = None
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        painter.setPen(QPen(QColor('#2a3546'), 1))
        painter.setBrush(QColor('#111b29'))
        painter.drawRoundedRect(self.rect().adjusted(0, 0, -1, -1), 9, 9)
        painter.setBrush(Qt.NoBrush)
        if len(self.values) < 2:
            painter.setPen(QColor("#93a6bb"))
            painter.drawText(self.rect(), Qt.AlignCenter, "График появится после START / выбора пула")
            return
        bounds = list(self.values) + [float(v) for v in self.levels.values() if float(v) > 0]
        bounds += [v for stamp, _, v in self.markers if stamp >= self.times[0]]
        lo, hi = min(bounds), max(bounds)
        padding = (hi-lo)*.1 if hi != lo else max(abs(hi)*.01, 1e-30)
        lo, hi = max(0, lo-padding), hi+padding
        spread = hi-lo
        left, right, top, bottom = 120, max(130, w-185), 25, h-30
        def y(value):
            return bottom-(bottom-top)*(value-lo)/spread
        elapsed = max(self.times[-1]-self.times[0], .001)
        painter.setPen(QColor('#93a6bb'))
        for index in range(4):
            value = lo + spread*index/3
            py = y(value)
            painter.setPen(QPen(QColor('#263449'), 1))
            painter.drawLine(QPointF(left, py), QPointF(right, py))
            painter.setPen(QColor('#a5b3c5'))
            painter.drawText(10, int(py)+4, price_text(value, self.usd_rate, 6))
        painter.drawText(left, h-5, f'−{elapsed:.1f} с')
        painter.drawText(int(right)-110, h-5, 'последняя цена')
        colors = {'DIP':'#f4c76b', 'ENTRY':'#a7a4ef', 'TP':'#60e1bb', 'SL':'#f38e9e'}
        label_y = top-18
        for label, value in sorted(self.levels.items(), key=lambda item: -float(item[1])):
            if float(value) <= 0:
                continue
            painter.setPen(QPen(QColor(colors.get(label, '#a7a4ef')), 1, Qt.DashLine))
            py = y(float(value))
            painter.drawLine(QPointF(left, py), QPointF(right, py))
            label_y = max(py, label_y+18)
            painter.drawText(int(right)+8, int(label_y)+4, f'{label} {price_text(value, self.usd_rate, 6)}')
        path = QPainterPath()
        for i, value in enumerate(self.values):
            point = QPointF(left+(right-left)*(self.times[i]-self.times[0])/elapsed, y(value))
            if i == 0 or self.times[i]-self.times[i-1] > .55:
                path.moveTo(point)
            else:
                path.lineTo(point)
        painter.setPen(QPen(QColor("#60e1bb"), 2))
        painter.drawPath(path)
        for stamp, label, value in self.markers:
            if stamp < self.times[0]:
                continue
            px = left+(right-left)*(stamp-self.times[0])/elapsed
            py = y(value)
            painter.setPen(QColor('#60e1bb' if label == 'BUY' else '#f4c76b'))
            painter.drawEllipse(QPointF(px, py), 4, 4)
            painter.drawText(int(min(px+6, right-55)), int(max(top+12, py-8)), label)
        if self.hover is not None and left <= self.hover.x() <= right and top <= self.hover.y() <= bottom:
            stamp = self.times[0]+elapsed*(self.hover.x()-left)/(right-left)
            index = min(range(len(self.times)), key=lambda i: abs(self.times[i]-stamp))
            px = left+(right-left)*(self.times[index]-self.times[0])/elapsed
            painter.setPen(QPen(QColor('#a5b3c5'), 1, Qt.DotLine))
            painter.drawLine(QPointF(px, top), QPointF(px, bottom))
            painter.drawEllipse(QPointF(px, y(self.values[index])), 4, 4)
            painter.setPen(QColor('#e7eef7'))
            painter.drawText(left, 17, f'{price_text(self.values[index], self.usd_rate)} · {self.times[index]-self.times[-1]:.1f} с от последней котировки')


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
        self.resize(1180, 850)
        self.setMinimumSize(920, 680)
        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(20, 16, 20, 12)
        layout.setSpacing(10)
        title_row = QHBoxLayout()
        title = QLabel("DIP / BSC")
        title.setObjectName("title")
        title_row.addWidget(title)
        title_row.addStretch()
        self.mode = QComboBox()
        self.mode.addItems(["DEMO", "PAPER", "LIVE"])
        self.mode.setMinimumWidth(130)
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
        self.build_bot()
        self.build_settings()
        self.build_pairs()
        self.build_about()
        layout.addWidget(self.execution_bar)
        self.activity = QPlainTextEdit()
        self.activity.setReadOnly(True)
        self.activity.setMaximumBlockCount(600)
        self.activity.setFixedHeight(120)
        self.activity.hide()
        self.journal_toggle = QPushButton("▸  ACTIVITY LOG · журнал событий")
        self.journal_toggle.setObjectName('journal')
        self.journal_toggle.setCheckable(True)
        self.journal_toggle.toggled.connect(self.toggle_journal)
        layout.addWidget(self.journal_toggle)
        layout.addWidget(self.activity)
        self.footer = QLabel("Готов к DEMO. Реальные сделки доступны только в LIVE.")
        self.footer.setObjectName("muted")
        self.footer.setWordWrap(True)
        layout.addWidget(self.footer)
        saved_preferences = self.store.data.get("ui_preferences")
        if saved_preferences is not None:
            try:
                saved_preferences = preferences.normalize(saved_preferences)
                for key, value in saved_preferences["settings"].items():
                    self.params[key].setText(value)
                policy = saved_preferences.get('signal_policy', {})
                self.signal_mode.setCurrentIndex(self.signal_mode.findData(policy.get('mode', 'legacy')))
                self.signal_window.setValue(float(policy.get('window_seconds', 60)))
                self.signal_rebound.setValue(float(policy.get('rebound_pct', 0)))
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
        selection = saved_preferences.get("selection", {})
        self.router.setCurrentText(selection.get("router", "AUTO"))
        if self.quote.findText(selection.get("pair", "WBNB")) >= 0:
            self.quote.setCurrentText(selection.get("pair", "WBNB"))
        self.amount_key = preferences.pair_key(self.router.currentText(), self.quote.currentText())
        if self.amount_key in self.pair_amounts:
            self.params["amount"].setText(self.pair_amounts[self.amount_key])
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
        if self.locked:
            self.log("В журнале есть незавершённая LIVE-операция. Проведите сверку в настройках")
        self.update_controls()
        self.worker.start()

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
        layout.setContentsMargins(0, 12, 4, 8)
        layout.setSpacing(12)
        return layout

    def form(self, parent):
        layout = QFormLayout(parent)
        layout.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        layout.setHorizontalSpacing(14)
        layout.setVerticalSpacing(10)
        layout.setLabelAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        return layout

    def disclosure(self, layout, title, content, expanded=False):
        toggle = QPushButton()
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

    def build_bot(self):
        layout = self.tab("Торговля")
        metrics = QHBoxLayout()
        self.metrics = {}
        self.metric_captions = {}
        for key, title in [("price", "ЦЕНА / БАЗОВЫЙ АКТИВ"), ("base", "БАЗА DIP"),
                           ("position", "КОЛИЧЕСТВО TARGET"), ("state", "СОСТОЯНИЕ")]:
            box = QWidget()
            box.setObjectName('metricCard')
            box.setMinimumHeight(64)
            card = QVBoxLayout(box)
            card.setContentsMargins(14, 10, 14, 10)
            card.setSpacing(5)
            caption = QLabel(title)
            self.metric_captions[key] = caption
            caption.setObjectName('metricCaption')
            card.addWidget(caption)
            metric = QLabel("—")
            metric.setObjectName("metric")
            metric.setTextInteractionFlags(Qt.TextSelectableByMouse)
            if key == 'price':
                metric.setProperty('tone', 'positive')
            card.addWidget(metric)
            metrics.addWidget(box, 1)
            self.metrics[key] = metric
        layout.addLayout(metrics)
        self.strategy_status = QLabel('Готов к запуску')
        self.strategy_status.setObjectName('banner')
        self.strategy_status.setWordWrap(True)
        self.strategy_status.setToolTip('Расстояния рассчитаны от последней цены до уровней сигнала, без учёта расходов.')
        self.display_position = Decimal(0)
        self.last_price = None
        layout.addWidget(self.strategy_status)
        self.chart = Chart()
        self.chart.setToolTip(self.chart.toolTip() + " USD — ориентировочный пересчёт всех точек по последнему полученному курсу, не исторический валютный график.")
        layout.addWidget(self.chart)
        self.levels_label = QLabel('Вход DIP: — · ENTRY: — · TP: — · SL: —')
        self.levels_label.setWordWrap(True)
        self.levels_label.setToolTip('ENTRY и TP/SL основаны на цене сигнала. TP не означает прибыль после расходов.')
        self.levels_label.hide()  # Levels are labelled directly on the chart.
        self.quote_age = QLabel('Котировок ещё нет')
        self.quote_age.setObjectName('muted')
        self.quote_age.setWordWrap(True)
        layout.addWidget(self.quote_age)
        self.base_price = None
        self.usd = UsdRate(self)
        self.usd.changed.connect(self.refresh_currency)
        self.last_quote_at = None
        self.display_unit = 'условных единиц (DEMO)'
        self.price_source = 'DEMO'
        self.age_timer = QTimer(self)
        self.age_timer.timeout.connect(self.update_quote_age)
        self.age_timer.start(250)
        compact = QGroupBox('ОСНОВНЫЕ ПАРАМЕТРЫ')
        compact_grid = QGridLayout(compact)
        self.params = {}
        for column, (key, title, value) in enumerate([
                ('amount', 'AMOUNT (базовый актив)', '0.02'), ('dip', 'DIP %', '3'),
                ('take_profit', 'TP %', '2'), ('stop_loss', 'STOP LOSS %', '2')]):
            field = self.field(value)
            field.setAlignment(Qt.AlignRight)
            self.params[key] = field
            compact_grid.addWidget(QLabel(title), 0, column)
            compact_grid.addWidget(field, 1, column)
        layout.insertWidget(0, compact)
        self.market_summary = QLabel('Рынок: DEMO · локальная модель')
        self.market_summary.setWordWrap(True)
        layout.addWidget(self.market_summary)
        body_widget = QWidget()
        body = QHBoxLayout(body_widget)
        body.setContentsMargins(0, 0, 0, 0)
        pool_group = QGroupBox("РЫНОК / AUTOPAIR")
        form = self.form(pool_group)
        saved_pool = self.store.data.get("last_pool", {})
        self.token = self.field(saved_pool.get("token", ""), "Адрес target-токена в BSC")
        form.addRow(QLabel("TOKEN ADDRESS"))
        form.addRow(self.token)
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
        form.addRow(QLabel("POOL ADDRESS"))
        form.addRow(self.pool_input)
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
        grid = self.form(strategy)
        for key, title, value in [("slippage", "SLIPPAGE %", "3"),
                 ("dynamic", "DYNAMIC", "150"),
                 ("max_roundtrip_loss", "Макс. потери BUY→SELL %", "3")]:
            self.params[key] = self.field(value)
            self.params[key].setAlignment(Qt.AlignRight)
            grid.addRow(title, self.params[key])
        self.params['max_roundtrip_loss'].setToolTip(
            'Проверка котировок входа и обратного выхода на одном блоке до покупки. '
            'Включает комиссии пула и влияние суммы. Не учитывает газ, token tax и изменение пула после BUY; '
            'не гарантирует возможность будущей продажи. Применяется в PAPER и LIVE.')
        self.signal_mode = QComboBox()
        self.signal_mode.setMinimumContentsLength(12)
        self.signal_mode.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.signal_mode.addItem('Совместимость · два снижения', 'legacy')
        self.signal_mode.addItem('DIP от максимума за окно', 'window')
        self.signal_window = QDoubleSpinBox()
        self.signal_window.setRange(1, 300)
        self.signal_window.setValue(60)
        self.signal_window.setSuffix(' s')
        self.signal_rebound = QDoubleSpinBox()
        self.signal_rebound.setRange(0, 20)
        self.signal_rebound.setDecimals(2)
        self.signal_rebound.setSuffix(' %')
        self.signal_mode.setToolTip('Оконный режим экспериментальный: максимум наблюдавшихся цен за окно, '
            'затем DIP и необязательный отскок. Параметры не оптимизированы по доходности.')
        grid.addRow('Расчёт DIP', self.signal_mode)
        grid.addRow('Окно максимума', self.signal_window)
        grid.addRow('Подтверждение отскока', self.signal_rebound)
        self.editable += [self.signal_mode, self.signal_window, self.signal_rebound]
        self.interval = QDoubleSpinBox()
        self.interval.setRange(0.1, 0.5)
        self.interval.setDecimals(3)
        self.interval.setValue(0.1)
        self.interval.setToolTip("V3: минимум 0.103 с. Разрыв наблюдений > 0.55 с сбрасывает базу DIP.")
        self.interval.setSuffix(" s")
        self.editable.append(self.interval)
        grid.addRow("Интервал опроса", self.interval)
        body.addWidget(strategy, 2, Qt.AlignTop)
        self.market_toggle = self.disclosure(layout, "Рынок / AutoPair и дополнительные параметры", body_widget)
        layout.removeWidget(self.market_summary)
        layout.removeWidget(self.market_toggle)
        market_row = QHBoxLayout()
        market_row.addWidget(self.market_summary, 1)
        market_row.addWidget(self.market_toggle)
        layout.insertLayout(0, market_row)
        self.market_toggle.toggled.connect(lambda opened: QTimer.singleShot(
            0, lambda: self.tabs.widget(0).ensureWidgetVisible(body_widget)) if opened else None)
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
        row.addWidget(QLabel("BNB"), 0)
        row.addWidget(self.convert_amount)
        row.addWidget(self.button("BUY BASE", lambda: self.trade("convert", buy=True, amount=self.convert_amount.text())))
        row.addWidget(self.button("SELL ALL BASE → BNB", lambda: self.trade("convert", buy=False, amount="0")))
        row.addWidget(self.button("SELL WALLET → BNB", lambda: self.trade("sweep"), "danger"))
        self.disclosure(layout, "CONVERTER / WALLET SWEEP · LIVE", converter)
        layout.addStretch()

    def build_settings(self):
        from .rpc_presets import MAIN, BACKUP
        layout = self.tab("RPC и кошелёк")
        group = QGroupBox("ПОДКЛЮЧЕНИЕ BSC")
        form = self.form(group)
        self.rpc = self.field(placeholder="https://… — ваш BSC HTTP RPC")
        self.rpc.setEchoMode(QLineEdit.PasswordEchoOnEdit)
        self.rpc_preset = self.add_rpc_presets(form, 'Источник RPC', self.rpc, MAIN)
        form.addRow("HTTP RPC", self.rpc)
        self.backup_rpc = self.field(placeholder='Резервный HTTPS RPC · только чтение котировок')
        self.backup_rpc.setEchoMode(QLineEdit.PasswordEchoOnEdit)
        self.backup_rpc_preset = self.add_rpc_presets(form, 'Источник резерва', self.backup_rpc, BACKUP)
        form.addRow('Резервный RPC', self.backup_rpc)
        self.save_rpc = QCheckBox("Сохранить RPC в macOS Keychain")
        self.editable.append(self.save_rpc)
        form.addRow(self.save_rpc)
        row = QHBoxLayout()
        row.addWidget(self.button("Подключить", lambda: self.send("connect", rpc=self.rpc.text().strip(),
            backup_rpc=self.backup_rpc.text().strip(), save=self.save_rpc.isChecked())))
        row.addWidget(self.button("Загрузить RPC из Keychain", self.load_rpc))
        form.addRow(row)
        self.gas = self.field("0.1")
        self.gas.setMaximumWidth(180)
        self.gas.setAlignment(Qt.AlignRight)
        form.addRow("GAS GWEI", self.gas)
        hint = QLabel("Лимит: 0.005 BNB газа на транзакцию. Накопительного бюджета оборота нет; размер покупки задаётся AMOUNT.")
        hint.setWordWrap(True)
        hint.setObjectName('muted')
        form.addRow(hint)
        layout.addWidget(group)
        wallet = QGroupBox("КОШЕЛЁК")
        form = self.form(wallet)
        self.key = self.field(placeholder="Private key отдельного BSC-кошелька")
        self.key.setEchoMode(QLineEdit.Password)
        form.addRow("PRIVATE KEY", self.key)
        form.addRow(self.button("VERIFY WALLET AND SAVE · Keychain", self.save_wallet))
        self.wallet = self.field(self.store.data.get("wallet_address", ""), "Публичный адрес 0x… для просмотра балансов")
        form.addRow("WALLET ADDRESS", self.wallet)
        form.addRow(self.button("Обновить балансы", self.balances))
        layout.addWidget(wallet)
        recovery = self.recovery_group = QGroupBox("ВОССТАНОВЛЕНИЕ LIVE")
        rec = QVBoxLayout(recovery)
        self.recovery_details = QLabel()
        self.recovery_details.setTextFormat(Qt.PlainText)
        self.recovery_details.setWordWrap(True)
        self.recovery_details.setTextInteractionFlags(Qt.TextSelectableByMouse)
        rec.addWidget(self.recovery_details)
        self.saved_positions = QComboBox()
        rec.addWidget(self.saved_positions)
        rec.addWidget(self.button("Подготовить сохранённый пул", self.prepare_saved_position))
        self.receipt_result = QLabel('Подключите RPC, затем проверьте receipts. Для чтения ключ не требуется.')
        self.receipt_result.setWordWrap(True)
        rec.addWidget(self.receipt_result)
        self.position_comparison = QLabel('Балансы сохранённых позиций ещё не сверены с сетью.')
        self.position_comparison.setTextFormat(Qt.PlainText)
        self.position_comparison.setWordWrap(True)
        rec.addWidget(self.position_comparison)
        rec.addWidget(self.button("Сверить сохранённые позиции с сетью", self.compare_saved_positions))
        text = QLabel("После таймаута или аварийного закрытия LIVE блокируется. Сначала проверьте receipt и балансы. "
                      "Снятие блокировки сбрасывает кэш позиций; реальные остатки остаются в кошельке.")
        text.setWordWrap(True)
        rec.addWidget(text)
        rec.addWidget(self.button("Проверить receipts", self.check_receipts))
        rec.addWidget(self.button("Балансы сверены · снять блокировку", self.unlock, "danger"))
        layout.addWidget(recovery)
        layout.addStretch()

    def build_pairs(self):
        layout = self.tab("Активы и балансы")
        row = QHBoxLayout()
        hint = QLabel("Базовые активы для AutoPair · балансы и ликвидность проверяются через RPC")
        hint.setWordWrap(True)
        hint.setObjectName('muted')
        row.addWidget(hint)
        row.addStretch()
        row.addWidget(self.button("Обновить балансы", self.balances))
        layout.addLayout(row)
        self.table = QTableWidget(0, 3)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(38)
        self.table.setMinimumHeight(330)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        self.table.setHorizontalHeaderLabels(["Актив", "Контракт BSC", "Баланс"])
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.setColumnWidth(0, 160)
        self.table.setColumnWidth(2, 180)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.doubleClicked.connect(self.pair_clicked)
        layout.addWidget(self.table)
        layout.addWidget(QLabel("Двойной клик по активу выбирает PAIR для AutoPair. «?» означает ошибку чтения."))
        layout.addWidget(self.button("REMOVE выбранную пользовательскую базу", self.remove_profile))

    def build_about(self):
        layout = self.tab("О реализации")
        intro = QLabel("DipBot Mac · Руководство")
        intro.setObjectName('title')
        layout.addWidget(intro)
        sections = [
            ("РЕЖИМЫ РАБОТЫ", "<b>DEMO</b> — заданный локальный цикл цен, без RPC и кошелька.<br><br>"
             "<b>PAPER</b> — реальные цены и router quotes на размер виртуальной сделки. "
             "Комиссия пула и price impact входят в котировку; газ, token tax и задержка включения не моделируются. "
             "Slippage ограничивает исполнение BUY, а не списывается как комиссия. DEMO/REPLAY сохраняют стресс-модель.<br><br>"
             "<b>LIVE</b> — реальные транзакции. Нужны RPC, кошелёк и проверенный пул."),
            ("ЦЕНА И СТРАТЕГИЯ", "Стратегия использует стоимость <b>1 TARGET в базовом активе</b>; USD в UI — справочный пересчёт. "
             "AMOUNT задаётся в базовом активе, количество TARGET — число токенов позиции.<br><br>"
             "Вход DIP рассчитывается от текущей базы. База обновляется при росте или двух снижениях; "
             "проверка DIP выполняется первой. Разрыв наблюдений больше <b>0.55 с</b> сбрасывает базу входа.<br><br>"
             "В LIVE база TP/SL — цена пула после receipt BUY; в PAPER — цена сигнала. Это не средняя цена исполнения. TAKE PROFIT не гарантирует прибыль после расходов. "
             "После STOP LOSS бот останавливается; скачок цены может превысить заданный порог."),
            ("УПРАВЛЕНИЕ И ВОССТАНОВЛЕНИЕ", "<b>STOP</b> останавливает стратегию и закрывает позицию. "
             "Если транзакция уже отправлена, бот ждёт receipt. При неизвестном результате LIVE блокируется: "
             "проверяйте receipts и балансы во вкладке «RPC и кошелёк».<br><br>"
             "Converter может выполнять несколько транзакций. При частичном сбое промежуточный актив "
             "остаётся в кошельке. V3 не поддерживает fee-on-transfer токены."),
            ("О ПРИЛОЖЕНИИ", "Независимая экспериментальная реализация для macOS, PancakeSwap V2/V3, BSC. "
             "Полная эквивалентность Windows-оригиналу и прибыльность стратегии не подтверждены.")]
        for title, description in sections:
            group = QGroupBox(title)
            box = QVBoxLayout(group)
            text = QLabel(description)
            text.setWordWrap(True)
            text.setTextInteractionFlags(Qt.TextSelectableByMouse)
            box.addWidget(text)
            layout.addWidget(group)
        layout.addStretch()

    def toggle_journal(self, expanded):
        self.activity.setVisible(expanded)
        self.journal_toggle.setText(("▾" if expanded else "▸") + "  ACTIVITY LOG · журнал событий")

    def log(self, message):
        self.activity.appendPlainText(datetime.now().strftime("%H:%M:%S") + "  " + message)

    def remember_amount(self):
        try:
            self.pair_amounts[self.amount_key] = preferences.positive_amount(self.params["amount"].text())
        except ValueError:
            pass  # Invalid edits never replace a previously valid per-pair amount.

    def market_changed(self, *_):
        if not self.quote.currentText():
            return
        self.remember_amount()
        self.amount_key = preferences.pair_key(self.router.currentText(), self.quote.currentText())
        self.params["amount"].setText(self.pair_amounts.get(self.amount_key, "0.02"))
        self.invalidate_discovery()

    def invalidate_discovery(self, *_, clear_pool=True):
        self.auto_generation += 1
        self.worker.discovery_generation = self.auto_generation
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
        if name in ("discover", "verify", "select", "add_profile"):
            data["generation"] = self.auto_generation
        if name in ("discover", "verify", "select"):
            self.pool_label.setText("Поиск и проверка маршрута…")
        self.searching = name in ("discover", "verify", "select")
        self.busy = True
        self.update_controls()
        self.worker.submit(name, **data)

    def signal_policy(self):
        return {'mode': self.signal_mode.currentData(), 'window_seconds': self.signal_window.value(),
                'rebound_pct': str(self.signal_rebound.value())}

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
        self.send(command, mode=mode, generation=self.auto_generation, settings={k: v.text().strip() for k, v in self.params.items()},
                  signal_policy=self.signal_policy(), interval=self.interval.value(), gas=self.gas.text(), token=self.token.text(), router=self.router.currentText(),
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
                details.append(str(tx.get('hash', 'hash не записан')) + ' · ' + str(tx.get('status', 'неизвестно')))
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
        self.banner.setProperty('mode', mode)
        self.banner.style().unpolish(self.banner)
        self.banner.style().polish(self.banner)
        if mode == 'DEMO':
            self.display_unit = 'условных единиц (DEMO)'
        if hasattr(self, 'chart'):
            self.reset_price_display()
        if hasattr(self, "selection_ready"):
            self.update_controls()
        if mode == "DEMO":
            self.market_summary.setText("Рынок: DEMO · локальная модель")
        elif hasattr(self, "pool_label"):
            self.market_summary.setText(self.pool_label.text())

    def reset_price_display(self):
        self.chart.clear()
        self.usd.set_token('')
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

    def refresh_currency(self):
        if not hasattr(self, 'usd') or not hasattr(self, 'price_source'):
            return
        rate = self.usd.current() if self.price_source not in ('DEMO', 'REPLAY') else None
        self.chart.usd_rate = rate
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

    def refresh_pnl(self):
        payload = getattr(self, 'pnl_status', None)
        if payload is None:
            return
        mode = self.mode.currentText()
        text = f"{mode} · " + ('BOT работает' if self.running else 'BOT остановлен')
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

    def update_strategy_status(self):
        if self.stop_pending:
            self.strategy_status.setText('Останавливается · ожидается завершение операции и закрытие позиции')
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
        elif getattr(self, 'entry_notice', '') and self.running:
            text = 'Вход пропущен · ' + self.entry_notice
        elif self.running and self.last_quote_at is not None and time.monotonic()-self.last_quote_at > .55:
            text = 'Котировка устарела · нет обновлений более 0,55 с'
        elif self.display_position > 0:
            text = 'Позиция открыта' + (' · автоматическая стратегия остановлена' if not self.running else '')
        elif self.running:
            text = 'Ждёт падения до DIP' if 'DIP' in self.chart.levels else 'Получает котировки · формирует базу DIP'
        else:
            text = 'Готов к запуску' if self.mode.currentText() == 'DEMO' or getattr(self, 'selection_ready', False) else 'Выберите рынок · раскройте AutoPair'
        fresh = self.last_quote_at is not None and time.monotonic()-self.last_quote_at <= .55
        if fresh and self.last_price is not None and self.last_price > 0:
            for key in (('TP', 'SL') if self.display_position > 0 else ('DIP',) if self.running else ()):
                if key not in self.chart.levels:
                    continue
                level = Decimal(str(self.chart.levels[key]))
                distance = ((level-self.last_price) if key == 'TP' else (self.last_price-level))/self.last_price*100
                text += f' · до {key}: {distance:.2f}%' if distance > 0 else f' · {key}: уровень достигнут'
        self.strategy_status.setText(text)

    def update_quote_age(self):
        self.update_strategy_status()
        self.refresh_currency()
        if self.last_quote_at is None:
            self.quote_age.setText('Котировок ещё нет')
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
        self.quote_age.setText(f'1 TARGET в {conversion} · {source} · последняя котировка {age:.1f} с назад{state}')

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
        elif name == "error":
            self.journal_toggle.setChecked(True)
            self.footer.setText("ОШИБКА: " + payload)
            QMessageBox.warning(self, "Операция прервана", payload)
        elif name == 'price_context':
            if self.price_source != payload['source']:
                self.reset_price_display()
            self.price_source = payload['source']
            self.display_unit = ('условных единиц (DEMO)' if payload['source'] == 'DEMO' else
                next((name for name, addr in profiles().items()
                      if addr.lower() == payload['quote'].lower()), payload['quote']))
            if payload['source'] == 'BSC' and self.isVisible():
                self.usd.set_token(payload['quote'])
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
        elif name == "pools":
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
            self.remember_amount()
            self.router.blockSignals(True)
            self.quote.blockSignals(True)
            self.router.setCurrentText(payload.router)
            names = catalog(self.store, payload.router)
            pair = next((name for name, token in names.items() if token.lower() == payload.quote.lower()), "ALL")
            self.quote.setCurrentText(pair)
            self.router.blockSignals(False)
            self.quote.blockSignals(False)
            self.amount_key = preferences.pair_key(payload.router, pair)
            self.params["amount"].setText(self.pair_amounts.get(self.amount_key, "0.02"))
            self.pool_input.setText(payload.address)
            self.token.setText(payload.token)
            self.pool_label.setText(payload.label + "\nAMOUNT в активе " + payload.quote)
            self.market_summary.setText("Рынок: " + payload.label)
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
                        item.setForeground(QColor("#60e1bb"))
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
        elif name == "receipt_review":
            self.receipt_result.setText(payload)
            self.refresh_recovery()
        elif name == "status":
            self.refresh_recovery()
            self.quote_unavailable = payload.get('quote_unavailable', False)
            self.entry_notice = payload.get('entry_notice', '')
            self.halt_reason = payload.get('halt_reason', '')
            self.running, self.active_mode, self.locked = payload["running"], payload["mode"], payload["locked"]
            if not self.running and not self.busy and not self.worker.stop_event.is_set():
                self.stop_pending = False
            self.metrics["state"].setText("STOPPING" if self.stop_pending else "LOCKED" if self.locked and self.active_mode == "LIVE" else "WAIT RPC" if self.running and self.quote_unavailable else "WAIT DIP" if self.running and self.entry_notice else "RUNNING" if self.running else "ERROR" if self.halt_reason else "IDLE")
            state = self.metrics['state']
            tone = 'danger' if self.locked and self.active_mode == 'LIVE' else 'positive' if self.running else ''
            if state.property('tone') != tone:
                state.setProperty('tone', tone)
                state.style().unpolish(state)
                state.style().polish(state)
            active = (payload['mode'] == self.mode.currentText() and
                      (payload['running'] or float(payload['position']) > 0) and
                      (self.mode.currentText() == 'DEMO' or self.selection_ready))
            self.base_price = Decimal(payload['base']) if active and Decimal(payload['base']) > 0 else None
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
                "signal_policy": self.signal_policy(),
                "settings": {key: field.text().strip() for key, field in self.params.items()},
                "gas": self.gas.text().strip(), "interval": str(self.interval.value())})
        except (ValueError, OSError):
            QMessageBox.warning(self, "Настройки не сохранены",
                                "Не удалось сохранить параметры. Предыдущие настройки сохранены, если запись не была заменена.")
        self.usd.set_token('')
        event.accept()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--position-check", help="Offline synthetic position UI audit directory")
    parser.add_argument("--position-check-resume", action="store_true")
    parser.add_argument("--market-paper-token", help="Isolated visible PAPER market audit token")
    parser.add_argument("--market-paper-pool", help="Canonical pool for market audit")
    parser.add_argument("--market-paper-output", help="New directory for market audit")
    parser.add_argument("--smoke-test", action="store_true", help="Offline GUI startup test, temporary state")
    parser.add_argument('--display-check', help='Isolated read-only GUI audit directory')
    parser.add_argument('--display-replay', help='Recorded PAPER report with market_samples for GUI replay')
    parser.add_argument("--paper-acceptance", help="Isolated read-only PAPER check directory")
    parser.add_argument("--acceptance-seconds", type=int, default=600)
    parser.add_argument("--acceptance-resume", action="store_true")
    parser.add_argument("--acceptance-endpoint", default="https://bsc-dataseed.binance.org")
    args = parser.parse_args()
    diagnostics = None
    app = QApplication(sys.argv[:1])
    app.setApplicationName("DipBot Mac")
    app.setStyleSheet(STYLE)
    if args.position_check:
        from .position_check import run
        return run(app, args.position_check, args.position_check_resume)
    if args.market_paper_token:
        if not args.market_paper_output:
            parser.error('--market-paper-token requires --market-paper-output')
        from pathlib import Path
        from tools.token_ui_paper_check import run
        return run(args.market_paper_token, Path(args.market_paper_output), args.acceptance_seconds,
                   args.market_paper_pool, exercise_recovery=True, close_after=True)
    if args.display_check:
        if not args.display_replay:
            parser.error('--display-check requires --display-replay')
        from .display_check import run
        return run(app, args.display_check, args.display_replay, args.acceptance_seconds)
    if args.paper_acceptance:
        from .acceptance import run
        return run(app, args.paper_acceptance, args.acceptance_seconds, args.acceptance_resume, args.acceptance_endpoint)
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
        from .diagnostics import Diagnostics
        try:
            diagnostics = Diagnostics(data_dir() / 'diagnostics')
        except (OSError, ValueError):
            diagnostics = None
        try:
            window = Window()
        except Exception:
            if diagnostics is not None:
                diagnostics.close(clean=False)
            QMessageBox.critical(None, "Данные приложения", "Не удалось прочитать state.json. Сохраните его копию для сверки; торговля не запущена")
            return 1
        if diagnostics is not None and diagnostics.previous_unclean:
            window.log('Предыдущая сессия завершилась без отметки штатного выхода. Проверьте позиции и журнал восстановления; диагностика сохранена локально.')
        window.show()
    try:
        result = app.exec()
    except BaseException:
        if diagnostics is not None:
            diagnostics.exception(*sys.exc_info())
            diagnostics.close(clean=False)
        raise
    if diagnostics is not None:
        diagnostics.close(clean=result == 0)
    return result


if __name__ == "__main__":
    sys.exit(main())
