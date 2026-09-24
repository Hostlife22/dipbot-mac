# PAPER и пакет macOS — текущая проверка

Исправлен повторный START после STOP без смены рынка. Полный pytest:
355 passed; инструменты анализа: 30 passed, 4 subtests passed.
[Отдельный отчёт PAPER, готового приложения и аварийных сценариев](PAPER_PACKAGED_ACCEPTANCE_RU.md).

# Последняя прикладная проверка — 24 сентября 2026

Выполнены ограниченные LIVE-тесты V2/V3, Converter и Sweep, а также PAPER,
проверка профиля через реальный RPC и сборка macOS. Найден и исправлен
учёт позиции при продаже через базовый конвертер. 350 offline-тестов прошли.
[Отчёт LIVE-проверки и ограничения](LIVE_ACCEPTANCE_RU.md).
Ниже сохранена история прежних проверок; их отметки о невыполненном LIVE
относятся к соответствующим прошлым проходам.

# Проверка DipBot Mac 0.1

## Повторный FLOSS и namespace Trader, 2026-09-24

- Три завершённых ограниченных прохода FLOSS: baseline, исправление MOV, наблюдение memcpy. Каждый: **426 контекстов, 8 ошибок возврата, 0 строк**; исправление MOV не сработало ни разу в этом корпусе.
- Ошибки локализованы на трёх memcpy call-sites с неподготовленными адресами/размерами и стеком.
- Восстановлены 50 явных записей namespace Trader и пустой tuple баз; close не определён среди этих записей.
- Добавлена поддержка двух/трёхаргументных method vectors; **30 тестов инструментов passed**. Python compile и diff check прошли.
- [Отчёт и ограничения](FLOSS_RETURN_TRADER_AUDIT_RU.md). Production не менялся; последний pytest приложения — 336 passed. Оригинал/реальные RPC не запускались, коммит/push не выполнялись.

## Lookup и Envi, 2026-09-24

- **29 тестов инструментов**, **3 теста Envi** прошли; 20 сценариев эмуляции: 15 ожидаемых возвратов и 5 остановок на неподготовленном fallback.
- Распознаны 174 глобальных getter; 226/296 старых контекстов FLOSS имеют статический путь. Полный string-decoding не выполнялся.
- Исправление точной инструкции MOV imm32→RAX проверено синтетическим кодом на Intel Mac; оригинал не запускался. Связь с прежними ошибками возврата FLOSS не доказана.
- [Отчёт](GLOBAL_LOOKUP_AUDIT_RU.md). Production не менялся. Последний полный pytest приложения: 336 passed; повторного запуска приложения/GUI/сборки не было. Коммит/push не выполнялись.

## Расширенный unwind и конвертер, 2026-09-24

- Полный pytest: **336 passed**, 17.23 с; прежнее предупреждение websockets.legacy. Тесты инструментов: **25 passed**.
- Добавлены 10 проверок V3 swap/unwrap и 3 проверки сохранности повреждённого реестра при catalog/ADD/REMOVE.
- Статический индекс: 231 тело из карт фабрик, 301 unwind-диапазон; назначение cookie-handler проверено отдельно. Динамический граф не объявляется полным.
- FLOSS: повторное сопоставление 296 существующих контекстов, 192 статических пути; новой эмуляции и новых строк нет.
- [Новые доказательства и оставшиеся задачи](UNWIND_CONVERTER_AUDIT_RU.md). Production не менялся; GUI/сборка не повторялись. Оригинал, DPAPI, RPC и реальные транзакции не запускались.
- Python compile и `git diff HEAD --check` прошли. Коммит/push не выполнялись; индекс Git не менялся инструментами этого прохода.

## Ошибки после receipt и Sweep, 2026-09-24

- Полный pytest: **323 passed**, 15.82 с; прежнее предупреждение websockets.legacy.
- Пять новых сценариев: receipt status 0/1 × file/directory fsync failure и callback error после сохранения confirmed. Ни одной реальной отправки.
- Подтверждена Windows-ветвь Exception → failed/continuing после _sell_target, в том числе возможность ошибки при close после receipt.
- [Отчёт](POST_SEND_AUDIT_RU.md). Production не менялся, GUI/сборка не повторялись. Оригинал, системный DPAPI и FLOSS runtime не запускались.
- Существующие инструменты не менялись; последний прогон их тестов — 20 passed.


