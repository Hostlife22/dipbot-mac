# Аудит соответствия по TZ.md

Обновление 2026-09-24 после `f1a69b3`: см. [дополнительный статический аудит](STATIC_RECOVERY_AUDIT_RU.md). P12/P13/P18 частично разобраны, найдены подтверждённые отличия и исправлены дополнительные Mac-ошибки. Таблица и счётчики ниже относятся к предыдущему проходу; не являются итогом нового.

Дата: 2026-09-24. Baseline: `4f3e986`, чистое рабочее дерево, 70 тестов и GUI smoke успешны. Исследован тот же EXE SHA-256 `0f9da36f8a9f0b9908d69e00a7c7c3501efb8d9823246078063267dababd0f72`. Среда: macOS 26.6.2 (25G83), x86_64, Python 3.12.3, radare2/r2ghidra 6.1.0. Windows-оригинал не исполнялся; лицензия и пользовательские кошельки не читались. Ни одной транзакции во внешнюю сеть не отправлено.

## Результат и границы

Исправлено **6 расхождений/ошибок**: две критичные ошибки управления STOP и журналом, две существенные ошибки конвертера его deadline и потерянные предпочтения маршрутов. **6 отличий сохранены намеренно**, **4 области остались UNKNOWN/BLOCKED**, **3 ограниченных группы поведения отмечены MATCH**. Это учёт строк таблицы, не процент совпадения приложений. Среди четырёх UNKNOWN три HIGH и одна LOW; FIXED не означает проверку LIVE.

Нативный анализ подтвердил фактическое использование `exactInput`, прямой native BNB BUY и разные deadline для торговли и конвертера. Это уточняет прежний [NATIVE_AUDIT_RU.md](NATIVE_AUDIT_RU.md): ранее подтверждалось только наличие ABI и 30 секунд для торгового swap. Полные Python-исходники не восстановлены.

Все области приоритетов ТЗ рассмотрены: Converter и STOP исследованы до вызовов/ветвлений; market data — до wrapper и текущих RPC плюс отдельного измерения; стратегия — повторно проверенные ветвления и граничные сценарии; AutoPair/профили, Sweep/restart и UI — карта текущего кода и частичные свидетельства оригинала с явно указанными пробелами. Полный аудит каждого vendor-модуля EXE не заявляется.

## Карта компонентов

| Mac | Оригинал / исследованный объём |
| --- | --- |
| `strategy.py` | `DipBot.start`, growth/down-streak, gap, TP→SL, snapshot BUY guard |
| `chain.py` | Trader price/quote, `_quote_route`, кодирование V3 path; read-only измерение Mac |
| `trader.py` | `_select_safe_route`, `execute_bnb_to_quote`, `execute_quote_to_bnb`, round-trip; независимые инварианты журнала |
| `worker.py` | `DipBot` states, request_stop и receipt callbacks; очередь Mac, последующие чтения, Sweep |
| `profiles.json`, AutoPair UI | 42 публичных адреса совпадают; router-specific каталоги и полный выбор кандидатов не восстановлены |
| `storage.py`, `app.py` | Mac Keychain/state/валидация/кнопки проверены чтением кода и smoke; полная семантика Windows persistence/UI UNKNOWN |

## GAP REPORT

`S` = STATIC_CONTROL_FLOW, `C` = CONSTANT_OR_STRING, `I` = независимый инвариант нашей реализации. Статус MATCH ограничен конкретными условиями строки; это не runtime differential.

