from __future__ import annotations

import time
from decimal import Decimal
from decimal import Decimal as D
from typing import TYPE_CHECKING, Any

from dipbot.domain.usd import price_text
from dipbot.observability.telemetry import TIMINGS
from dipbot.ui.ui_components import set_tone

if TYPE_CHECKING:
    from dipbot.ui.window import Window


def reset_price_display(view: Window) -> None:
    view.presentation.market_block = view.presentation.market_block_timestamp = None
    view.presentation.market_rpc_source = "BSC"
    view.chart.clear()
    view.usd.set_token("")
    view.gas_usd.set_token("")
    view.presentation.base_price = None
    view.presentation.last_quote_at = None
    view.presentation.last_price = None
    view.presentation.display_position = Decimal(0)
    view.presentation.price_source = view.mode.currentText()
    view.metrics["price"].setText("—")
    view.metrics["base"].setText("—")
    view.levels_label.setText("Вход DIP: — · ENTRY: — · TP: — · SL: —")
    view.update_quote_age()


def display_price(view: Window, value: D | str | float | int | None, digits: int = 8) -> str:
    if value is None:
        return "—"
    if view.presentation.price_source in ("DEMO", "REPLAY"):
        return f"{float(value):.{digits}g}"
    return price_text(value, view.usd.current(), digits)


def capture_usd_rates(view: Window) -> None:
    for source in (view.usd, view.gas_usd):
        view.worker.rates.update(source.token, source.current(), source.received_at)


def refresh_currency(view: Window) -> None:
    if not hasattr(view, "usd") or not hasattr(view, "price_source"):
        return
    rate = view.usd.current() if view.presentation.price_source not in ("DEMO", "REPLAY") else None
    view.chart.usd_rate = rate
    view.chart.reference_base = view.presentation.base_price
    unit = (
        "USD"
        if rate is not None
        else ("DEMO" if view.presentation.price_source == "DEMO" else view.presentation.display_unit)
    )
    if len(unit) > 16:
        unit = unit[:6] + "…" + unit[-4:]
    view.metric_captions["price"].setText("ЦЕНА, " + unit)
    view.metric_captions["base"].setText("БАЗА DIP, " + unit)
    view.metrics["price"].setText(view.display_price(view.presentation.last_price))
    view.metrics["base"].setText(view.display_price(view.presentation.base_price))
    if view.presentation.last_price is not None:
        view.metrics["price"].setToolTip(
            f"{view.presentation.last_price} {view.presentation.display_unit} / TARGET · USD — ориентировочный пересчёт"
        )
    view.chart.update()
    view.refresh_pnl()


def refresh_trade_details(view: Window) -> None:
    payload = view.presentation.pnl_status
    same = payload.get("mode") == view.mode.currentText() and view.mode.currentText() != "DEMO"
    detail = payload.get("trade_detail") if same else None

    def usd(value: Any) -> str:
        if value is None:
            return "— (нет данных)"
        return "≈ $" + price_text(Decimal(value), digits=6)

    if detail:
        rows = [
            ("Средняя цена BUY (без доп. расходов)", "buy_price_usd"),
            ("Средняя цена SELL (без доп. расходов)", "sell_price_usd"),
            ("Обмен на входе", "entry_gross_usd"),
            ("Расходы входа", "entry_fee_usd"),
            ("Всего затрачено", "entry_total_usd"),
            ("Получено от продажи", "exit_gross_usd"),
            ("Расходы выхода", "exit_fee_usd"),
            ("Результат закрытия", "net_usd"),
        ]
        view.trade_details.setText(
            "\n".join(label + ": " + usd(detail.get(key)) for label, key in rows)
            + "\nКомиссия пула уже в суммах обмена; повторно не вычитается. "
            + (
                "Расходы PAPER — модель."
                if view.mode.currentText() == "PAPER"
                else "Расходы LIVE — записанный газ; неполный учёт не оценивается."
            )
            + ("\nЧастичный/неполный выход: итог неизвестен." if detail.get("complete") is False else "")
        )
    else:
        view.trade_details.setText("Нет данных об исполнении для выбранного режима/рынка")
    estimate = payload.get("open_estimate") if same else None
    age = time.monotonic() - estimate["at"] if estimate else None
    if not view.presentation.display_position:
        text = "Открытая позиция, USD: —"
    elif estimate and age is not None and 0 <= age <= 0.55 and not view.presentation.quote_unavailable:
        text = "Оценка продажи: " + usd(estimate["value_usd"]) + " · P&L позиции: " + usd(estimate["pnl_usd"])
        text += " · без будущего газа SELL" if estimate["excludes_exit_gas"] else " · по модели PAPER"
        text += f" · {age:.1f} с назад"
    else:
        text = "Открытая позиция, USD: — (нет свежей котировки продажи)"
    if same and view.presentation.display_position and not payload.get("running"):
        text += " · наблюдение без автоторговли"
        error = payload.get("position_watch_error")
        if error:
            text += " · " + error
    view.position_estimate.setText(text)