## Shutdown и границы trap, 2026-09-24

- Полный pytest: **318 passed**, 14.74 с; инструменты: **20 passed**.
- Четыре проверки Mac closeEvent: активная операция, worker wait timeout и безопасное сохранение после его завершения.
- Исправлен ложный fall-through после INT3/UD2/HLT; затронутые native-отчёты пересчитаны. registry_save: 783 вместо 1022 инструкций.
- Уточнены optional close/callable/error propagation и сигнал GUI REMOVE; это не доказательство полного rollback или join вложенных задач.
- [Отчёт и ограничения](SHUTDOWN_AUDIT_RU.md). Production не менялся; оригинал, FLOSS runtime, RPC и транзакции не запускались.


## Round-trip floor и ошибка GUI REMOVE, 2026-09-24

- Полный pytest: **314 passed**, 13.01 с; прежнее предупреждение websockets.legacy. Инструменты: **19 passed**.
- Подтверждены integer subtraction/multiplication/floor-division и sentinel/clamping функции round-trip loss.
- Добавлены 10 сравнительных/граничных сценариев, включая дробные bps и одну raw unit при 10^30; Mac сохраняет точный лимит 15%.
- Добавлен call_vector2; прослежены аргументы save и 202 инструкции прямого GUI error path, не достигающие обычного refresh/reschedule.
- [Отчёт](ROUNDTRIP_SAVE_RU.md). Полный rollback и Python runtime не восстановлены. Production не менялся; GUI/сборка/FLOSS не перезапускались.


## Единый аудит и границы commit файла, 2026-09-24

- Полный pytest: **304 passed**, 12.74 с; прежнее предупреждение websockets.legacy. Инструменты: **18 passed**.
- Исправлен rollback публичного реестра/настроек после уже выполненного replace: память сохраняет новое видимое состояние, ошибка durability не подавляется.
- 13 новых сценариев: ADD/REMOVE/preferences × file_sync/replace/dir_open/dir_sync и безопасное сообщение ошибки.
- Source offscreen GUI smoke: exit 0. Packaging не менялся, bundle не пересобирался.
- [Матрица всех 12 областей](COMPREHENSIVE_AUDIT_RU.md), [различия защит](MAC_PROTECTION_DIFFERENCES_RU.md).
- Полный Windows-паритет, косвенный rollback и Python runtime для FLOSS не подтверждены. Оригинал/RPC/транзакции/Windows DPAPI не запускались.


## Sweep и защищённая запись, 2026-09-24

- 18 тестов инструментов прошли; добавлена проверка keyword-values из константного tuple и неверного смещения.
- Подтверждены <= 0 в проверках TARGET balance, BASE balance и TARGET quote.
- Уточнена цепочка временного файла/write/flush/fsync/replace и cleanup unlink; это не доказательство rollback реестра в памяти.
- Восстановлены parents/exist_ok/missing_ok и kwargs JSON-сериализации.
- Production не менялся; приложение/сборка/FLOSS не перезапускались. Оригинал, RPC и транзакции не запускались.

[Отчёт и ограничения](STORAGE_SWEEP_FOLLOWUP_RU.md).


## Общий поток констант и порог Sweep, 2026-09-24

- 17 тестов инструментов прошли; добавлены проверки границ и поиска потока PE-ресурса.
- Слот 0x1429a02b0 подтверждён как int 0; известный финальный баланс <= 0 пропускает remaining.
- Прослежены 476 инструкций после ошибки REMOVE и пути init/_save; полный косвенный rollback не доказан.
- Найден загрузчик общей таблицы и восемь вызовов PyDict_New; полноценная инициализация эмулятора не выполнена.
- Production не менялся; приложение/сборка/FLOSS повторно не запускались. Оригинал и RPC не запускались.

[Отчёт и ограничения](SHARED_CONSTANTS_RU.md).


## Специальные методы и внешние ошибки, 2026-09-24

- 14 тестов инструментов прошли; добавлена проверка type-based lookup и literal tuple None.
- Получатели `_lock.__enter__/__exit__` и два tuple-вызова разрешены на статическом пути REMOVE.
- Уточнены обработка неуспешного reload в GUI и событийный повтор AutoPair из live-pair result.
- Сопоставлены 296 прежних контекстов FLOSS: 190 имеют статический путь к получателю, 106 — нет. Новых эмуляций и восстановленных runtime-объектов нет.
- Production не менялся; тесты приложения/сборка повторно не запускались. Оригинал, RPC и транзакции не запускались.