| ID | Parity / severity | Windows / Mac до изменения и влияние | Resolution / результат | Доказательства, confidence |
| --- | --- | --- | --- | --- |
| P01 | STRATEGY_DEVIATION / HIGH | Windows V2 native swap; V3 полный `exactInput`. Mac wrap + отдельные swaps: промежуточные активы, лишний газ и риск незавершённой цепочки | FIXED: BUY напрямую BNB; homogeneous V2/V3 path исполняется одной swap; V3 SELL затем отдельно unwrap только дельты | S: `0x141e7ca84`, `0x141e7dc88`, `0x141e7ff8c`, `0x141e80938`; HIGH |
| P02 | BUG / HIGH | Windows сортирует котируемые маршруты и проверяет их обратные котировки. Mac выбирал один лучший pool каждого шага до проверки: отклонение лидера скрывало безопасный fee tier | FIXED: все кандидаты восстановленного списка котируются/проверяются; выбирается лучший безопасный | S: `0x141e7718b`, `0x141e774d4`, `0x141e775d0`, `0x141e78d1c`; HIGH для отбора, каталог кандидатов отдельно P12 |
| P03 | BUG / MEDIUM | Оригинальный converter использует 60 с; общий Mac swap использовал 30 с. Возможны лишние expiry/revert | FIXED: converter 60 с, торговый swap 30 с | S: `0x141e7cddd`, `0x141e7dde6`, `0x141e800ca`; HIGH |
| P04 | BUG / CRITICAL | Оригинальный STOP сохраняет force-stop при pending. Mac `command(buy/start)` очищал stop_event: запрос STOP до/во время подготовки мог пропасть | FIXED: отмена ещё не начатых операций, проверки после подготовки/чтения; при обработке STOP удаляются оставшиеся команды очереди; подтверждённый BUY затем закрывается | S: `0x1408a07c0–0x1408a0c79`; очередь — I, не предполагаем одинаковый UI Windows; HIGH |
| P05 | BUG / CRITICAL | Windows persistence UNKNOWN. Mac `finish()` удалял operation до сохранения: OSError оставлял диск заблокированным, а память — разблокированной | FIXED: при исключении save восстанавливаются operation/history в памяти; повторный finish не дублирует историю | I; `trader.py:LiveTrader.finish`, fault injection. HIGH для Mac-багa, не parity-утверждение |
| P06 | INTENTIONAL_DIFFERENCE / HIGH | Windows bot SELL передаёт minOut=1. Mac сохраняет slippage minimum; выход может отклониться при сильном падении | RETAINED_INTENTIONALLY: защиту не ослабляли | S: `0x1408965a1–0x1408965db`; HIGH |
| P07 | INTENTIONAL_DIFFERENCE / MEDIUM | Windows converter V2 добавляет tax buffer 500 bps к slippage, ограничивает общую сумму 9500 bps. Mac не расширяет разрешённое проскальзывание на 5% | RETAINED_INTENTIONALLY: tax-токены могут давать revert при более строгом minOut; изменение требует отдельного решения | S: `0x141e77cca–0x141e77efb`; HIGH |
| P08 | INTENTIONAL_DIFFERENCE / LOW | Windows floor round-trip loss до целых bps; Mac проверяет точное ≤15%. Windows minOut снизу 1, Mac отклоняет округление до 0 | RETAINED_INTENTIONALLY: граничные 15.00…%/dust отклоняются строже | S: `0x141e75d75–0x141e75dfe`, snapshot formula; HIGH |
| P09 | INTENTIONAL_DIFFERENCE / HIGH | Windows wrapper вызывает `get_price_from_reserves`, Mac дополнительно проверяет chainId/блок; номинальная пауза не равна периоду наблюдений | RETAINED_INTENTIONALLY: проверки сохранены. Измерено 0.50–0.51 с между V2-наблюдениями, близко к gap 0.55 с | S wrapper `0x14088e397`; READ_ONLY_RPC_MAC; HIGH для Mac, полное число внутренних запросов Windows не доказано |
| P10 | INTENTIONAL_DIFFERENCE / HIGH | Windows цикл повторяет чтение после ошибки с паузой 0.05; Mac приостанавливает автоматический цикл | RETAINED_INTENTIONALLY: автоматический retry сделки/неоднозначного broadcast не добавлялся | S: bot.start, прежние evidence/error branch; HIGH для общего отличия; все типы исключений Windows не разобраны |
| P11 | INTENTIONAL_DIFFERENCE / MEDIUM | Windows использует ensure_approve_max; Mac точечный approve, дополнительные preflight, snapshot/router bound и блокировка unknown transaction | RETAINED_INTENTIONALLY: больше RPC/задержка, но инварианты ТЗ сохраняются | S SELL converter `0x141e7f60a`; Mac source/tests; HIGH |
| P12 | UNKNOWN / HIGH | Точный router-specific каталог AutoPair, приоритеты/равные котировки, cache invalidation, fee-pref и ADD/REMOVE оригинала восстановлены частично. Mac ищет прямые/двухшаговые V2/V3 через USDT/ETH; ADD BASE проверяет 0.01 WBNB и 10% | BLOCKED: не хватает доказанной полной схемы построения/обновления динамических и router-specific профилей и AutoPair; нужны связанные GUI/AutoPair функции и трассы. Известная широта Mac-кандидатов не названа MATCH | C/S `_preferred_buy_route`, `_buy_route_candidates`, pair_profiles; MEDIUM |
| P13 | UNKNOWN / HIGH | Windows restart, восстановление старых остатков и полный Sweep не доказаны эквивалентными. Mac сохраняет позиции/операции, Sweep target→bases, останавливается на неоднозначной отправке | BLOCKED: нужны ветви восстановления/сериализации и контролируемый crash/restart оригинала. Mock подтверждает Mac-инварианты, не Windows | Частичные receipt callbacks + Mac source; LOW для оригинального restart |
| P14 | UNKNOWN / HIGH | Исполнение оригинала на тех же RPC-ответах, reorg, разные типы токенов, длительная работа и точное float-поведение не проверены | BLOCKED: нет настроенной изолированной Windows VM/runtime и replay harness оригинала. В PATH нет wine/qemu/prlctl; запуск EXE на основной системе не выполнялся | Ограничение среды; уверенность в эквивалентности LOW |
| P15 | MATCH / N/A | DIP до переноса базы; growth относительно предыдущей цены, два снижения, flat сохраняет streak, gap строго >0.55, TP перед SL | Ограниченный MATCH: статические условия + 13 синтетических vectors, без реального Windows replay | S и fixtures; HIGH для порядка, не для всех float-границ |
| P16 | MATCH / N/A | Цена quote/token и единицы quote/token raw, V2/V3 ориентация, параметры BUY guard | Ограниченный MATCH: обычные входы; точная арифметика Mac и дополнительные bounds могут отличаться на границах | S предыдущего аудита + `test_chain`, `test_strategy`; MEDIUM |
| P17 | MATCH / N/A | Нормальный STOP при pending BUY ждёт receipt и закрывает позицию; pending SELL завершается перед остановкой | Ограниченный MATCH: успешный сценарий и отсутствие дубликата в последовательном Worker. Ошибки receipt/reorg не покрыты этим MATCH | S request_stop и контролируемая очередь; MEDIUM |
| P19 | STRATEGY_DEVIATION / HIGH | Mac потерял mode/fee 42 профилей, перебирал любые bridge/fee. Оригинал имеет preferred-first, bridge fee=100, ETH fallback только при via_eth_v3 | FIXED для статических профилей: routes.py, metadata и dedup сохраняют восстановленный порядок; dynamic/AutoPair остаются P12 | S: `0x141e72ab0`, `0x141e733e4`, `0x141e73b27`, `0x141e840c1`; C: 42 tuples; MEDIUM/HIGH по области |
| P18 | UNKNOWN / LOW | Mac настройки формируются UI, восстановление старой позиции использует сохранённый entry; полная Windows-политика defaults/persistence/migration не установлена | BLOCKED: нужны GUI/config методы и изолированные конфиги Windows. Реальные пользовательские данные не исследовались | Mac source, C; LOW |