def refresh_pnl(view: Window) -> None:
    payload = view.presentation.pnl_status
    if not payload:
        return
    view.refresh_trade_details()
    mode = view.mode.currentText()
    text = f"{mode} · " + ("BOT работает" if view.presentation.running else "BOT остановлен")
    historical = payload.get("historical_usd") if mode != "DEMO" and payload["mode"] == mode else None
    if historical is not None:
        value = historical.get("value")
        if value is None:
            result = "— (неполный USD-учёт)" if historical.get("missing") else "— (нет закрытых сделок)"
        else:
            usd = Decimal(value)
            amount = (
                format(abs(usd), ".2f")
                if abs(usd) >= Decimal(".01") or not usd
                else price_text(abs(usd), digits=4)
            )
            result = "≈ " + ("−" if usd < 0 else "+" if usd > 0 else "") + "$" + amount
        suffix = (
            " · по модели PAPER"
            if mode == "PAPER"
            else " · газ BUY/SELL учтён"
            if historical["includes_gas"]
            else " · без газа"
        )
        view.footer.setText(
            text
            + " · Закрытый P&L: "
            + result
            + suffix
            + (" · LIVE LOCKED" if view.presentation.locked else "")
        )
        view.footer.setToolTip(
            "USD по сохранённым ориентировочным курсам на моменты исполнения (DEX Screener, возраст до 90 с). "
            "Смена текущего курса не пересчитывает закрытый результат. LIVE: отслеживаемые позиции этого кошелька "
            "с начала нового USD-учёта, включая учтённые закрытия Sweep и распределённый газ. Прочие расходы показаны отдельно в USD-учёте; старые неполные записи не восстанавливаются догадкой. "
            f"Закрыто: {historical.get('closed', 0)}, неполных: {historical.get('missing', 0)}. PAPER учитывает заданную стоимость операций по модели; token tax не учтён."
        )
        return
    if payload["mode"] != mode or payload["realized"] == "—":
        result = "—"
    else:
        realized = Decimal(payload["realized"])
        token = payload.get("pnl_quote", "").lower()
        rate = (
            view.usd.current()
            if mode != "DEMO"
            and view.presentation.price_source != "REPLAY"
            and token
            and view.usd.token == token
            else None
        )
        if rate is not None:
            usd = realized * rate
            amount = (
                format(abs(usd), ".2f")
                if abs(usd) >= Decimal("0.01") or not usd
                else price_text(abs(usd), digits=4)
            )
            result = "≈ " + ("−" if usd < 0 else "+" if usd > 0 else "") + "$" + amount
        else:
            result = "— (USD недоступен)" if mode != "DEMO" else "— (DEMO без USD)"
    view.footer.setText(
        text
        + " · Закрытый P&L: "
        + result
        + " · без газа"
        + (" · LIVE LOCKED" if view.presentation.locked else "")
    )
    view.footer.setToolTip(
        "Результат закрытых сделок. USD — пересчёт по текущему курсу базового актива, "
        "не исторический долларовый P&L. PAPER не учитывает газ и token tax."
    )