[Отчёт и оставшиеся ограничения](EXCEPTION_FOLLOWUP_RU.md).


## Аргументы и частичная модель Python, 2026-09-24

- Полный pytest: **291 passed**, 12.56 с; прежнее предупреждение websockets.legacy. Тесты инструментов: **13 passed**.
- Прослежены статические пути создания Trader и DipBot; восстановлены имена keyword-аргументов и часть значений. Полного динамического графа нет.
- Уточнение Sweep: неизвестный финальный баланс добавляет метку в remaining и сообщение в failed оригинала. Пустой remaining при unknown в Mac-тесте не является доказанным сценарием оригинала.
- Подтверждён событийный повтор AutoPair после REMOVE и локальная ошибка SecureStoreError при отсутствии файла настроек.
- Модель 1220 известных ASCII-имён патчит 1975 слотов в эмуляторе. В 245 контекстах get_attribute заголовки имени доступны; receiver по-прежнему недоступен. Новые строки не извлечены, полноценная инициализация Python не восстановлена.
- Production и packaging в этом проходе не изменены; оригинал, RPC, транзакции и системный DPAPI не запускались.

[Отчёт, свидетельства и воспроизведение](STATE_ARGUMENT_AUDIT_RU.md). Ниже — история предыдущих проходов.


## Вынесенные ветви и глобальные вызовы, 2026-09-24

- Полный pytest: **291 passed**, 12.93 с; прежнее предупреждение websockets.legacy.
- **10 тестов инструментов** прошли в отдельном Capstone-окружении; Python-инструменты компилируются.
- Закрыты прямые цели из пяти прежних ограниченных диапазонов: 6525 дополнительных инструкций. Обход не включает exception-table edges и callee. Также разобраны три пути загрузки настроек.
- Четыре дополнительных операции lookup/call, 62 обращения, 33 inline-конструкции функций. reload_runtime_settings связан с телом 0x141dc0230.
- Уточнено условие Sweep: ложное remaining ведёт к очистке UI и опциональному callable reconcile_external_flat. Новый Mac-тест сохраняет позицию при пустом remaining, но неизвестном балансе.
- FLOSS: проверены 245 контекстов get_attribute и 51 call1; receiver headers недоступны во всех. Объекты Python не восстановлены; прежние 426/8/0 не заменяются утверждением об успехе.
- Production/packaging не изменены, GUI и сборка повторно не запускались. EXE, реальные RPC/транзакции и системный DPAPI не запускались.

[Отчёт, ограничения и команды](BRANCH_GLOBAL_AUDIT_RU.md). Ниже — результаты предыдущих проходов.


## Получатели Nuitka и изоляция контекстов FLOSS, 2026-09-24

- Полный pytest: **290 passed**, 12.29 с; прежнее предупреждение websockets.legacy. Source offscreen smoke: exit 0.
- Шесть отдельных тестов анализатора на синтетическом машинном коде прошли в окружении Capstone.
- 59 native-диапазонов, 575 цепочек доступа, 198 определений фабрик и 91 кандидат связи с методом. Runtime-dispatch и вынесенные ветви не полностью разрешены.
- FLOSS со свежим исходным snapshot на caller: 426 контекстов вместо 361, 8 сообщений hook restore failure вместо 74. Строк нет; валидное Python-состояние не восстановлено.
- Семь новых Mac-сценариев: изоляция V2/V3 при REMOVE, rollback реестра, повреждённые расшифрованные данные и неизменность источника импорта настроек.
- Production/packaging не изменены; bundle не пересобирался. Оригинал, RPC, реальные транзакции и системный DPAPI не запускались.

[Отчёт и команды](RECEIVER_AUDIT_RU.md), [цепочки](RECEIVER_FLOW_EVIDENCE.json), [FLOSS](FLOSS_RECEIVER_EVIDENCE.json). Записи ниже — результаты предыдущих проходов.


## Расширенные контексты Nuitka и Mac-проверки, 2026-09-24

