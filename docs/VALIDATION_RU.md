# Проверка DipBot Mac 0.1

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
