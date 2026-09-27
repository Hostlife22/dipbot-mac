# Инструменты

Запускайте из корня репозитория через `uv run python -m tools.<имя> --help`. Перед запуском изучите аргументы и режим: здесь есть инструменты реального исполнения, это не общий безопасный набор команд для пакетного запуска.

| Назначение | Модули |
| --- | --- |
| PAPER и интерфейс | `paper_workspace`, `token_ui_paper_check`, `paper_soak`, `gui_acceptance` |
| Визуальная проверка без сети | `design_audit` |
| Анализ записанного рынка | `replay_market`, `compare_paper_runs`, `market_cycle_audit`, `validate_strategy` |
| RPC и задержки | `read_only_probe`, `rpc_latency_probe`, `cycle_latency_report` |
| Восстановление и хранение | `recovery_readonly_check`, `storage_benchmark`, `check_local_cancellation` |
| Локальный fork | `fork_roundtrip`, `fork_sweep_audit` |
| LIVE-аудит с расходами | `live_ui_audit` — только в явно разрешённом бюджете |
| Совместимость и публичные данные | `inspect_release`, `migrate_windows_ui`, `windows_dpapi_probe` |
| Вспомогательные модели для тестов | `native_models`, `protected_format` — импортируемые модули |

`windows_dpapi_probe` проверяет системный DPAPI только на Windows. Проверки fork требуют локального узла; PAPER/read-only требуют RPC. `token_ui_paper_check` — CLI-обёртка; реализация для приложения находится в `dipbot/checks/token_ui_paper.py`. Общий read-only guard находится в `dipbot/checks/read_only.py`.

Вывод прогонов направляйте в `.local-artifacts/` или `/tmp`, без секретов. Старые анализаторы Nuitka/Ghidra/FLOSS и промежуточные отчёты доступны в Git на `89e3a4f`; они не нужны для запуска Mac-приложения.

## Performance baseline

`performance_baseline` измеряет локальную стратегию, overhead трассировки и ABI
без подключения к сети. `rpc_latency_probe` разделяет новые чтения и cache hits,
сохраняет block/hash и предварительные p95/p99. `cycle_latency_report` учитывает
отдельные approve/swap и ACK даже при последующей ошибке; финальность не предполагается.

```bash
uv run --frozen python -m tools.performance_baseline --output .local-artifacts/performance/cpu.json --profile .local-artifacts/performance/cpu.prof
uv run --frozen python -m tools.rpc_latency_probe --samples 1000 --max-seconds 300 --output .local-artifacts/performance/rpc.json
```

Ограничение по времени может завершить RPC-прогон раньше 1000 независимых блоков;
смотрите `independent_blocks`, а не только число итераций. Сравнивайте одинаковые
cohorts и сборки. Ни один из этих инструментов не отправляет транзакции.

Для чередующегося сравнения direct/Multicall с одинаковыми canonical guards:

```bash
uv run --frozen python -m tools.rpc_latency_probe --identity --samples 5 --output .local-artifacts/performance/identity-ab.json
```

`fork_roundtrip --paired-performance` дополнительно выполняет три пары BUY/SELL
(число пар задаётся `--paired-repeats 1..30`)
каждого варианта на одном Anvil, восстанавливая snapshot между вариантами.
Все дополнительные отправки локальные; учитываются в `benchmark_local_submissions`.
Не совмещайте с `--sweep-audit`: это разные наборы проверок. Результаты fork не
представляют BSC consensus или paid RPC. `rpc_latency_probe --head-seconds 60`
добавляет shadow-сравнение HTTP/WSS по одинаковым hash без торговых решений.

Для отдельного визуального PAPER-прогона trailing в собранном приложении доступен
`--market-paper-trailing 3`; без этого параметра сохранены прежние настройки.

```bash
uv run --frozen python -m tools.rpc_latency_probe --shadow-seconds 120 --finality-seconds 30 --output .local-artifacts/performance/shadow-finality.json
```