- Полный pytest: **283 passed**, 12.35 с; прежнее предупреждение websockets.legacy. Новый файл: 12 сценариев Sweep, сохранения, process death и повреждённых настроек.
- Source offscreen GUI smoke: exit 0. В этом проходе production/packaging не менялись; bundle повторно не собирался.
- Ghidra: восемь выведенных прототипов, семь успешных exports, без ошибок скрипта.
- capa: завершены 27 функций. FLOSS: восемь помощников, 361 объект контекста, 0 извлечённых строк, 74 hook restore failures. Таймаута нет; runtime-контексты остаются неполными.
- Карта 48 диапазонов: 1164 вызова помощников, 922 имени атрибутов; точного разрешения receivers нет.
- Read-only BSC: пять кандидатов, V2/V3 quotes, 15 Multicall metadata reads совпали с direct RPC на одном блоке; 0 транзакций.
- EXE не запускался. Windows crash/runtime и системный DPAPI не проверены. Защиты Mac сохранены.

[Подробный отчёт](CALL_CONTEXT_AUDIT_RU.md), [инструментальные результаты](CALL_CONTEXT_EVIDENCE.json), [RPC](CONTEXT_RPC_EVIDENCE.json). Нижние записи относятся к предыдущим проходам.


## Scoped follow-up и остатки Sweep, 2026-09-24

- Полный pytest: **271 passed**, 10.85 с; после усиления одного сценария повторно прошли 9 тестов нового файла.
- Source smoke, сборка Intel x86_64, Cocoa smoke и codesign verify: exit 0.
- capa/FLOSS: завершены 12 функций в non-recursive workspace; decoded caller contexts не восстановлены, полного покрытия нет.
- Ghidra: четыре выведенных прототипа, семь успешных повторных exports.
- Исправлено удаление позиции при ненулевом/неизвестном остатке после Sweep.
- Пользователь подтвердил Mac-only; Windows/DPAPI/runtime оригинала не запускались, реальных RPC/транзакций нет.

[Отчёт](SCOPED_RE_AUDIT_RU.md), [native evidence](REMAINING_NATIVE_EVIDENCE.json), [tool evidence](SCOPED_RE_EVIDENCE.json).


## Инструменты реверса, 2026-09-24

- capa 9.4.0 / FLOSS 3.1.1 установлены в изолированные uv tool окружения. Полная Ghidra 12.0.3 запускается с JDK 21.0.10.
- FLOSS static: exit 0; capa file-only API: завершён. CLI pefile: NotImplementedError; full capa и FLOSS decoded: timeout 420 с.
- Ghidra auto-analysis: timeout 180 с. Повторный адресный проход: семь успешных C/ASM exports, без script errors.
- Сверка адресов инструкций Ghidra/Capstone воспроизводится; только четыре завершающих INT3 различаются в converter.
- Python-инструменты компилируются; Java-скрипт выполнен полной Ghidra. Production/packaging не менялись, тесты приложения и сборка повторно не запускались.
- Windows EXE не исполнялся; RPC/кошельки/DPAPI не использовались.

[Отчёт](RE_TOOLING_RU.md), [публичные свидетельства](RE_TOOLING_EVIDENCE.json).


## Crash/runtime follow-up, 2026-09-24

- `uv run --frozen pytest -q`: **262 passed**, 12.06 с; прежнее предупреждение websockets.legacy.
- Три SIGKILL-точки atomic write, три ошибки файловой записи, две проверки restart/reconcile и сравнительные minOut-векторы прошли.
- Source offscreen smoke: exit 0. Production/packaging не изменены; повторная сборка не требовалась.
- Подготовлен Windows-only DPAPI probe; системная проверка на Mac **не выполнялась**.
- Статический разбор семи close/stop/event/error диапазонов без исполнения EXE. Реальных RPC/транзакций в этом проходе нет.

[Отчёт и команды](CRASH_RUNTIME_AUDIT_RU.md). Результаты ниже относятся к предыдущим проходам.


## Полный статический проход, 2026-09-24

- `uv run --frozen pytest -q`: **245 passed**, 7.84 с; прежнее предупреждение websockets.legacy.
- Source offscreen smoke, `./BUILD_MAC.command`, Cocoa smoke собранного Intel x86_64 приложения: exit 0.
- `codesign --verify --deep --strict`: exit 0.
- Реальный read-only BSC probe: пять кандидатов, V2/V3 canonical pools, Multicall, BUY/SELL quotes, CATALOG_TOKEN WBNB. Отправлено **0 транзакций**.
- Первые два dataseed endpoint отказали в соединении; успешен bsc.nodereal.io. Discovery block 123767739; дальнейшие quotes не закреплены за этим блоком.
- `tools.full_static_audit.report`: JSON повторно воспроизведён из EXE (с нормализацией tuple/list при JSON-сериализации).
- `git diff HEAD --check`: без ошибок. Commit/push в этом проходе не выполнялись.
- Windows EXE не запускался; реальные пользовательские настройки, лицензия и Keychain не использовались.

