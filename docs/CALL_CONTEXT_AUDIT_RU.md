# Контексты Nuitka и проверки Mac — 2026-09-24

Оригинал NRNF v1.4.14 не запускался. Анализ привязан к SHA-256 EXE в [свидетельствах](CALL_CONTEXT_EVIDENCE.json). Лицензия, пользовательские кошельки и Keychain не использовались.

## Nuitka, capa и FLOSS

- В Ghidra применены восемь выведенных прототипов вместо четырёх. Добавлены `has_attribute`, `set_attribute`, `call0`, `call1`; семь выбранных функций повторно декомпилированы без ошибок скрипта. Это вывод по инструкциям, а не восстановленные исходные символы.
- `has_attribute` проверяет наличие атрибута, а не истинность его значения; результат 1/0/-1. `set_attribute` преобразует результат нативного setter в bool. Ошибочная трактовка этих помощников меняет смысл ветвей.
- Workspace расширен с 12 до 27 функций: вызывающие обработчики, Sweep, AutoPair, реестр, защищённое чтение/запись и восемь помощников. Обход по-прежнему ограниченный, без рекурсивного анализа всего EXE и без искусственных рёбер вызовов.
- capa завершил все 27 функций. Публичное правило `contain loop` найдено в пяти функциях; это не подтверждение торговой логики или вредоносности.
- FLOSS завершил восемь помощников и вернул **361 объект контекста**: 21/27/245/2/2/55/8/1 соответственно для method_call0, method_call1, get_attribute, getattr_default, has_attribute, set_attribute, call0, call1. Строк stack/tight/decoded — 0.
- В логе **74 сообщения об ошибке восстановления PC hook**. Полученные контексты не доказывают корректное состояние PyObject/интерпретатора; нулевой результат декодирования не доказывает отсутствия строк. Таймаута в этом проходе нет.

[Карта обращений](DISPATCH_GRAPH_EVIDENCE.json): 48 диапазонов, 1164 прямых обращения к выбранным помощникам, 922 связи с именем атрибута. Сканер отслеживает R8 только внутри локальной последовательности, сбрасывая его на вызовах, переходах и известных целях прямых переходов. Полного межпроцедурного анализа, разрешения косвенных целей и идентификации runtime receiver нет. Диапазоны могут не включать вынесенные ветви; отсутствие имени в карте не доказывает отсутствие поведения.

## Поток, AutoPair и сохранение

Дополнительно разобраны GUI.start_bot (0x140f54250), GUI._run_bot_thread (0x140f55f70), CommercialGUI.__init__ (0x1408f5f80).

- В start_bot прослежены подготовка target, получение Thread и `_run_bot_thread`, сохранение `bot_thread`, вызов start. Параметры конструктора включают target/daemon; значение daemon этим отчётом не устанавливается.
- В `_run_bot_thread` получение asyncio.run и bot.start заканчивается передачей результата start в call1 по 0x140f561f6: соответствует `asyncio.run(bot.start())`. В ветви ошибки присутствуют сообщение `Bot thread fatal error`, событие state/stopped и stopped_with_reason. Эти UI-события не являются доказательством durable journal.
- В конструкторе CommercialGUI связаны `token.textChanged.connect(_schedule_autopair)` (call1 по 0x1408f8a1b) и timeout таймера с `_start_autopair_resolution` (call1 по 0x1408f8878). Подтверждается событийная цепочка ранее найденного singleShot debounce; периодический опрос PENDING всё ещё не доказан.
- Карта включает read/write protected JSON, close/error и Trader init. Оставшиеся косвенные обращения к хранилищам, завершение вложенных задач и все пути исключений не разрешены. Новых подтверждённых версий защищённого формата или миграций в этом проходе нет.

## Сценарии Mac

В `tests/test_expanded_scenarios.py` добавлены 12 проверок на искусственных данных:

- ошибка preflight первого токена не мешает второму; STOP после первой продажи сохраняет второй;
- неопределённая отправка первого токена блокирует дальнейшие отправки;
- сбой финальной записи после первой подтверждённой операции сохраняет блокировку и запись для сверки после перезапуска, второй токен не отправляется;
- отдельный процесс завершается непосредственно до/после atomic replace финального учёта: на диске либо старая позиция с confirmed operation, либо новый учёт с history. Это проверка смерти процесса, не отключения питания;
- неизвестная версия, NaN/Infinity и отрицательные суммы не перезаписывают корректные настройки; обрезанный JSON не перезаписывается при неудачном старте.

Также повторно проходят существующие сценарии смены маршрута и остатка (`test_phase3.py`), ошибок balance/save при REMOVE (`test_remaining_branches.py`). Это проверка инвариантов Mac; эквивалентность Windows не установлена.

Read-only BSC: пять кандидатов, канонические V2/V3, BUY/SELL quotes; **15 чтений factory/token0/token1 через Multicall совпали с отдельными запросами на блоке 123774184**. Котировки делались отдельно и не привязаны к этому блоку. Только eth_chainId, eth_getBlockByNumber, eth_getCode, eth_call; **0 транзакций**. [Результат](CONTEXT_RPC_EVIDENCE.json).

## Воспроизведение

Указать путь к reference EXE в EXE, а OUT — отдельный каталог вне Git. Подготовка и capa используют Python окружения flare-capa, FLOSS — окружения flare-floss (версии из RE_TOOLING_RU.md).

```bash
python -m tools.context_workspace "$EXE" --output "$OUT/expanded.viv"
python -m tools.context_re_analysis capa "$EXE" --root "$OUT" --rules "$RULES"
python -m tools.context_re_analysis floss "$EXE" --root "$OUT" --rules "$RULES"
python -m tools.dispatch_graph "$EXE" --output "$OUT/dispatch.json"
uv run --frozen pytest -q
QT_QPA_PLATFORM=offscreen uv run --frozen python launcher.py --smoke-test
```

Для dispatch нужны Capstone/pefile. Java-скрипт NuitkaPrototypes запускается после импорта через Ghidra headless с `-noanalysis -postScript NuitkaPrototypes.java "$OUT/typed-decompiled"`; подробности окружения в tools/ghidra/README.md. Сырые PE/workspace/decompilation/logs остаются вне Git.

Полный pytest: **283 passed**, 12.35 с; source offscreen smoke: exit 0. Python-инструменты компилируются; карта обращений повторно воспроизведена из EXE. Production/packaging в этом проходе не менялись, bundle не пересобирался.

## Что остаётся

Нужны дальнейшее разрешение receivers/косвенных callee и моделирование Nuitka/Python runtime для полезного декодирования FLOSS. Статика не подтверждает реальное поведение Windows при авариях и сетевых сбоях; системный DPAPI на Mac не проверен. Полное совпадение, периодический PENDING и все ветви завершения/хранилищ остаются неподтверждёнными. Защиты Mac не ослаблены.