def update_state_badge(view: Window) -> None:
    """Presentation of existing worker/UI states; never changes trading decisions."""
    tone = ""
    if view.presentation.stop_pending:
        text, tone = "STOPPING", "warning"
    elif view.presentation.exit_retry:
        text, tone = "EXIT RPC", "warning"
    elif view.presentation.locked and view.mode.currentText() == "LIVE":
        text, tone = "LOCKED", "danger"
    elif view.presentation.searching:
        text = "SEARCH"
    elif view.presentation.busy:
        text = {"buy": "BUYING", "sell": "SELLING", "sweep": "SWEEP", "convert": "CONVERT"}.get(
            view.presentation.ui_command, "WAIT"
        )
    elif view.worker.execution_monitor is not None:
        text = "EXECUTING"
    elif view.presentation.running and view.presentation.quote_unavailable:
        text, tone = "WAIT RPC", "warning"
    elif (
        view.presentation.running
        and view.presentation.last_quote_at is not None
        and time.monotonic() - (view.presentation.last_quote_at or 0) > 0.55
    ):
        text, tone = "STALE", "warning"
    elif not view.presentation.running and (view.presentation.halt_reason or view.presentation.ui_error):
        text, tone = "ERROR", "danger"
    elif view.presentation.display_position > 0:
        text, tone = "POSITION", "positive"
    elif view.presentation.running:
        text = {"rebound": "REBOUND", "cooldown": "COOLDOWN", "warmup": "WARMUP", "baseline": "BASELINE"}.get(
            view.presentation.wait_reason, "WAIT DIP"
        )
        tone = "positive"
    elif "PENDING" in view.pool_label.text() and view.mode.currentText() != "DEMO":
        text, tone = "PENDING", "warning"
    else:
        text = "IDLE"
    view.metrics["state"].setText(text)
    set_tone(view.metrics["state"], tone)
    set_tone(view.strategy_status, tone)