[Изменения, свидетельства и ограничения](FULL_STATIC_AUDIT_RU.md), [RPC-результат](READ_ONLY_RPC_EVIDENCE.json). Ниже сохранены исторические результаты; их ограничения по RPC относятся к соответствующим проходам.


## Nonce/receipt, 2026-09-24

- `uv run --frozen pytest -q`: **226 passed**, 7.04 с; одно прежнее предупреждение websockets.legacy.
- Source offscreen smoke: exit 0.
- `./BUILD_MAC.command`: exit 0; Cocoa smoke собранного приложения: exit 0.
- `codesign --verify --deep --strict`: exit 0.
- `tools.nonce_native.report`: шесть диапазонов/выбранные константы повторно воспроизводят NONCE_EVIDENCE.json.
- `git diff HEAD --check`: без ошибок. Commit/push не выполнялись, существующий индекс сохранён.

[Выводы и ограничения](NONCE_RECOVERY_RU.md). Windows EXE не запускался; RPC, подписи и receipts в тестах — локальные синтетические сценарии. Реальные транзакции не отправлялись.


## Sweep internals, 2026-09-24

- `uv run --frozen pytest -q`: **205 passed**, 6.51 с, одно прежнее предупреждение websockets.legacy.
- Source offscreen smoke: exit 0.
- `./BUILD_MAC.command`: exit 0; Cocoa smoke собранного приложения: exit 0.
- `codesign --verify --deep --strict`: exit 0.
- `tools.sweep_native.report`: пять диапазонов/выбранные константы повторно воспроизводят SWEEP_EVIDENCE.json.
- `git diff --check`: без ошибок. Существующее содержимое индекса сохранено; новых commit/push в этом проходе нет.

[Разбор и изменения](SWEEP_INTERNALS_RU.md). Все сценарии offline, Windows EXE/реальные RPC/пользовательские данные не использовались. Симуляция SELL проверена на подставном контракте и не подтверждает LIVE parity.


## Follow-up после 2a3bb6b, 2026-09-24

- `uv run --frozen pytest -q`: **196 passed**, 6.33 с, одно прежнее предупреждение websockets.legacy.
- Source offscreen smoke и временный GUI-сценарий REMOVE → single-shot 220 мс: exit 0, без RPC.
- `./BUILD_MAC.command`: exit 0; Cocoa smoke собранного приложения: exit 0.
- `codesign --verify --deep --strict`: exit 0.
- `tools.followup_native.report` повторно воспроизводит FOLLOWUP_EVIDENCE.json.
- `git diff --check`: без ошибок. Коммит 2a3bb6b содержит предыдущий проход; изменения этого follow-up оставлены в рабочем дереве. Push не выполнялся.

[Новые выводы и остающиеся UNKNOWN](FOLLOWUP_PARITY_RU.md). Windows/DPAPI/runtime не запускались; envelope проверен только на синтетических callbacks, без доступа к пользовательским данным.


## Проход workflow, 2026-09-24 (база 74ca2a9)

- `uv run --frozen pytest -q`: **182 passed**, 6.60 с; одно прежнее предупреждение websockets.legacy.
- Source offscreen smoke: exit 0.
- `./BUILD_MAC.command`: exit 0, Intel x86_64 `.app` собрана.
- Built smoke через Cocoa (`env -u QT_QPA_PLATFORM … --smoke-test`): exit 0.
- `codesign --verify --deep --strict 'dist/DipBot Mac.app'`: exit 0.
- `tools.workflow_native`: 16 диапазонов и выбранные публичные константы повторно воспроизведены. Хеши текущих исходников добавлены в `WORKFLOW_EVIDENCE.json` отдельно от EXE-свидетельств.
- Отдельный GUI-сценарий на временном Store: суммы V2/WBNB и V3/USDT восстанавливаются при переключении и новом запуске; устаревший discovery-результат игнорируется.
- `git diff HEAD --check`: без ошибок в итоговых файлах. Существующий индекс Git не обновлялся; commit/push не выполнялись.

