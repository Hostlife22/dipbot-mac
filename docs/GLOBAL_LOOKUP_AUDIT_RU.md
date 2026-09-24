# Глобальные lookup и изолированная эмуляция словаря

2026-09-24. Без запуска Windows-приложения, RPC и транзакций. Production Mac не менялся. Это продолжение незакрытого аудита, не восстановленные исходники и не доказательство полного паритета.

## Новые результаты

`tools/global_lookup_audit.py` распознал **174 getter-функции** по точному 272-байтовому шаблону: совпадают инструкции, регистры, поля объектов, локальные переходы и адреса внешних вызовов; различаться могут RIP-relative адреса данных. Для каждого записаны имя, модуль таблицы констант, слоты словарей, версия и индекс кэша, хеш тела. Это значительно строже поиска имени рядом с call.

[GLOBAL_LOOKUP_EVIDENCE.json](GLOBAL_LOOKUP_EVIDENCE.json) различает `gui.Trader`, `wallet_sweep.Trader`, `gui.DipBot`, `dynamic_pairs.REGISTRY_PURPOSE`, `trader.Web3` и другие обращения. Значения этих глобальных переменных, их последующие замены и фактические классы объектов этим не устанавливаются. Название модуля взято из принадлежности таблицы констант; идентичность fallback-словаря не доказана здесь.

Повторный проход 231 тела функций с новыми помощниками дал **5593 распознанных обращения** вместо 5064. [LOOKUP_FLOW_EVIDENCE.json](LOOKUP_FLOW_EVIDENCE.json) сохраняет неопределённые аргументы, границы CFG и ссылки на входные хеши.

Примеры уточнения:

- `Trader.__init__`: `trader.Web3.HTTPProvider(self.http_rpc_url)`, затем вызов `trader.Web3(provider)`; `trader.threading.Lock` для nonce-lock. Это путь значений, не проверка реального класса Web3/Lock.
- `DynamicPairRegistry._save`: отдельные lookup REGISTRY_KIND, REGISTRY_VERSION, REGISTRY_PURPOSE; сортировка `self._records.values()` с keyword `key`, значение которого остаётся неизвестным. Обратная вставка удалённой записи не доказана.
- `secure_store.read_protected_json`: цепочка `Path(...).read_text(encoding='ascii')`, `json.loads`, `base64.b64decode`; tuple/kwargs последнего вызова остаются неполными. Новые версии/миграции не обнаружены этим уточнением.
- `WalletSweepEngine.run`: локализованы глобальные обращения к profile_assets, choose_registered_candidate, safe_error и WalletSweepReport. У вызова choose_registered_candidate результат None ведёт к skipped (`0x14209b490–0x14209b536`), тогда как NULL-результат вызова идёт в обработку исключения (`0x14209b448–0x14209b462`). Их нельзя смешивать как одинаковую ошибку маршрута.
- AutoPair: в обработчике результата различимы глобальные STATUS_PENDING, STATUS_CATALOG_TOKEN и остальные статусы. Нового периодического таймера этим не найдено.

Сопоставление прежних 296 контекстов FLOSS: **226 имеют статический путь, 70 — нет**, конфликтов путей не обнаружено. Ранее было 192/104. [FLOSS_LOOKUP_REQUIREMENTS.json](FLOSS_LOOKUP_REQUIREMENTS.json) не объявляет эти пути реальными объектами памяти.

## Что удалось выполнить в эмуляторе

`tools/emulate_cached_lookup.py` использует Envi из окружения FLOSS. Код getter-функций и unicode-key lookup эмулируется по настоящим байтам EXE, с синтетическими словарями/известными ASCII-строками. DLL Python читается как данные; entry point и функции DLL не запускаются. Значения глобальных переменных оригинала не подставляются как якобы восстановленные.

Для пяти getter-функций выполнено **20 сценариев**:

