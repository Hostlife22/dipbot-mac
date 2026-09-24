# Продолжение реверса: ограниченный анализ и остатки Sweep

Дата: 2026-09-24. По указанию пользователя работаем только на Mac, без запуска оригинала. Это продолжение [настройки инструментов](RE_TOOLING_RU.md). Изменение Mac: Sweep больше не удаляет позицию до проверки фактического остатка TARGET.

## Завершённый capa/FLOSS проход

Вместо повторного построения графа всего EXE создан Vivisect workspace полного PE с отключённым рекурсивным обходом callees. Исходный бинарник не изменён, адреса/данные сохранены; оригинал не исполнялся. FLOSS использует программную эмуляцию отдельных инструкций, а не запуск EXE под Windows.

Обработаны 12 функций: GUI close/stop/events, CommercialGUI close, AutoPair error, Sweep error, optional Trader.close, REMOVE, Trader.__init__, converter slippage, protected JSON read/write. Оба прохода завершились с exit 0, в отличие от прежних timeout. Результаты: [SCOPED_RE_EVIDENCE.json](SCOPED_RE_EVIDENCE.json).

- capa: одно публичное совпадение `contain loop` в REMOVE; прочие совпадения — внутренние subscope-части правил, которые исключены из списка возможностей. Частичное совпадение такого правила не означает наличие кейлоггера, AES или anti-analysis.
- FLOSS: stack/tight/decoded counts равны нулю на этом наборе. **Прямые code xrefs к точкам входа не восстановлены**, поэтому decoded-проход имеет неполные контексты вызова. Нельзя заключать, что скрытых строк нет.
- Не выполнялся полный рекурсивный анализ остальных функций. Scope в JSON явно ограничен; результаты не заменяют прежний разбор таблиц констант Nuitka.

Инструменты `tools/scoped_workspace.py` и `tools/scoped_re_analysis.py` используют установленные audit-окружения (viv-utils 0.8.1 / vivisect 1.3.2, capa 9.4.0, FLOSS 3.1.1), а не зависимости приложения. Workspace сохранён вне Git: `~/.local/share/dipbot-re-audit/followup/selected.viv`.

Пример из корня репозитория; задайте явный путь вместо `/path/to/original.exe`:

```bash
~/.local/share/uv/tools/flare-capa/bin/python -m tools.scoped_workspace \
  /path/to/original.exe --output /tmp/selected.viv
~/.local/share/uv/tools/flare-capa/bin/python -m tools.scoped_re_analysis \
  capa /tmp/selected.viv /path/to/original.exe \
  --rules "$HOME/.local/share/dipbot-re-audit/capa-rules" --output /tmp/capa-scoped.json
~/.local/share/uv/tools/flare-floss/bin/python -m tools.scoped_re_analysis \
  floss /tmp/selected.viv /path/to/original.exe --output /tmp/floss-scoped.json
```

## Косвенные вызовы Nuitka

В отдельном Ghidra-проекте применены четыре **выведенных вручную**, а не восстановленных из debug symbols прототипа Windows x64:

| VA | Модель аргументов |
| --- | --- |
| `0x142911880` | context, receiver, attribute name → вызов без явных аргументов |
| `0x1429119e0` | context, receiver, attribute name, argument → вызов с одним аргументом |
| `0x1429125c0` | неиспользуемый context, receiver, attribute name → получение атрибута |
| `0x1428fccf0` | context, receiver, attribute name, fallback → getattr с default |

Основание — регистровые аргументы call sites и инструкции помощников. Типы оставлены общими указателями; полного описания PyObject/Nuitka runtime нет. Для последнего helper в native JSON зафиксирован только начальный unwind-фрагмент, не всё тело.

`tools/ghidra/NuitkaPrototypes.java` проверяет SHA-256, задаёт прототипы и повторяет экспорт семи функций. Все exports успешны. Например, GUI.closeEvent теперь явно показывает receiver/name для `_balance_generation`, `_balance_timer`, `_save_ui_state` и stop. Это улучшение читаемости; не все косвенные вызовы разрешены и не все типы доказаны.

Выходные C/ASM и лог находятся в `~/.local/share/dipbot-re-audit/followup/typed-decompiled/` и `ghidra-typed.log`. Только их хеши включены в публичные свидетельства.

## Восстановление и Sweep

Дополнительно разобраны GUI-обвязки Sweep:

- `_wallet_sweep_worker`, `0x140917680`: неблокирующее получение `_manual_lock`; занятый lock даёт error event, иначе создаётся WalletSweepEngine и вызывается run. Просмотрены success/error события и release в путях завершения. Это блокировка конкурентных ручных действий, не durable journal.
- `_finish_wallet_sweep_ui`, `0x1409187e0`: сбрасывает running/блокировку UI, обновляет доступность REMOVE и через singleShot планирует balance/holdings refresh.
- `_handle_wallet_sweep_complete`, `0x140918d60`: проверяет `remaining`; ветка сообщения о нулевых перечисленных балансах связана со сбросом position UI и optional `reconcile_external_flat`. Списки skipped/failed логируются отдельно.

Эти пути дополняют прежний разбор создания/закрытия Trader. Подтверждения отдельного журнала торгового восстановления оригинала не получено. Ни release lock, ни UI refresh, ни список tx_hashes не являются доказательством сохранения операции перед отправкой.

### Найденное расхождение Mac и исправление

Раньше Mac после возврата `swap` безусловно удалял сохранённые позиции этого TARGET. Успешный receipt не гарантирует нулевого баланса: может остаться пыль или измениться баланс токена.

Теперь перед завершением операции читается фактический остаток:

- ноль — позиции TARGET этого кошелька удаляются;
- положительное значение — позиции и entry сохраняются, amount ограничивается остатком и прежним размером позиции;
- ошибка чтения — выбрасывается UncertainTransaction, журнал операции и позиции остаются для сверки, следующие отправки не выполняются.

Это более строгий инвариант Mac; точного совпадения всех Windows-ветвей accounting не заявляем. Обычная продажа с нулевым остатком сохраняет прежнее поведение. Проверка включает синтетический confirmed receipt → ошибка баланса → перезагрузка Store → запрет нового begin.

## AutoPair / PENDING

Повторно рассмотрены `_schedule_autopair` (`0x1409093b0`) и `_autopair_worker` (`0x14090ab20`). В schedule видны остановка предыдущего таймера, смена generation, сброс inflight/result/manual-confirmed, проверка ввода, dirty и запуск таймера. Worker передаёт generation/selection_epoch и время выполнения через события.

Новый важный источник таймера: `_handle_pair_holdings_error` (`0x1409143f0`) очищает inflight, читает и сбрасывает **holdings pending**, затем при наличии отложенного запроса планирует `_request_pair_holdings_refresh`. Это pending-запрос обновления holdings, не статус AutoPair PENDING и не периодический поиск пула.

REMOVE вызывает `_schedule_autopair` и отдельно планирует holdings refresh. Периодическое повторение PENDING в рассмотренных ветвях по-прежнему не подтверждено; новый периодический таймер в Mac не добавлен.

## REMOVE и защищённые настройки

`DynamicPairRegistry.remove` (`0x140bc0c60`) нормализует router/name, входит в lock, находит запись, выполняет pop и `_save`. GUI REMOVE проверяет состояния, обновляет каталог, затем запускает повторный AutoPair/holdings refresh. Статический порядок pop → save не доказывает rollback при отказе диска в Windows.

Добавлены проверки Mac: ошибка чтения баланса или сохранения при REMOVE сохраняет каталог в памяти/на диске и не выдаёт событие успешного удаления. Намеренно сохраняется более строгое поведение Mac.

Повторно прослежен `read_protected_json` (`0x141dce190`): ASCII envelope → format → int(version) против VAULT_VERSION → строгий base64 → unprotect → UTF-8 JSON object; ошибки обобщаются. В этой функции не найдена ветка миграции других версий. Это не доказательство отсутствия всех исторических миграций в других сборках.

Добавлены искусственные проверки отклонения неизвестных/невалидных версий до вызова unprotect. Системный DPAPI-тест **не запускался**: по уточнению пользователя доступен только Mac. Никакие реальные protected-файлы не расшифровывались.

## Воспроизводимость и проверка

[REMAINING_NATIVE_EVIDENCE.json](REMAINING_NATIVE_EVIDENCE.json) воспроизводится `tools.remaining_native`: 12 диапазонов/фрагментов и выбранные публичные константы. Сырые декомпиляции и workspace в Git не включены.

Полный pytest: **271 passed**, 10.85 с, прежнее предупреждение websockets.legacy. После усиления интеграционного сценария confirmed receipt повторно прошли все 9 тестов нового файла. Source smoke, сборка Intel x86_64, Cocoa smoke и codesign verify успешны.

Остаются: полный граф вызовов/типов, весь бинарник за пределами выбранных функций, системный DPAPI в Windows, фактическое восстановление Windows после аварий и runtime-сравнение. Более строгие Mac journal/minOut/uncertain safeguards сохранены. Commit/push в этом проходе не выполнялись; существующий индекс не обновлялся.