Оригинал не запускался, Windows-версия/активация и Windows UI не проверялись. Реальные RPC/транзакции, пользовательские файлы и Keychain не использовались. Четыре subprocess-crash проверки используют подставной транспорт. [Изменения, доказательства и ограничения](WORKFLOW_PARITY_RU.md).

Ниже сохранены результаты прежних проходов.


Дата: 2026-09-24. Актуальный результат: [аудит по ТЗ](PARITY_AUDIT_RU.md), [формальная модель](STRATEGY_SPEC_RU.md).

Повторный аудит: [выводы и адреса кода](NATIVE_AUDIT_RU.md). После изменений заново собрана `dist/DipBot Mac.app`; нативный Cocoa smoke test и `codesign --verify --deep --strict` завершились с кодом 0. Дополнительно проверены граница gap, плоская цена, перенос базы до/после DIP, snapshot minOut через approve, цена после BUY receipt, сохранение позиции при сбое чтения и граница round-trip конвертера. Первоначальные read-only RPC-проверки ниже не повторялись и не являются LIVE-проверкой изменений.

## Среда

- macOS 26.6.2 (25G83), процесс x86_64.
- Python 3.12.3; PySide6 6.11.2; web3.py 7.16.0.
- PyInstaller 6.22.3. Точные зависимости закреплены в `uv.lock`.
- Готовая сборка: `dist/DipBot Mac.app`, x86_64, ad-hoc подпись.
- Windows-сборка NRNF v1.4.14 не запускалась; её SHA-256 после работы совпадает с исходным отчётом.

## Пройдено

1. **101 локальный тест после выполнения TZ.md**, сеть запрещена внутри тестов:
   - DIP, TP, SL, сброс после разрыва котировок, запрет повторного входа после SL;
   - точные целочисленные суммы/minOut, разные decimals, V2/V3 ориентация цены;
   - отказ для чужой factory, поддельного адреса пула и чужого target;
   - ABI V3: порядок параметров QuoterV2 и наличие deadline в SwapRouter;
   - точечный approve с предварительным сбросом ненулевого allowance;
   - minOut не ослабляется после ожидания approve;
   - BUY/SELL V2/V3: правильные направления, recipient и фактический баланс;
   - запись hash до broadcast, timeout с перезапуском хранилища, запрет повторной отправки;
   - revert, pending nonce, лимит газа, сверка неизвестного receipt;
   - отсутствие автоматического снятия блокировки после успешного receipt;
   - единый V2/V3 путь конвертера, native funding, ABI exactInput, reverse fees и deadline 60 с;
   - безопасная альтернатива fee tier, порядок 42 статических converter profiles;
   - сохранение latch при save failure, STOP до/во время подготовки BUY и после receipt, отмена queued BUY;
   - DEMO-цикл, запрет двойной позиции, защита от смены target/подключения;
   - SELL ограничен учтённым количеством, очистка позиции после продажи;
   - редактирование потенциально чувствительных текстов ошибок.
2. **GUI из исходников**: запуск, DEMO START, виртуальный BUY, STOP LOSS / STOP, закрытие. [Снимок](SCREENSHOT_DEMO.png) содержит только синтетические данные.
3. **Готовая `.app`**: нативный запуск Cocoa через `--smoke-test`, загрузка каталога, локальное подписание и восстановление адреса временного случайного ключа, создание окна и корректное закрытие. Ни provider, ни broadcast в этом тесте не создаются. Keychain backend создаётся, но чтение/запись Keychain не выполняется.
4. **Подпись пакета**: `codesign --verify --deep --strict`.
5. **Read-only BSC mainnet** через публичный `https://bsc-dataseed.binance.org`:
   - chainId 56, свежий блок; первый зафиксированный блок 123729001;
   - канонический V2 USDT/WBNB `0x16b9a82891338f9bA80E2D6970FddA79D1eb0daE`;
   - V3 USDT/WBNB fee tiers 100, 500, 2500, 10000;
   - резервы V2, liquidity/slot0 V3, котировки 0.001 WBNB → USDT;
   - factory официальных V2 и V3 router совпала с ожидаемыми адресами.

Во время проверки самостоятельной сборки обнаружено отсутствие metadata `py_ecc`; исправлено включением dependency metadata и backend-модулей в `BUILD_MAC.command`. Повторный запуск `.app` выполнен успешно.

## Не проверено и не подтверждено