- 10 попаданий в кэш при индексах 0 и 1 — возвращён ожидаемый указатель на синтетическое значение;
- 5 несовпадений версии кэша — реально пройден helper `0x1428fe580`, ключ найден по идентичности указателя, значение возвращено;
- 5 отсутствующих ключей — выполнение дошло до fallback helper `0x140006c30` и остановлено на границе подготовленной модели. Успешный возврат fallback не подделывался.

Результаты: [CACHED_LOOKUP_EMULATION.json](CACHED_LOOKUP_EMULATION.json). Это рабочий изолированный компонент чтения словаря. Вставка/изменение словаря, вычисление настоящих строковых хешей, сравнение разных строковых объектов, типовая диспетчеризация, функции, модули и tstate не инициализированы. Полный FLOSS string-decoding проход не выполнялся; новых декодированных строк нет.

## Найденная ошибка Envi

В установленном окружении Envi инструкция `48 c7 c0 ff ff ff ff` (`mov rax, -1`) даёт `0xffffffff`, хотя x64 расширяет immediate со знаком до `0xffffffffffffffff`. На missing-key пути helper возвращает этой инструкцией -1. Неверное значение превращается в положительный индекс и вызывает чтение по адресу `0x1070001020` в синтетическом эксперименте.

Проверка на настоящем Intel Mac: отдельная C-программа, содержащая только эти синтетические байты в inline asm, напечатала `ffffffffffffffff` и завершилась с кодом 0. Windows EXE не запускался. Повторяемый фрагмент:

```c
unsigned long long result;
__asm__ volatile (".byte 0x48,0xc7,0xc0,0xff,0xff,0xff,0xff" : "=a"(result));
```

`tools/envi_lookup_step.py` исправляет семантику только этой точной инструкции в изолированном эксперименте. Файл EXE, установленный пакет Envi и прочие FLOSS-проходы не изменены. Это не заглушка функции и не принудительный успешный возврат: ветви, память и внешние вызовы сохраняются. Три теста проверяют 64-битное расширение, неизменность flags, настоящий TEST/JS и отсутствие расширения для 32-битного MOV.

**Связь с прежними восемью ошибками возврата FLOSS не установлена.** Нельзя объявлять их исправленными по этому эксперименту.

## Воспроизведение и проверка

В Python с Capstone/pefile:

```bash
python -m tools.global_lookup_audit /path/to/reference.exe --output docs/GLOBAL_LOOKUP_EVIDENCE.json
python -m tools.unwind_inventory /path/to/reference.exe --lookups docs/GLOBAL_LOOKUP_EVIDENCE.json --output docs/LOOKUP_FLOW_EVIDENCE.json
python -m tools.floss_receiver_requirements docs --inventory docs/LOOKUP_FLOW_EVIDENCE.json --output docs/FLOSS_LOOKUP_REQUIREMENTS.json
python -m unittest discover -s tools/tests -q
```

В Python окружения FLOSS/Envi:

```bash
python -m unittest tools.test_envi_lookup_step -q
python -m tools.emulate_cached_lookup /path/to/reference.exe --lookups docs/GLOBAL_LOOKUP_EVIDENCE.json --output docs/CACHED_LOOKUP_EMULATION.json
```

Проверены **29 тестов инструментов**, отдельно **3 теста Envi** и 20 сценариев эмуляции (15 возвратов, 5 явно ограниченных fallback). Python compile и diff check прошли. Последний полный pytest приложения — 336 passed; приложение не менялось, этот набор и GUI smoke повторно не запускались.

## Что осталось

Не закрыты inline lookup и другие формы getter, конкретные runtime binding, остальные tuple/kwargs, полноценный Python runtime и повторный FLOSS decoding. Через них остаются открыты полные rollback/close/задачи, порядок всех ошибок Sweep, полнота событий AutoPair и оставшиеся изменения профилей/миграции. Нынешний проход дал более точные места дальнейшего разбора и воспроизводимую проблему эмулятора; он не заменяет семантическую проверку этих ветвей.
