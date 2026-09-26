# Формальная модель Windows и Mac

Артефакт: NRNF v1.4.14, hash и адреса в [PARITY_EVIDENCE.json](https://github.com/Hostlife22/dipbot-mac/blob/89e3a4f/docs/PARITY_EVIDENCE.json). Это реконструкция по машинному коду, не восстановленные Python-исходники. Трасс запущенного Windows нет. Историческое сравнение Mac с baseline `4f3e986` описано в [PARITY_AUDIT_RU.md](https://github.com/Hostlife22/dipbot-mac/blob/89e3a4f/docs/PARITY_AUDIT_RU.md).

## Обозначения и цена

`p` — quote units / target token units. `a` — количество quote raw units, `dq/dt` — decimals quote/target. Native BNB отличается от WBNB ERC-20; BNB нужен также на газ. AMOUNT торговой стратегии выражен в quote выбранного пула.

V2: `p = (reserve_quote / reserve_target) * 10**(dt-dq)`.
V3: raw token1/token0 = `sqrtPriceX96**2 / 2**192`; ориентировать по target и умножить на `10**(dt-dq)`.
Spot price не равна router quote конечной суммы. Router учитывает комиссию и price impact; token tax может дополнительно менять фактический выход. Mac вычисляет цену с Decimal context 78 и закрепляет pool reads за номером проверенного блока. Точное float-округление всех ветвей Windows остаётся ограничением сравнения.

OHLC, CEX bid/ask и биржевые partial fills не являются входами исследованного reserve/slot0 пути. Отсутствие других стратегических фильтров во всём приложении не доказано только этим фактом.

## Windows: реконструированное ядро

| Состояние / событие | Условие и действие | Следующее состояние |
| --- | --- | --- |
| start | Подготовка, начальная цена/база; точные все failure paths не восстановлены | monitoring |
| monitoring, новое наблюдение | Если интервал с предыдущим >0.55 с, перенести base/last на p, сбросить down_streak; не покупать на этом наблюдении | monitoring |
| monitoring без gap | `dip = (base-p)/base*100`; проверить порог BUY до обновления базы | pending_buy при сигнале/отправке |
| monitoring, BUY не сработал, `p > last` | base=p, down_streak=0 | monitoring |
| monitoring, BUY не сработал, `p < last` | Увеличить down_streak; если >=2, base=p и down_streak=0 | monitoring |
| monitoring, `p == last` | Сохранить down_streak/base | monitoring |
| pending_buy, успешный receipt | Прочитать цену; reference TP/SL = новая цена. Учёт token amount и fallback по live balance имеет дополнительные ветви | open |
| open, наблюдение | `change = (p-entry_reference)/entry_reference*100`; TP проверяется перед SL; gap не подавляет выход | pending_sell при TP/SL |
| SL | force_stop_after_sell=true; SELL, затем остановка | stopped после подтверждения |
| pending_sell, успешный receipt | Очистка позиции, новое чтение цены, перенос базы | monitoring или stopped |
| STOP/open | `_sell_requested=true`, force_stop_after_sell=true, reason; пробудить стратегию | pending_sell → stopped |
| STOP/pending_buy | Сохранить force-stop, дождаться receipt, запросить выход | pending_sell → stopped |
| STOP/pending_sell | Сохранить force-stop, дождаться receipt | stopped |
| STOP/прочее | running=false, stopped, уведомление | stopped |

Операторы границ, состояния и ссылки: `0x140898371`, `0x14089b135`, `0x14089b3a9`, `0x14089ba36`, `0x14089bc23`, `0x1408a07c0–0x1408a0c79`. Не все exception/restart paths представлены этой нормальной моделью.

BUY:

```text
spot_expected_raw = floor((a / 10**dq) / p * 10**dt)
guard_percent = max(0, min(99, slippage - dynamic/100))
min_raw = max(1, floor(spot_expected_raw * (1 - guard_percent/100)))
```

В торговом BUY/SELL deadline=30 с; обычный receipt poll=0.2 с. В bot SELL найдена граница minOut=1. Сравнение TP/SL не является чистым PnL с газом, комиссией и налогом.

## Windows: конвертер

`_preferred_buy_route` использует режим профиля: direct_v2, direct_v3, via_usdt_v3, via_eth_v3, native_wrap. 42 публичных tuples дают mode и fee конкретного профиля; это не полная router-specific таблица GUI/AutoPair.

`_buy_route_candidates` для ненативного профиля:

1. Preferred route.
2. V2 WBNB→base, V2 WBNB→USDT→base.
3. Для fee tiers 100/500/2500/10000: V3 direct и V3 через USDT; через ETH — при mode=via_eth_v3.
4. В обеих V3 bridge-парах WBNB/USDT и WBNB/ETH первый fee=100. Последний fee берётся из профиля для preferred, из итерации для fallback.
5. Дедупликация по route key с сохранением порядка. SELL использует обратные пути; полностью все сценарии dynamic profile ещё не доказаны.

Адреса: preferred `0x141e71fb0`, candidates `0x141e72ab0`, проверка via_eth `0x141e733e4`, dedup `_route_key` `0x141e73b27`; bridge constants `0x141e840c1–0x141e840f5`. Mac дополнительно исключает пути с повторяющимися токенами до вызова router.

`_quote_route`: V2 `getAmountsOut` для всего path; V3 packed path `address(20 bytes) + fee(3 big endian) + address...`, вызов `quoteExactInput`. `_select_safe_route` сортирует выходы по убыванию, затем проверяет обратную котировку кандидатов; failed reverse/слишком большая потеря отклоняют кандидат, а не все варианты.

```text
loss_bps = max(0, floor((amount_in - reverse_out) * 10000 / amount_in))
reject if loss_bps > 1500
slippage_bps = conversion_of_percent_to_integer_bps(slippage)
tax_buffer_bps = 500 if route.kind == v2 else 0
effective_bps = min(9500, slippage_bps + tax_buffer_bps)
minimum = max(1, floor(expected_out * (10000-effective_bps) / 10000))
```

Порядок сложения/bounds/minimum подтверждён `0x141e77cca–0x141e77efb`. Детальное преобразование произвольного float Slippage в целые bps требует отдельной проверки `_converter_slippage_bps`; эту строку формулы не следует читать как доказанное точное округление.

V2 BUY: `swapExactETHForTokensSupportingFeeOnTransferTokens`, native value. V2 SELL: `swapExactTokensForETHSupportingFeeOnTransferTokens`. V3 BUY: `exactInput(path, recipient, now+60, amount, minimum)` с native value. V3 SELL: exactInput в WBNB, ожидание receipt, затем withdraw дельты WBNB. Swap-путь атомарен внутри одной транзакции; approve и unwrap — отдельные операции.

## Mac: модель после исправлений

Нормальные условия DIP/gap/TP/SL совпадают с реконструированным ядром в проверенных сценариях. `Strategy` хранит base/entry/last/down_streak/stopped. `Worker` исполняет команды последовательно; отдельной параллельной сделки нет. UI STOP — threading.Event. Ещё не начатые queued BUY/START отменяются; STOP во время активной операции обрабатывается после возврата из неё, затем закрывается учтённая позиция. Статусы pending представлены журналом LiveTrader и блокирующим ожиданием Worker, а не одноимённым состоянием Strategy.

```text
intent persisted → preflight → exact allowance if needed
→ unsigned tx + pending nonce checks → sign locally
→ local hash persisted → broadcast → receipt
→ delta balance → tracked holdings persisted
→ post-receipt reference price → finish journal
```

После неизвестного результата, ошибки чтения/сохранения или revert автоматический цикл приостанавливается; незавершённая операция сохраняет latch. Повторный `finish` после save failure не удаляет latch в памяти и не дублирует history. Операция может остаться заблокированной даже до первого broadcast; это более строгое поведение.

BUY сохраняет `max(snapshot minimum, pre-approve quote minimum, post-approve quote minimum)`. Округлившийся к 0 minOut отклоняется. Количество позиции — фактическая дельта target, а не весь кошелёк; старые токены не включаются автоматически. SELL ограничен tracked amount и текущим балансом, использует Slippage minimum. Старая сохранённая позиция сохраняет свой entry; историческое значение не заменяется сегодняшней ценой при миграции.

Конвертер использует восстановленные статические mode/fee и порядок fallback. Для неизвестных dynamic bases выбран default direct_v2 с теми же fallback; совпадение этого default с оригиналом не доказано. Кандидаты подтверждаются canonical factory/pools. Применяется точная граница round-trip ≤15%, minOut без добавочных 5% tax allowance, fresh check после approve и сохранение прежней границы. V2 native SELL учитывает `balance_after - balance_before + gasUsed*effectiveGasPrice`. V3 unwrap не включает старый WBNB. Внешние параллельные переводы могут искажать balance delta — тестами отсутствие таких переводов не гарантируется.

## Behavioral Diff

| Сценарий | Windows evidence/model | Mac baseline 4f3e986 | Mac теперь |
| --- | --- | --- | --- |
| DIP=3, цены 100→99→99→98 через 0.1 с | base=98, нет BUY, flat сохраняет streak | Совпадает после прежнего аудита | Совпадает; fixture flat_keeps_streak |
| 100→98.5→97 | BUY до переноса базы | Совпадает | Совпадает; fixture buy_before_reanchor |
| Первый fee tier: output=200, reverse=50 на input=100; второй: output=190, reverse=95 | Первый отклоняется, второй принимается | Терял второй из-за greedy выбора | Выбирает второй |
| V3 BUY через USDT | Один exactInput с полным path/native value | Wrap + два отдельных swaps | Один exactInput; fees/path проверены |
| STOP уже запрошен перед queued BUY | Намерение остановки сохраняется; очередь Windows отдельно не восстановлена | stop_event стирался, возможен BUY | BUY отклоняется до открытия позиции |
| save падает при finish | Windows UNKNOWN | Memory latch удалён, disk latch остался | Memory latch сохранён; повторная операция запрещена |
| V2 converter Slippage=2%, output=10000 | Восстановленная модель min=9300 при tax buffer 5% | Строже, без 5% | Строже: min=9800; намеренно |

Expected для стратегии хранится в `tests/fixtures/parity/strategy.json`: 13 заранее заданных сценариев с hash/VA, без вызова тестируемой реализации для генерации expected. Тесты журнала/очереди — инварианты Mac; ABI tests — контрактный интерфейс; ни одна из этих групп не является записью исполнения оригинального Windows.

## Уточнение восстановления и STOP (2026-09-24)

STOP проверяется до начала Converter и после RPC-подготовки каждого этапа Sweep. При пропуске непроданного target сохраняется entry. Sweep включает пулы сохранённых позиций текущего кошелька. START/BUY другого пула при сохранённой позиции того же кошелька отклоняется. Подробности и границы Windows-соответствия: [STATIC_RECOVERY_AUDIT_RU.md](https://github.com/Hostlife22/dipbot-mac/blob/89e3a4f/docs/STATIC_RECOVERY_AUDIT_RU.md).

## AutoPair и динамические профили

Discovery теперь сортирует и группирует кандидатов по восстановленным правилам оригинала; ready-кандидаты имеют приоритет. PENDING не разрешает торговлю. Проверенные converter mode/fee хранятся отдельно для V2/V3 и используются в свежих котировках. Подробная спецификация и оставшиеся отличия: [AUTOPAIR_PARITY_RU.md](https://github.com/Hostlife22/dipbot-mac/blob/89e3a4f/docs/AUTOPAIR_PARITY_RU.md).