- Реальные approve, BUY/SELL, wrap/unwrap, Converter и Sweep: **ни одной транзакции не отправлено**. Для такой проверки нужны явное разрешение и выделенный тестовый кошелёк.
- Запись реального private key или RPC в пользовательский Keychain. Проверен импорт/создание backend; реальный доступ может потребовать системного подтверждения macOS.
- Полная эквивалентность торговых сигналов оригиналу, фактическая скорость 100/103 мс. `2 down moves` восстановлено статически и покрыто синтетическими тестами.
- Все 42 актива, honeypot/tax/blacklist, неоднородные RPC, реорганизации цепочки и длительная работа.
- Нативная Apple Silicon сборка, другие версии macOS, Developer ID и нотарификация Apple.
- Активация Windows-приложения, pair grid и AutoPair в оригинальном UI не проверялись; лицензия не открывалась.

Локальные mock-тесты проверяют код и инварианты, но не заменяют тестирование LIVE на небольших суммах и аудит торгового исполнения. Эту сборку нельзя считать подтверждённой для эксплуатации с существенными средствами.


## Дополнительные read-only проверки по TZ.md

- Пять V2 price reads на публичном BSC RPC: медиана 0.4009 с, интервалы наблюдений 0.5017–0.5076 с при паузе 0.1 с. Это не Windows timing comparison.
- Новый quoteExactInput для полного V3 WBNB→USDT→ETH с fees 100/500 и обратного пути выполнен на публичном RPC: input 1000000000000000 raw WBNB, output 289921091876462 raw ETH, reverse 998786887420339 raw WBNB. Адреса и полный результат в PARITY_EVIDENCE.json. Подписания и broadcast не было.
- Это проверка чтения контрактного интерфейса, не исполнения swap; LIVE, reorg и Windows runtime parity не подтверждены.

Итог этой итерации: 101 passed, 0 failed; одно предупреждение websockets.legacy. Source GUI smoke, BUILD_MAC.command, нативный Cocoa smoke новой .app, codesign --verify --deep --strict и git diff --check завершились с кодом 0. Архитектура сборки x86_64; commit/push не выполнялись.

## Дополнительный статический аудит — 2026-09-24, после f1a69b3

Оригинал не запускался. Выполнено:

- `uv run --frozen pytest -q`: **117 passed**, 1 прежнее предупреждение websockets.legacy.
- Source GUI smoke с `QT_QPA_PLATFORM=offscreen`: exit 0.
- Два последовательных Window с временным Store: DIP/GAS/interval восстановлены; второй запуск DEMO, без chain/live; Keychain и RPC не использовались.
- `./BUILD_MAC.command`: exit 0, новый `dist/DipBot Mac.app`, x86_64, ad-hoc подпись.
- Cocoa smoke готового `Contents/MacOS/DipBot Mac --smoke-test`: exit 0.
- `codesign --verify --deep --strict 'dist/DipBot Mac.app'`: exit 0.
- `tools.recovery_native.inspect`: восемь диапазонов и выбранные константы точно воспроизводят JSON после нормализации tuple/list при сериализации.
- `git diff --check`: без ошибок. Commit/push не выполнялись в этом проходе.

Результаты, исправления и оставшиеся различия: [STATIC_RECOVERY_AUDIT_RU.md](STATIC_RECOVERY_AUDIT_RU.md). Эти проверки не подтверждают Windows runtime parity или LIVE-торговлю.

## AutoPair / dynamic registry — 2026-09-24, после 8228f0a

- `uv run --frozen pytest -q`: **156 passed**, 1 прежнее предупреждение websockets.legacy.
- Source GUI smoke: exit 0; отдельный тест интерфейса с временным Store и подставной chain подтвердил PENDING → RESOLVED и установку выбранного pool address.
- `./BUILD_MAC.command`: exit 0, bundle пересобран после последнего изменения исходников.
- Cocoa smoke готового bundle: exit 0.
- `codesign --verify --deep --strict`: exit 0.
- Десять native-диапазонов и выбранные константы воспроизведены по hash-locked EXE; `git diff --check` без ошибок.
- Все RPC-ответы в новых тестах подставные. Оригинал, реальные транзакции, пользовательские данные и Keychain не использовались.

Scope и оставшиеся ограничения: [AUTOPAIR_PARITY_RU.md](AUTOPAIR_PARITY_RU.md). Runtime parity и LIVE не подтверждены.