Этот режим отдельно сравнивает polling/HeadSchedule с HTTP-проверкой цены V2,
сохраняет несовпавшие head и ошибки. `finality` наблюдает публичный блок по тегу
провайдера и canonical hash; не отправляет tx и не меняет receipt-policy бота.

`fork_roundtrip --cohort-audit` проверяет allowance needed/ready и ABI warm/cleared
по три повтора BUY/SELL, затем Converter. Это локальные транзакции Anvil через
read-only proxy; холодный сетевой клиент и BSC latency здесь не моделируются.
`--sweep-audit` сохраняет отдельные traces частичных отказов и STOP.
В изолированном `.app` PAPER можно указать `--market-paper-wss wss://bsc-rpc.publicnode.com`
и HTTP через `--acceptance-endpoint`; ключи и LIVE заблокированы аудитом.

Для будущего анализа ликвидности без archive RPC сохраняйте снимки во время наблюдения:

```bash
uv run --frozen python -m tools.read_only_probe --endpoint https://bsc-rpc.publicnode.com --token TOKEN_ADDRESS --pool POOL_ADDRESS --seconds 600 --amount-usd 20 --output .local-artifacts/performance/market-snapshots.jsonl
```

Файл создаётся новым; каждые 5 с записываются block/hash, исходное состояние пула,
котировки BUY/обратной продажи на том же блоке и активность за 100 блоков. USD —
индикативный курс базового токена с источником/возрастом. Только `canonical=true`
можно использовать как подтверждённый снимок; ошибки и неполная активность явные.
Обратная котировка не моделирует состояние после BUY, tax/MEV и газ. RPC guard
исключает отправки; инструмент не читает кошелёк. Не запускайте много диагностических
потоков одновременно на public RPC: учитывайте rate limits и сохраняйте ошибки.

`rpc_latency_probe --connections --samples 5 --output REPORT.json` сравнивает первый
запрос новой HTTP-сессии и прогретый клиент в трёх чередующихся сериях. SDK retries
отключены; это не разложение DNS/TLS и не измерение отправки транзакции.


LIVE-аудит сохраняет общий бюджет при продолжениях; `--inject-lost-ack` разрешён
только для первого WRAP нового журнала и требует последующего отдельного `--resume`.
Он реально отправляет tx, затем имитирует потерю ответа: повторная отправка в этом
контексте запрещена. Это не read-only команда. Финальность наблюдается отдельным
Chain; задержка очереди учитывается. Бюджетная USD-проверка остаётся дополнительной
стоимостью времени аудитора, поэтому его полный цикл не равен чистому hot path.

`fork_roundtrip` поддерживает отдельный round-trip с произвольной канонической
базой: сначала локальный Converter BNB → BASE, затем BUY/SELL TARGET и
Converter BASE → BNB. Расходы конвертации входят в потери round-trip;
расширенные сравнительные cohorts пока требуют WBNB.

`live_ui_audit --automatic-token TOKEN --automatic-pool POOL` принимает явный
канонический V2-пул, в том числе с базой, отличной от WBNB. Требуются отдельная
авторизация LIVE и предварительная проверка продаваемости. Новый журнал,
не более 600 секунд и двух покупок, размер позиции — пятая часть полученной
базы; резерв расходов не уменьшается после возвратов. Файл `stop.request`
в каталоге аудита вызывает штатный STOP с последующим возвратом базы в BNB.
При неоднозначной транзакции автоматическая очистка не выполняется.

После неуспешного автоматического прогона `--cleanup-only` с теми же TOKEN/POOL
сохраняет бюджет, проверяет receipts и нулевой TARGET, затем через штатную сверку
снимает блокировку и продаёт только остаток BASE. Не запускает стратегию повторно.
`automatic_scenario_passed` сохраняет исходный результат; успешная очистка не
означает успешную покупку. В отчёт включается газ подтверждённых revert-транзакций.