## Изменения и регрессии

- P01/P03: `chain.py` — ABIs, path validation, quoteExactInput; `trader.py:convert` — native funding, один swap, receipt accounting, deadline 60. Тесты проверяют обе версии/направления, selector, packed fees, native gas adjustment, отсутствие отдельного wrap перед BUY и unwrap только нового WBNB.
- P02: `conversion_route` сохраняет варианты fee tiers восстановленного каталога, исключает смешанные V2/V3 paths, котирует целый путь в одном вызове и проверяет обратный. Начальный маршрут и его minOut не ослабляются после approve. Каталог путей ещё не идентичен P12.
- P04: `worker.py:command/run` — STOP до старта, во время configure/read, после подтверждённого BUY, очистка очереди. Сценарий до исправления реально падал: `open_position` вызывался после STOP.
- P05: `LiveTrader.finish` — возврат предыдущего snapshot словаря при save error. Тест до исправления реально падал на отсутствии operation; после исправления новая операция блокируется.

Первый red-run новых тестов: 3 падения (журнал, отсутствующий quote_route, старый интерфейс выбора маршрута). После реализации конвертера отдельно зафиксирован red-run STOP: 1 failure, 7 passed. Ошибки отсутствующего API — проверка нового интерфейса, не самостоятельное доказательство торгового бага; P01/P02 дополнительно обоснованы нативным кодом и старым greedy-циклом. Устаревший тест последовательного конвертера заменён сценариями доказанного атомарного вызова, а не ослаблен ради зелёного результата.