def update_strategy_status(view: Window) -> None:
    view.update_state_badge()
    if view.presentation.searching and not view.presentation.stop_pending:
        view.strategy_status.setText("Поиск · проверяется адрес, ликвидность и доступные маршруты")
        return
    if (
        view.presentation.ui_error
        and not view.presentation.running
        and not view.presentation.stop_pending
        and not view.presentation.locked
    ):
        view.strategy_status.setText("Ошибка операции · " + view.presentation.ui_error)
        return
    if (
        not view.presentation.running
        and not view.presentation.busy
        and not view.presentation.stop_pending
        and not view.presentation.locked
        and not view.presentation.selection_ready
        and view.mode.currentText() != "DEMO"
    ):
        view.strategy_status.setText(
            view.pool_label.text()
            if "DEMO" not in view.pool_label.text()
            else "Выберите рынок · раскройте AutoPair"
        )
        return
    retry = view.presentation.exit_retry
    if retry:
        remaining = max(0, retry["retry_at"] - time.monotonic())
        view.strategy_status.setText(
            ("Останавливается · " if view.presentation.stop_pending else "")
            + f"Позиция открыта, выход ожидает RPC · {retry['error']} · попытка {retry['attempt']}/{retry['limit']} через {remaining:.1f} с"
        )
        return
    if view.presentation.stop_pending:
        view.strategy_status.setText("Останавливается · ожидается завершение операции и закрытие позиции")
        return
    if view.presentation.busy and view.presentation.ui_command in ("buy", "sell", "convert", "sweep"):
        view.strategy_status.setText(
            {"buy": "Покупка", "sell": "Продажа", "convert": "Конвертация", "sweep": "Продажа остатков"}[
                view.presentation.ui_command
            ]
            + " · ожидается результат исполнения"
        )
        return
    if view.worker.execution_monitor is not None and view.mode.currentText() == view.worker.mode:
        view.strategy_status.setText(
            "Сделка выполняется · цена обновляется отдельно; ожидается результат исполнения"
        )
        return
    if view.presentation.quote_unavailable and view.presentation.running:
        view.strategy_status.setText(
            "Нет котировок · повтор чтения; "
            + (
                "позиция открыта, TP/SL временно недоступны"
                if view.presentation.display_position > 0
                else "новые входы запрещены"
            )
        )
        return
    if view.presentation.locked and view.mode.currentText() == "LIVE":
        text = "Требуется сверка LIVE · проверьте незавершённую операцию"
    elif view.presentation.halt_reason and not view.presentation.running:
        action = (
            " · позиция сохранена; после устранения причины повторите SELL POSITION или STOP"
            if view.presentation.display_position > 0
            else " · проверьте причину перед START"
        )
        text = "Остановлен из-за ошибки · " + view.presentation.halt_reason + action
    elif (
        view.presentation.running
        and view.presentation.last_quote_at is not None
        and time.monotonic() - (view.presentation.last_quote_at or 0) > 0.55
    ):
        text = "Котировка устарела · нет обновлений более 0,55 с"
    elif view.presentation.entry_notice and view.presentation.running:
        text = (
            view.presentation.entry_notice
            if view.presentation.entry_notice.startswith("Пауза после выхода:")
            else "Вход пропущен · " + view.presentation.entry_notice
        )
    elif (
        view.presentation.signal_notice
        and view.presentation.running
        and not view.presentation.display_position
    ):
        text = view.presentation.signal_notice
    elif view.presentation.display_position > 0:
        text = "Позиция открыта" + (
            " · автоматическая стратегия остановлена" if not view.presentation.running else ""
        )
    elif view.presentation.running:
        text = (
            "Ждёт падения до DIP" if "DIP" in view.chart.levels else "Получает котировки · формирует базу DIP"
        )
    else:
        text = (
            "Готов к запуску"
            if view.mode.currentText() == "DEMO" or view.presentation.selection_ready
            else "Выберите рынок · раскройте AutoPair"
        )
    fresh = (
        view.presentation.last_quote_at is not None
        and time.monotonic() - (view.presentation.last_quote_at or 0) <= 0.55
    )
    if fresh and view.presentation.last_price is not None and view.presentation.last_price > 0:
        for key in (
            ("TP", "SL")
            if view.presentation.display_position > 0
            else ("DIP",)
            if view.presentation.running
            else ()
        ):
            if key == "DIP" and (
                view.presentation.wait_reason in ("rebound", "cooldown", "warmup")
                or view.presentation.entry_notice
            ):
                continue
            if key not in view.chart.levels:
                continue
            level = Decimal(str(view.chart.levels[key]))
            distance = (
                (
                    (level - view.presentation.last_price)
                    if key == "TP"
                    else (view.presentation.last_price - level)
                )
                / view.presentation.last_price
                * 100
            )
            text += f" · до {key}: {distance:.2f}%" if distance > 0 else f" · {key}: уровень достигнут"
    if view.presentation.display_position > 0 and fresh:
        entry = Decimal(str(view.chart.levels.get("ENTRY", 0)))
        if entry > 0 and view.presentation.last_price is not None:
            text += f" · цена от опорного входа: {(view.presentation.last_price / entry - 1) * 100:+.2f}% (не P&L)"
    view.strategy_status.setText(text)
    view.chart.setAccessibleName("График цены и уровней стратегии")
    view.chart.setAccessibleDescription(
        " · ".join(f"{key}: {view.display_price(value)}" for key, value in view.chart.levels.items())
    )


