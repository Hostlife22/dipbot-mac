# Проверка DipBot Mac 0.1

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
