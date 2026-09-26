"""Build Qt screens on the window; event handling stays in Window."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from dipbot.ui.window import Window

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from PySide6.QtWidgets import QWidget

    from dipbot.domain.strategy import Settings

from decimal import Decimal

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from dipbot.domain.strategy import Settings
from dipbot.ui.chart import Chart
from dipbot.ui.ui_components import MetricLabel
from dipbot.ui.usd_feed import UsdRate


def build_bot(self: Window) -> None:
    layout = self.tab("Торговля")
    metrics = QHBoxLayout()
    self.metrics = {}
    self.metric_captions = {}
    for key, title in [
        ("price", "ЦЕНА / БАЗОВЫЙ АКТИВ"),
        ("base", "БАЗА DIP"),
        ("position", "ПОЗИЦИЯ · TARGET"),
        ("state", "СОСТОЯНИЕ"),
    ]:
        box = QWidget()
        box.setObjectName("metricCard")
        box.setMinimumHeight(58)
        card = QVBoxLayout(box)
        card.setContentsMargins(10, 6, 10, 6)
        card.setSpacing(3)
        caption = QLabel(title)
        self.metric_captions[key] = caption
        caption.setObjectName("metricCaption")
        card.addWidget(caption)
        metric = MetricLabel("—")
        metric.setObjectName("metric")
        metric.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        if key == "price":
            metric.setProperty("tone", "positive")
        card.addWidget(metric)
        metrics.addWidget(box, 1)
        self.metrics[key] = metric
    layout.addLayout(metrics)
    self.strategy_status = QLabel("Готов к запуску")
    self.strategy_status.setObjectName("banner")
    self.strategy_status.setWordWrap(True)
    self.strategy_status.setToolTip(
        "Расстояния рассчитаны от последней цены до уровней сигнала, без учёта расходов."
    )
    self.display_position = Decimal(0)
    self.last_price = None
    layout.addWidget(self.strategy_status)
    self.chart = Chart()
    self.chart.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
    self.chart.setToolTip(
        self.chart.toolTip()
        + " USD — ориентировочный пересчёт всех точек по последнему полученному курсу, не исторический валютный график."
    )
    layout.addWidget(self.chart, 1)
    self.levels_label = QLabel("Вход DIP: — · ENTRY: — · TP: — · SL: —")
    self.levels_label.setWordWrap(True)
    self.levels_label.setToolTip(
        "ENTRY — опорная цена стратегии: в PAPER после модельной задержки, в LIVE после receipt. Это не средняя цена исполнения. TP не означает прибыль после расходов."
    )
    self.levels_label.hide()  # Levels are labelled directly on the chart.
    self.quote_age = QLabel("Котировок ещё нет")
    self.quote_age.setObjectName("muted")
    self.quote_age.setWordWrap(True)
    layout.addWidget(self.quote_age)
    self.position_estimate = QLabel("Открытая позиция, USD: —")
    self.position_estimate.setObjectName("muted")
    self.position_estimate.setWordWrap(True)
    self.position_estimate.setToolTip(
        "PAPER: оценка продажи после модельных расходов; LIVE: без будущего газа SELL. Котировка действительна 0,55 с. P&L сравнивается с сохранёнными расходами входа в USD."
    )
    layout.addWidget(self.position_estimate)
    self.trade_details = QLabel("Нет данных об исполнении")
    self.trade_details.setWordWrap(True)
    self.trade_details.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    self.trade_toggle = self.disclosure(
        layout, "Исполнение и результат последней сделки · USD", self.trade_details
    )
    self.trade_toggle.toggled.connect(
        lambda opened: QTimer.singleShot(0, lambda: show_in_tab(self, self.trade_details)) if opened else None
    )
    self.chart.setToolTip(
        self.chart.toolTip()
        + " Маркеры BUY/SELL — рыночные цены, без расходов; цены исполнения показаны в деталях сделки."
    )
    self.base_price = None
    self.usd = UsdRate(self)
    self.gas_usd = UsdRate(self)
    self.usd.changed.connect(self.capture_usd_rates)
    self.gas_usd.changed.connect(self.capture_usd_rates)
    self.usd.changed.connect(self.refresh_currency)
    self.last_quote_at = None
    self.display_unit = "условных единиц (DEMO)"
    self.price_source = "DEMO"
    self.age_timer = QTimer(self)
    self.age_timer.timeout.connect(self.update_quote_age)
    self.age_timer.start(250)
    compact = QGroupBox("ОСНОВНЫЕ ПАРАМЕТРЫ")
    compact_grid = QGridLayout(compact)
    self.params = {}
    for column, (key, title, value) in enumerate(
        [
            ("amount", "Сумма", "0.02"),
            ("dip", "DIP %", str(Settings().dip)),
            ("take_profit", "Take Profit %", str(Settings().take_profit)),
            ("stop_loss", "Stop Loss %", str(Settings().stop_loss)),
        ]
    ):
        field = self.field(value)
        field.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.params[key] = field
        label = QLabel(title)
        label.setBuddy(field)
        field.setAccessibleName(title)
        compact_grid.addWidget(label, 0, column)
        compact_grid.addWidget(field, 1, column)
    self.amount_unit = QComboBox()
    self.amount_unit.addItem("База пары", "quote")
    self.amount_unit.addItem("USD", "usd")
    self.amount_unit.setMinimumContentsLength(8)
    self.amount_unit.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
    self.amount_unit.setToolTip(
        "USD пересчитывается в базу перед каждым входом по свежей ориентировочной котировке; "
        "газ не входит в AMOUNT. Доступно в PAPER/LIVE."
    )
    self.editable.append(self.amount_unit)
    compact_grid.addWidget(QLabel("Валюта суммы"), 0, 4)
    compact_grid.addWidget(self.amount_unit, 1, 4)
    layout.insertWidget(0, compact)
    self.market_summary = MetricLabel("Рынок: DEMO · локальная модель")
    self.market_summary.setMinimumWidth(0)
    layout.addWidget(self.market_summary)
    body_widget = QWidget()
    body = QHBoxLayout(body_widget)
    body.setContentsMargins(0, 0, 0, 0)
    pool_group = QGroupBox("РЫНОК / AUTOPAIR")
    form = self.form(pool_group)
    saved_pool = self.store.data.get("last_pool", {})
    self.token = self.field(saved_pool.get("token", ""), "Адрес токена или пула BSC")
    self.token.setAccessibleName("Адрес токена или пула BSC")
    form.addRow(QLabel("TOKEN ADDRESS"))
    form.addRow(self.token)
    selectors = QHBoxLayout()
    self.router = QComboBox()
    self.router.addItems(["AUTO", "V2", "V3"])
    self.router.setAccessibleName("Версия маршрута")
    self.quote = QComboBox()
    self.quote.setAccessibleName("Базовый актив")
    self.editable += [self.router, self.quote]
    selectors.addWidget(self.router)
    selectors.addWidget(self.quote, 1)
    form.addRow("ROUTER / PAIR", selectors)
    form.addRow(
        self.button(
            "AutoPair · найти пулы",
            lambda: self.send(
                "discover",
                token=self.token.text(),
                quote=self.quote.currentText(),
                router=self.router.currentText(),
            ),
        )
    )
    self.pool_input = self.field(saved_pool.get("address", ""), "Адрес известного пула PancakeSwap")
    self.pool_input.setAccessibleName("Адрес пула PancakeSwap")
    form.addRow(QLabel("POOL ADDRESS"))
    form.addRow(self.pool_input)
    form.addRow(
        self.button(
            "CHECK POOL", lambda: self.send("verify", token=self.token.text(), pool=self.pool_input.text())
        )
    )
    self.candidates = QComboBox()
    self.candidates.setAccessibleName("Найденные маршруты")
    self.candidates.setMinimumContentsLength(20)
    self.candidates.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
    self.editable.append(self.candidates)
    form.addRow("Маршрут", self.candidates)
    choose = QHBoxLayout()
    choose.addWidget(self.button("Выбрать", self.select_pool))
    choose.addWidget(self.button("ADD BASE", lambda: self.send("add_profile")))
    form.addRow(choose)
    compare_button = self.button("Сравнить на AMOUNT", self.compare_routes)
    compare_button.setToolTip(
        "Котировки BUY и обратного SELL для найденных пулов той же пары на одном блоке. "
        "Результат не меняет выбранный маршрут и не гарантирует продажу."
    )
    form.addRow(compare_button)
    self.route_comparison = QLabel("Сравнение маршрутов ещё не выполнено")
    self.route_comparison.setWordWrap(True)
    self.route_comparison.setObjectName("muted")
    form.addRow(self.route_comparison)
    self.pool_label = QLabel("DEMO использует локальную модель цены")
    self.pool_label.setWordWrap(True)
    self.pool_label.setObjectName("muted")
    form.addRow(self.pool_label)
    body.addWidget(pool_group, 3)
    strategy = QGroupBox("ПАРАМЕТРЫ СТРАТЕГИИ")
    grid = self.form(strategy)
    for key, title, value in [
        ("slippage", "SLIPPAGE %", str(Settings().slippage)),
        ("dynamic", "DYNAMIC", str(Settings().dynamic)),
        ("min_swaps", "Мин. Swap за 100 блоков · 0 выкл.", "0"),
        ("max_roundtrip_loss", "Макс. потери BUY→SELL %", "3"),
    ]:
        self.params[key] = self.field(value)
        self.params[key].setAlignment(Qt.AlignmentFlag.AlignRight)
        grid.addRow(title, self.params[key])
    self.params["max_roundtrip_loss"].setToolTip(
        "Проверка котировок входа и обратного выхода на одном блоке до покупки. "
        "Включает комиссии пула и влияние суммы. Не учитывает газ, token tax и изменение пула после BUY; "
        "не гарантирует возможность будущей продажи. Применяется в PAPER и LIVE."
    )
    self.signal_mode = QComboBox()
    self.signal_mode.setMinimumContentsLength(12)
    self.signal_mode.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
    self.signal_mode.addItem("Совместимость · два снижения", "legacy")
    self.signal_mode.addItem("DIP от максимума за окно", "window")
    self.signal_mode.addItem("DIP с порогом волатильности", "volatility")
    self.signal_volatility = QDoubleSpinBox()
    self.signal_volatility.setRange(0.1, 20)
    self.signal_volatility.setValue(2)
    self.signal_volatility.setToolTip(
        "Эксперимент: max(DIP, min(20%, множитель × σ доходностей новых блоков)). "
        "Нужно 10 изменений; без block ID берётся не больше одного наблюдения в секунду. "
        "Доходность не подтверждена."
    )
    self.editable.append(self.signal_volatility)
    self.signal_window = QDoubleSpinBox()
    self.signal_window.setRange(1, 300)
    self.signal_window.setValue(60)
    self.signal_window.setSuffix(" s")
    self.signal_rebound = QDoubleSpinBox()
    self.signal_rebound.setRange(0, 20)
    self.signal_rebound.setDecimals(2)
    self.signal_rebound.setSuffix(" %")
    self.signal_mode.setToolTip(
        "Оконный режим экспериментальный: максимум наблюдавшихся цен за окно, "
        "затем DIP и необязательный отскок. Параметры не оптимизированы по доходности."
    )
    self.block_age_limit = QDoubleSpinBox()
    self.block_age_limit.setRange(1, 30)
    self.block_age_limit.setValue(5)
    self.block_age_limit.setSuffix(" s")
    self.block_age_limit.setToolTip(
        "Возраст timestamp блока по часам Mac. Старые блоки не принимаются как свежие котировки. "
        "Это отдельно от предела разрыва наблюдений 0.55 с; timestamp блока имеет секундную точность."
    )
    section = QLabel("СИГНАЛ И СВЕЖЕСТЬ ДАННЫХ")
    section.setObjectName("metricCaption")
    grid.addRow(section)
    grid.addRow("Расчёт DIP", self.signal_mode)
    grid.addRow("Окно максимума", self.signal_window)
    grid.addRow("Множитель волатильности", self.signal_volatility)
    grid.addRow("Подтверждение отскока", self.signal_rebound)
    grid.addRow("Макс. возраст блока", self.block_age_limit)
    self.paper_delay = QDoubleSpinBox()
    self.paper_delay.setRange(0, 10)
    self.paper_delay.setValue(0.25)
    self.paper_delay.setSuffix(" s")
    self.paper_fee = self.field("0")
    self.paper_fee.setToolTip(
        "Фиксированная стоимость BUY и SELL в базовом активе выбранной пары. "
        "0 исключает газ; это допущение, не on-chain оценка. Комиссии пула уже включены в router quote. "
        "При смене базового актива проверьте сумму."
    )
    self.editable.append(self.paper_delay)
    section = QLabel("МОДЕЛЬ PAPER И РАСХОДЫ")
    section.setObjectName("metricCaption")
    grid.addRow(section)
    grid.addRow("Задержка исполнения PAPER", self.paper_delay)
    grid.addRow("Стоимость операции PAPER · база", self.paper_fee)
    self.paper_gas = QDoubleSpinBox()
    self.paper_gas.setRange(0, 2000000)
    self.paper_gas.setDecimals(0)
    self.paper_gas.setToolTip(
        "Газ на каждую виртуальную BUY/SELL: units × GAS GWEI, "
        "пересчёт в базу по свежим USD-курсам. Это заданная модель, не estimateGas. "
        "Добавляется к фиксированной стоимости; комиссии пула уже в router quote. 0 отключает."
    )
    self.editable.append(self.paper_gas)
    grid.addRow("Газ PAPER на операцию · 0 выкл.", self.paper_gas)
    self.cost_limit = QDoubleSpinBox()
    self.cost_limit.setRange(0, 100)
    self.cost_limit.setSuffix(" %")
    self.cost_limit.setToolTip(
        "Необязательный фильтр одной покупки: BUY→SELL quote + заданная модель газа. "
        "0 отключает. Это не накопительный бюджет. Задавайте ниже TP, если цель должна покрывать расходы; "
        "token tax и будущая цена газа не предсказаны."
    )
    self.cost_gas = QDoubleSpinBox()
    self.cost_gas.setRange(21000, 2000000)
    self.cost_gas.setDecimals(0)
    self.cost_gas.setValue(400000)
    self.cost_gas.setToolTip(
        "Допущение полного цикла, не измеренный gas estimate данного токена. "
        "Зависит от approve, wrap и маршрута; уточняйте по receipts/fork."
    )
    self.editable += [self.cost_limit, self.cost_gas]
    grid.addRow("Расходы цикла · 0 выкл.", self.cost_limit)
    grid.addRow("Модель газа на цикл", self.cost_gas)
    self.exit_basis = QComboBox()
    self.exit_basis.addItem("Цена пула (совместимость)", "spot")
    self.exit_basis.addItem("Котировка продажи позиции", "quote")
    self.exit_basis.setToolTip(
        "Router quote всей позиции относительно вложенной суммы. "
        "Включает комиссии пула и impact, исключает газ и token tax. "
        "Линии TP/SL по spot в этом режиме скрыты; trailing остаётся по цене пула."
    )
    self.editable.append(self.exit_basis)
    section = QLabel("ВЫХОД И ПОВТОРНЫЙ ВХОД")
    section.setObjectName("metricCaption")
    grid.addRow(section)
    grid.addRow("База TP/SL", self.exit_basis)
    self.exit_fields = {}
    for key, exit_label, maximum, suffix in (
        ("trailing_pct", "Trailing stop · 0 выкл.", 99.99, " %"),
        ("max_hold_seconds", "Макс. время позиции · 0 выкл.", 86400, " s"),
        ("cooldown_seconds", "Пауза после выхода", 3600, " s"),
    ):
        exit_field = QDoubleSpinBox()
        exit_field.setRange(0, maximum)
        exit_field.setDecimals(2)
        exit_field.setSuffix(suffix)
        self.exit_fields[key] = exit_field
        self.editable.append(exit_field)
        grid.addRow(exit_label, exit_field)
    self.exit_fields["trailing_pct"].setToolTip(
        "Падение от максимальной наблюдавшейся цены после входа. TP/SL имеют приоритет. "
        "По умолчанию после trailing stop автоматическая торговля останавливается."
    )
    self.exit_fields["max_hold_seconds"].setToolTip(
        "Выход на первой свежей котировке после срока. При отсутствии сети точное время не гарантируется."
    )
    self.continue_after_exit = QCheckBox("Продолжать после SL / trailing")
    self.continue_after_exit.setToolTip(
        "После подтверждённого выхода ждать cooldown и новый DIP. "
        "STOP, ошибка исполнения и неизвестная транзакция не перезапускаются. "
        "Может приводить к последовательным убыточным входам; по умолчанию выключено."
    )
    self.continue_after_exit.toggled.connect(
        lambda enabled: self.exit_fields["cooldown_seconds"].setMinimum(1 if enabled else 0)
    )
    self.editable.append(self.continue_after_exit)
    grid.addRow(self.continue_after_exit)
    self.record_market = QCheckBox("Записывать рынок для повторной проверки")
    self.record_market.setChecked(True)
    self.record_market.setToolTip(
        "Локальные цены/блоки/сигналы без ключей и RPC URL. Части до 10 MiB с автоматическим продолжением, "
        "200 MiB на архив; переполнение отмечается как неполные данные."
    )
    self.editable.append(self.record_market)
    grid.addRow(self.record_market)
    self.editable += [self.signal_mode, self.signal_window, self.signal_rebound, self.block_age_limit]
    self.interval = QDoubleSpinBox()
    self.interval.setRange(0.1, 0.5)
    self.interval.setDecimals(3)
    self.interval.setValue(0.1)
    self.interval.setToolTip("V3: минимум 0.103 с. Разрыв наблюдений > 0.55 с сбрасывает базу DIP.")
    self.interval.setSuffix(" s")
    self.editable.append(self.interval)
    grid.addRow("Интервал опроса", self.interval)
    # Keep market selection and strategy settings independently discoverable.
    self.strategy_toggle = self.disclosure(layout, "Дополнительные параметры стратегии", strategy)
    self.market_toggle = self.disclosure(layout, "Выбрать рынок · AutoPair", body_widget)
    layout.removeWidget(self.market_summary)
    layout.removeWidget(self.market_toggle)
    market_row = QHBoxLayout()
    market_row.addWidget(self.market_summary, 1)
    market_row.addWidget(self.market_toggle)
    layout.insertLayout(0, market_row)
    layout.removeWidget(body_widget)
    layout.insertWidget(1, body_widget)
    self.market_toggle.toggled.connect(
        lambda opened: QTimer.singleShot(0, lambda: show_in_tab(self, body_widget)) if opened else None
    )
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
    row.addWidget(
        self.button("BUY BASE", lambda: self.trade("convert", buy=True, amount=self.convert_amount.text()))
    )
    row.addWidget(self.button("SELL ALL BASE → BNB", lambda: self.trade("convert", buy=False, amount="0")))
    row.addWidget(self.button("SELL WALLET → BNB", lambda: self.trade("sweep"), "danger"))
    self.disclosure(layout, "CONVERTER / WALLET SWEEP · LIVE", converter)


def build_settings(self: Window) -> None:
    from dipbot.market.rpc_presets import BACKUP, MAIN

    layout = self.tab("RPC и кошелёк")
    group = QGroupBox("ПОДКЛЮЧЕНИЕ BSC")
    form = self.form(group)
    self.rpc = self.field(placeholder="https://… — ваш BSC HTTP RPC")
    self.rpc.setEchoMode(QLineEdit.EchoMode.PasswordEchoOnEdit)
    self.rpc_preset = self.add_rpc_presets(form, "Источник RPC", self.rpc, MAIN)
    form.addRow("HTTP RPC", self.rpc)
    self.backup_rpc = self.field(placeholder="Резервный HTTPS RPC · только чтение котировок")
    self.backup_rpc.setEchoMode(QLineEdit.EchoMode.PasswordEchoOnEdit)
    self.backup_rpc_preset = self.add_rpc_presets(form, "Источник резерва", self.backup_rpc, BACKUP)
    form.addRow("Резервный RPC", self.backup_rpc)
    self.ws_rpc = self.field(placeholder="Необязательный wss://… · новые блоки + HTTP fallback")
    self.ws_rpc.setEchoMode(QLineEdit.EchoMode.PasswordEchoOnEdit)
    form.addRow("WebSocket RPC", self.ws_rpc)
    self.send_rpc = self.field(placeholder="Необязательный HTTPS RPC отправки")
    self.send_rpc.setEchoMode(QLineEdit.EchoMode.PasswordEchoOnEdit)
    self.send_rpc.setToolTip(
        "Например, private RPC выбранного провайдера. Приватность определяется провайдером; "
        "этот клиент её не доказывает. Отправка только через выбранный endpoint, без публичного fallback. "
        "При неопределённом ответе журнал остаётся заблокирован. Пусто — основной RPC."
    )
    form.addRow("Отправка транзакций", self.send_rpc)
    self.adaptive_rpc = QCheckBox("Выбирать RPC котировок по задержке и ошибкам")
    self.adaptive_rpc.setToolTip(
        "Основной и резервный узел: проба альтернативы раз в 30 с, "
        "переключение после трёх замеров при преимуществе 25%. Отправка остаётся на основном RPC."
    )
    self.editable.append(self.adaptive_rpc)
    form.addRow(self.adaptive_rpc)
    self.timing_report = QPlainTextEdit()
    self.timing_report.setReadOnly(True)
    self.timing_report.setMaximumHeight(150)
    self.timing_report.setPlaceholderText("Задержки появятся после запросов. p50/p95/p99 — мс.")
    self.timing_report.setToolTip(
        "До 2048 последних замеров на ряд; счётчики за текущий процесс. "
        "Включены неудачные попытки. Это задержки приложения, не гарантия включения сделки в блок."
    )
    form.addRow("Измерения задержек", self.timing_report)
    self.timing_timer = QTimer(self)
    self.timing_timer.setInterval(5000)
    self.timing_timer.timeout.connect(self.refresh_timings)
    self.timing_timer.start()
    self.rpc_health_label = QLabel("")
    self.rpc_health_label.setWordWrap(True)
    form.addRow(self.rpc_health_label)
    self.save_rpc = QCheckBox("Сохранить RPC в macOS Keychain")
    self.editable.append(self.save_rpc)
    form.addRow(self.save_rpc)
    row = QHBoxLayout()
    row.addWidget(
        self.button(
            "Подключить",
            lambda: self.send(
                "connect",
                rpc=self.rpc.text().strip(),
                send_rpc=self.send_rpc.text().strip(),
                adaptive_rpc=self.adaptive_rpc.isChecked(),
                backup_rpc=self.backup_rpc.text().strip(),
                ws_rpc=self.ws_rpc.text().strip(),
                save=self.save_rpc.isChecked(),
            ),
        )
    )
    row.addWidget(self.button("Загрузить RPC из Keychain", self.load_rpc))
    form.addRow(row)
    self.gas = self.field("0.1")
    self.gas_reserve = self.field("0.0001")
    self.gas_reserve.setToolTip(
        "Резерв BNB сохраняется при входах и Converter BUY; не ограничивает оборот. "
        "При выходе SELL/Sweep можно расходовать резерв. Это заданный запас, не гарантия достаточного газа."
    )
    form.addRow("Резерв газа, BNB", self.gas_reserve)
    self.gas.setMaximumWidth(180)
    self.gas.setAlignment(Qt.AlignmentFlag.AlignRight)
    form.addRow("GAS GWEI", self.gas)
    hint = QLabel(
        "Лимит: 0.005 BNB газа на транзакцию. Накопительного бюджета оборота нет; размер покупки задаётся AMOUNT."
    )
    hint.setWordWrap(True)
    hint.setObjectName("muted")
    form.addRow(hint)
    layout.addWidget(group)
    accounting = QGroupBox("ИСТОРИЯ USD И РАСХОДЫ")
    accounting_layout = QVBoxLayout(accounting)
    self.accounting_text = QPlainTextEdit()
    self.accounting_text.setReadOnly(True)
    self.accounting_text.setMaximumHeight(180)
    self.accounting_text.setPlaceholderText("Исторический USD-учёт отслеживаемых позиций и расходы газа")
    accounting_layout.addWidget(self.accounting_text)
    accounting_layout.addWidget(
        self.button("Обновить USD-учёт и расходы", lambda: self.send("accounting_report"))
    )
    layout.addWidget(accounting)
    wallet = QGroupBox("КОШЕЛЁК")
    form = self.form(wallet)
    self.key = self.field(placeholder="Private key отдельного BSC-кошелька")
    self.key.setEchoMode(QLineEdit.EchoMode.Password)
    form.addRow("PRIVATE KEY", self.key)
    form.addRow(self.button("VERIFY WALLET AND SAVE · Keychain", self.save_wallet))
    self.wallet = self.field(
        self.store.data.get("wallet_address", ""), "Публичный адрес 0x… для просмотра балансов"
    )
    form.addRow("WALLET ADDRESS", self.wallet)
    form.addRow(self.button("Обновить балансы", self.balances))
    layout.addWidget(wallet)
    recovery = self.recovery_group = QGroupBox("ВОССТАНОВЛЕНИЕ LIVE")
    rec = QVBoxLayout(recovery)
    self.recovery_details = QLabel()
    self.recovery_details.setTextFormat(Qt.TextFormat.PlainText)
    self.recovery_details.setWordWrap(True)
    self.recovery_details.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    rec.addWidget(self.recovery_details)
    self.saved_positions = QComboBox()
    rec.addWidget(self.saved_positions)
    rec.addWidget(self.button("Подготовить сохранённый пул", self.prepare_saved_position))
    self.receipt_result = QLabel("Подключите RPC, затем проверьте receipts. Для чтения ключ не требуется.")
    self.receipt_result.setWordWrap(True)
    rec.addWidget(self.receipt_result)
    self.position_comparison = QLabel("Балансы сохранённых позиций ещё не сверены с сетью.")
    self.position_comparison.setTextFormat(Qt.TextFormat.PlainText)
    self.position_comparison.setWordWrap(True)
    rec.addWidget(self.position_comparison)
    rec.addWidget(self.button("Сверить сохранённые позиции с сетью", self.compare_saved_positions))
    text = QLabel(
        "После таймаута или аварийного закрытия LIVE блокируется. Сначала проверьте receipt и балансы. "
        "Снятие блокировки сбрасывает кэш позиций; реальные остатки остаются в кошельке."
    )
    text.setWordWrap(True)
    rec.addWidget(text)
    rec.addWidget(self.button("Проверить receipts", self.check_receipts))
    rec.addWidget(self.button("Отменить pending тем же nonce…", self.cancel_pending, "danger"))
    rec.addWidget(self.button("Балансы сверены · снять блокировку", self.unlock, "danger"))
    layout.addWidget(recovery)
    layout.addStretch()


def build_pairs(self: Window) -> None:
    layout = self.tab("Активы и балансы")
    row = QHBoxLayout()
    hint = QLabel("Базовые активы для AutoPair · балансы и ликвидность проверяются через RPC")
    hint.setWordWrap(True)
    hint.setObjectName("muted")
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
    self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
    self.table.setHorizontalHeaderLabels(["Актив", "Контракт BSC", "Баланс"])
    self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
    self.table.setColumnWidth(0, 160)
    self.table.setColumnWidth(2, 180)
    self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
    self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
    self.table.doubleClicked.connect(self.pair_clicked)
    layout.addWidget(self.table)
    layout.addWidget(QLabel("Двойной клик по активу выбирает PAIR для AutoPair. «?» означает ошибку чтения."))
    layout.addWidget(self.button("REMOVE выбранную пользовательскую базу", self.remove_profile))


def build_about(self: Window) -> None:
    layout = self.tab("О реализации")
    intro = QLabel("DipBot Mac · Руководство")
    intro.setObjectName("title")
    layout.addWidget(intro)
    sections = [
        (
            "РЕЖИМЫ РАБОТЫ",
            "<b>DEMO</b> — заданный локальный цикл цен, без RPC и кошелька.<br><br>"
            "<b>PAPER</b> — реальные цены и router quotes на размер виртуальной сделки. "
            "Комиссия пула и price impact входят в котировку; задержка и фиксированные расходы задаются моделью PAPER. Token tax и MEV не моделируются. "
            "Slippage ограничивает исполнение BUY, а не списывается как комиссия. DEMO/REPLAY сохраняют стресс-модель.<br><br>"
            "<b>LIVE</b> — реальные транзакции. Нужны RPC, кошелёк и проверенный пул.",
        ),
        (
            "ЦЕНА И СТРАТЕГИЯ",
            "Стратегия использует стоимость <b>1 TARGET в базовом активе</b>; USD в UI — справочный пересчёт. "
            "Сумма задаётся в базе или USD; количество TARGET — число токенов позиции.<br><br>"
            "В режиме совместимости база обновляется при росте или двух снижениях; "
            "проверка DIP выполняется первой. Оконный режим использует максимум за выбранное время и необязательный отскок. Разрыв наблюдений больше <b>0.55 с</b> сбрасывает базу входа.<br><br>"
            "В LIVE база TP/SL — цена пула после receipt BUY; в PAPER — опорная цена после модельной задержки. Это не средняя цена исполнения. TAKE PROFIT не гарантирует прибыль после расходов. "
            "После STOP LOSS бот останавливается; скачок цены может превысить заданный порог.",
        ),
        (
            "УПРАВЛЕНИЕ И ВОССТАНОВЛЕНИЕ",
            "<b>STOP</b> останавливает стратегию и закрывает позицию. "
            "Если транзакция уже отправлена, бот ждёт receipt. При неизвестном результате LIVE блокируется: "
            "проверяйте receipts и балансы во вкладке «RPC и кошелёк».<br><br>"
            "Converter может выполнять несколько транзакций. При частичном сбое промежуточный актив "
            "остаётся в кошельке. V3 не поддерживает fee-on-transfer токены.",
        ),
        (
            "О ПРИЛОЖЕНИИ",
            "Независимая экспериментальная реализация для macOS, PancakeSwap V2/V3, BSC. "
            "Полная эквивалентность Windows-оригиналу и прибыльность стратегии не подтверждены.",
        ),
    ]
    for title, description in sections:
        group = QGroupBox(title)
        box = QVBoxLayout(group)
        text = QLabel(description)
        text.setWordWrap(True)
        text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        box.addWidget(text)
        layout.addWidget(group)
    layout.addStretch()


def show_in_tab(view: Window, widget: QWidget) -> None:
    tab = view.tabs.widget(0)
    if isinstance(tab, QScrollArea):
        tab.ensureWidgetVisible(widget)