def update_quote_age(view: Window) -> None:
    view.refresh_trade_details()
    # Pull at UI cadence: no unbounded signal queue while an RPC/receipt blocks the executor.
    monitor = view.worker.execution_monitor
    if (
        monitor is not None
        and view.mode.currentText() == view.worker.mode
        and view.presentation.selection_ready
    ):
        snapshot = monitor.snapshot()
        current_block = view.presentation.market_block
        if (
            snapshot is not None
            and snapshot.pool.lower() == view.pool_input.text().lower()
            and (current_block is None or snapshot.block >= current_block)
        ):
            identity = (id(monitor), snapshot.revision)
            if identity != view.presentation._monitor_revision:
                view.presentation._monitor_revision = identity
                view.on_event(
                    "price_context",
                    {
                        "source": "BSC",
                        "quote": snapshot.quote,
                        "block": snapshot.block,
                        "block_timestamp": snapshot.block_timestamp,
                    },
                )
                view.on_event("price", str(snapshot.price))
                view.presentation.last_quote_at = snapshot.received_at
    view.update_strategy_status()
    view.refresh_currency()
    if view.presentation.last_quote_at is None:
        view.quote_age.setText("Котировок ещё нет")
        set_tone(view.quote_age, "")
        return
    age = max(0, time.monotonic() - (view.presentation.last_quote_at or 0))
    source = {"DEMO": "локальная модель DEMO", "REPLAY": "повтор записанных цен"}.get(
        view.presentation.price_source, "BSC / RPC"
    )
    state = " · нет новых котировок > 0.55 с" if view.presentation.running and age > 0.55 else ""
    if view.chart.usd_rate is not None:
        conversion = f"USD ≈ · курс {view.presentation.display_unit}/USD получен {time.monotonic() - (view.usd.received_at or 0):.0f} с назад (DEX Screener)"
    elif view.presentation.price_source == "BSC":
        conversion = f"{view.presentation.display_unit} · USD недоступен / курс загружается"
    else:
        conversion = view.presentation.display_unit
    if view.presentation.market_block is not None:
        source += f" · блок {view.presentation.market_block}"
        if view.presentation.market_block_timestamp is not None:
            source += f" (возраст {max(0, time.time() - view.presentation.market_block_timestamp):.1f} с)"
    if view.presentation.market_rpc_source != "BSC":
        source += " · резервный RPC"
    if view.presentation.same_block_cache:
        source += " · тот же блок"
    set_tone(view.quote_age, "warning" if age > 0.55 else "")
    view.quote_age.setToolTip(f"1 TARGET в {conversion} · {source}")
    view.quote_age.setText(
        f"1 TARGET в {conversion} · {source} · последняя котировка {age:.1f} с назад{state}"
    )


def refresh_timings(view: Window) -> None:
    if not view.timing_report.isVisible():
        return
    rows = TIMINGS.snapshot()
    view.timing_report.setPlainText(
        "\n".join(
            f"{name}: {row['p50_ms']:.1f} / {row['p95_ms']:.1f} / {row['p99_ms']:.1f} мс"
            f" · {row['count']} вызовов · {row['errors']} ошибок"
            for name, row in sorted(rows.items())
        )
    )


def update_controls(view: Window) -> None:
    idle = not view.presentation.busy and not view.presentation.running and not view.presentation.stop_pending
    position_open = view.presentation.display_position > 0
    for widget in view.editable + view.actions:
        widget.setEnabled(idle)
    if position_open:
        for widget in (view.mode, view.token, view.pool_input, view.router, view.quote, view.candidates):
            widget.setEnabled(False)
        blocked = {
            "AutoPair · найти пулы",
            "CHECK POOL",
            "Выбрать",
            "ADD BASE",
            "Подключить",
            "VERIFY WALLET AND SAVE · Keychain",
            "REMOVE выбранную пользовательскую базу",
        }
        for button in view.actions:
            if button.text() in blocked:
                button.setEnabled(False)
    ready = view.mode.currentText() == "DEMO" or view.presentation.selection_ready
    live_locked = view.mode.currentText() == "LIVE" and view.presentation.locked
    view.start.setEnabled(idle and ready and not live_locked)
    view.buy.setEnabled(idle and ready and not position_open and not live_locked)
    if (
        view.presentation.searching
        and not view.presentation.running
        and not view.presentation.stop_pending
        and not position_open
    ):
        view.token.setEnabled(True)
    view.sell.setEnabled(
        not view.presentation.busy
        and not view.presentation.stop_pending
        and position_open
        and not live_locked
    )
    view.stop.setEnabled(True)
    view.update_strategy_status()
