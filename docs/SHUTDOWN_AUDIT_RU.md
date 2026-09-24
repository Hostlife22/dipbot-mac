# Закрытие Trader/GUI и исправление границы анализа

2026-09-24. Статический аудит на Mac, оригинал не запускался. Production-код не менялся.

## Закрытие Trader

В `WalletSweepEngine._close_trader` восстановлено:

1. По 0x142093cc1 получается `getattr(trader, 'close', None)`.
2. По 0x142093d1c проверяется callable.
3. Если callable истинен, close вызывается по 0x142093d9d; иначе метод возвращает None.
4. Ошибка возвращается вызывающему коду: исключение восстанавливается в tstate по 0x142093ee1, RAX обнуляется по 0x142093eff.

Следовательно, это не безусловно тихая очистка. Однако вызывающий код может перехватить или заменить ошибку. Наличие этой обёртки не доказывает, что конкретный Trader реализует close или что close ожидает все вложенные задачи. В текущем списке определений конкретная реализация Trader.close не разрешена.

SELL/BASE вызывают эту обёртку и на нормальных, и на ошибочных путях. Само сообщение об ошибке закрытия нельзя трактовать как доказательство, что транзакция не отправлялась. Нового доказательства Windows durable journal здесь нет.

## GUI и REMOVE

В конструкторе CommercialGUI прослежена связь clicked → connect → bound remove_current_live_pair; вызов connect — 0x1408f7b31. Между этими операциями отдельная Python-обёртка для rollback не установлена. Политика обработки исключения на стороне Qt/глобального exception hook не восстановлена; полного внешнего rollback это не исключает.

CommercialGUI.closeEvent меняет поколения запросов holdings/live-pair, останавливает AutoPair timer и вызывает родительский closeEvent. GUI.closeEvent меняет поколение balance-запросов, останавливает balance timer, вызывает `_save_ui_state` и затем родительский closeEvent. Ошибка сохранения отклоняется в отдельную error-ветвь до обычного вызова родителя.

Этими путями не подтверждено ожидание всех потоков и задач. В Mac политика явная: активные busy/running блокируют закрытие; после запроса quit ожидается worker; при timeout окно не принимает close, предпочтения ещё не записываются.

## Исправление инструмента обхода

Обнаружена ошибка `tools.branch_closure.walk`: после INT3 обход мог продолжаться в соседнюю функцию. Теперь INT3/UD2/HLT завершают обычный поток и попадают в список неразрешённых `trap@address`. То же правило применяется в анализаторе происхождения аргументов. Это не моделирует SEH/обработчик trap и не объявляет trap нормальным возвратом.

Пересчитаны branch_closure, exception_followup, remove_error_paths, storage_sweep_followup, roundtrip_save_audit, shutdown_audit; также обновлены receiver_flow, argument_audit, extended_calls и сопоставление контекстов FLOSS.

- registry_save: **783 вместо 1022** инструкций, trap по 0x140bb9cd8.
- close_trader: **257 вместо первоначально полученных в этом проходе 722**, trap по 0x142093f93.
- gui_close — 370, commercial_close — 372.
- Ранее подтверждённые арифметические границы и 202 инструкции GUI REMOVE error path не изменились.

[Таблица пересчёта](TRAP_BOUNDARY_CORRECTION.json), [свидетельства shutdown](SHUTDOWN_EVIDENCE.json). Старые численные оценки в истории отчётов не следует использовать вместо обновлённых JSON. Отсутствие прямых переходов само по себе никогда не доказывало полноту exception/callee-графа.

## Проверки Mac

`tests/test_close_lifecycle.py` проверяет actual closeEvent с контролируемым worker и реальным временным Store, без Qt event loop и сети:

- busy, running и их сочетание запрещают закрытие до запроса worker quit;
- timeout ожидания запрещает сохранение настроек;
- повторное закрытие после завершения worker сохраняет настройки, не теряя незавершённый журнал.

Это четыре синтетических теста последовательности; не тест фактического Windows shutdown или реального потока Qt.

Полный pytest: **318 passed**, 14.74 с, прежнее предупреждение websockets.legacy. Тесты инструментов: **20 passed**, включая запрет ложного fall-through после трёх видов trap. Production/packaging не менялись; GUI smoke и сборка повторно не запускались. FLOSS runtime/DPAPI/реальные транзакции не запускались.

```sh
python -m tools.shutdown_audit "$EXE" --output docs/SHUTDOWN_EVIDENCE.json
uv run --frozen pytest -q tests/test_close_lifecycle.py
python -m unittest discover -s tools/tests -q
```

Остаются конкретный Trader.close, Qt exception policy, косвенные rollback/хранилища, SEH и завершение вложенных задач. Инициализация Python/Nuitka для FLOSS не восстановлена.