В P05 гарантируется сохранение блокировки в памяти при ошибке save. Аварийное отключение питания, ошибки fsync после уже выполненного replace и физическая надёжность файловой системы этим unit-тестом не доказаны.

## Измерение времени

Команда: `uv run --frozen python -m tools.measure_feed --count 5 --output /tmp/feed.json`. 2026-09-24 09:53:03 UTC, публичный `https://bsc-dataseed.binance.org`, V2 USDT/WBNB, 5 чтений после verify_pool. Медиана read 0.4009 с, max 0.4068 с; интервалы наблюдений 0.5017–0.5076 с при паузе 0.1 с. Снимки и параметры сохранены в [PARITY_EVIDENCE.json](PARITY_EVIDENCE.json). Это короткая выборка одного RPC, не оценка p95 и не сравнение с запущенным Windows.

Mac V2 price выполняет chainId, get_block и getReserves; V3 добавляет liquidity/slot0 (всего четыре запроса). Данные пула читаются на номере проверенного блока. Signal→submit/receipt в реальной сети не измерялись: отправка не разрешена. Их офлайн-тесты проверяют порядок и отсутствие дубликатов, не обещают latency. Снятие проверок ради скорости не выполнено.

## Воспроизведение анализа

```bash
# Из корня dipbot-mac. EXE_PATH — путь к оригинальному EXE, не к лицензии.
python -m tools.parity_native "$EXE_PATH" --output /tmp/native.json
uv run --with capstone python -m tools.parity_native "$EXE_PATH" \
  --output /tmp/native.json --disassembly /tmp/dipbot-parity-disassembly
uv run --frozen pytest -q
```

20 hash-locked участков кода включают ранее исследованное ядро, STOP и семь функций конвертера. Инструмент не загружает EXE как исполняемый код. VA относительно image base `0x140000000`; смещения и хеши в JSON. Декомпилятор r2ghidra использовался для помощи чтению; ключевые выводы сверены по инструкциям и константам. Воспроизводимый экспорт не зависит от `/tmp` прошлой сессии.

Контрактные интерфейсы сверены с [ISwapRouter](https://github.com/pancakeswap/pancake-v3-contracts/blob/main/projects/v3-periphery/contracts/interfaces/ISwapRouter.sol) и [PeripheryPayments](https://github.com/pancakeswap/pancake-v3-contracts/blob/main/projects/v3-periphery/contracts/base/PeripheryPayments.sol): exactInput принимает полный path, native value используется для оплаты WBNB. Это подтверждение интерфейса контракта, а не авторской стратегии Windows.

## Стратегические наблюдения

- Нулевая BUY tolerance может не вместить даже комиссию пула. Это следствие восстановленного spot guard; не «исправлено» добавлением комиссии.
- Расширение V2 converter tolerance ещё на 5% могло бы улучшить совместимость с tax-токенами, но ослабляет minOut. Формула оригинала восстановлена в [STRATEGY_SPEC_RU.md](STRATEGY_SPEC_RU.md); LIVE-политика сохранена строгой.
- Один swap делает маршрут атомарным внутри EVM, но approve и V3 SELL→unwrap остаются отдельными транзакциями. При сбое unwrap WBNB остаётся у кошелька, операция заблокирована до сверки. Это не полная атомарность всего workflow.

## Продолжение

Текущий код основан на `4f3e986` плюс незакоммиченные изменения этой задачи. Commit/push не выполнялись. Результаты окончательных тестов/сборки — [VALIDATION_RU.md](VALIDATION_RU.md).

Следующая независимая задача: связать router-specific таблицы, динамические профили и AutoPair/GUI функции (P12). Статический порядок converter fallback/dedup теперь восстановлен; не начинай его заново без новых противоречий. Адреса функций уже включены в `tools/parity_native.py`; начни с их свежего дизассемблирования и свяжи профили с исполняемыми конструкторами. После этого — persistence/Sweep restart (P13). Для P14 требуется отдельно подготовленная изолированная Windows-среда с запрещённым внешним broadcast и доступным штатным запуском; обход лицензии не является способом устранения блокера. Не считай наличие Wine достаточным replay harness.

Оставшиеся UNKNOWN не заменяются догадками. Статические и офлайн-проверки не подтверждают полное совпадение или готовность к LIVE.
