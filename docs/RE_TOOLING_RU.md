# capa, FLOSS и полная Ghidra: установка и сопоставление

2026-09-24. Исследован тот же EXE v1.4.14 с SHA-256 `0f9da36f8a9f0b9908d69e00a7c7c3501efb8d9823246078063267dababd0f72`. Windows-бот не запускался; лицензия, кошельки и пользовательские настройки не читались. Production-код Mac и его зависимости не изменены.

## Установлено и настроено

| Компонент | Состояние |
| --- | --- |
| capa | 9.4.0, отдельное окружение `uv tool` |
| FLOSS | 3.1.1, отдельное окружение `uv tool` |
| Правила capa | tag v9.4.0, commit `2af9fbfc1c9b4634dbeb76b5d34fca9389fa7f80` |
| Сигнатуры библиотек | Три `.sig` из capa v9.4.0; SHA-256 сохранены в свидетельствах |
| Полная Ghidra | Существующая 12.0.3; настроены отдельные GUI/headless launchers |
| Java для Ghidra | Существующая Homebrew JDK 21.0.10; системная Java 17 не заменена |

Команды доступны в PATH:

```bash
capa --version
floss --version
dipbot-ghidra
dipbot-ghidra-headless
```

Launchers находятся в `~/.local/bin`, выбирают `/usr/local/opt/openjdk@21` и `/usr/local/opt/ghidra/libexec`. Проверен headless запуск полной Ghidra, импорт PE, адресное дизассемблирование и декомпиляция. Интерактивная работа в GUI отдельно не проверялась.

Установка выполнена через `uv tool install --python 3.12 flare-capa` и аналогично `flare-floss`. Для воспроизведения этих версий используйте `flare-capa==9.4.0` и `flare-floss==3.1.1`. Python dependencies tools изолированы от `.venv` приложения. Источники: [установка capa](https://github.com/mandiant/capa/blob/master/doc/installation.md), [установка FLOSS](https://github.com/mandiant/flare-floss/blob/master/doc/installation.md), [сигнатуры capa](https://github.com/mandiant/capa/tree/v9.4.0/sigs).

## Полученные результаты

### FLOSS

Полный static-strings проход EXE завершился, exit 0: **635005 строковых кандидатов**. Это включает шум от машинного кода и зависимости; число не является количеством осмысленных строк бота.

Найдены `secure_vault.dat`, `ui_state.json`, `runtime-settings`, `CATALOG_TOKEN`, `_autopair_timer`, `_save_ui_state`, `_close_trader`, `CryptProtectData`, `CryptUnprotectData`, `pending_buy`, `pending_sell`. Адреса файловых смещений сохранены в [свидетельствах](RE_TOOLING_EVIDENCE.json). Они согласуются с ранее разобранными хранилищами и состояниями, но сами по себе не доказывают достижимость ветвей.

Дополнительный проход stack/tight/decoded ограничен четырьмя функциями и временем 420 с. FLOSS всё равно предварительно строит Vivisect workspace для большого EXE. Этот проход не дал завершённого отчёта в пределах лимита; отсутствие новых декодированных строк **не установлено**.

### capa

Файловый анализ через API завершён: **109487 признаков**, четыре совпадения правил:

- `compiled with nuitka` — согласуется с прежним разбором native-кода и таблиц Python-констант;
- `linked against libcurl` — признак, который может относиться к инфраструктуре/зависимостям, а не стратегии;
- `reference analysis tools strings`;
- `reference anti-VM strings targeting VirtualBox`.

Последние два совпадения — строковые признаки, **не доказательство антиотладки/анти-VM логики бота**. Не привязаны к подтверждённой выполняемой ветви. Не используются для изменения Mac.

CLI `capa -b pefile` в установленной версии завершился с NotImplementedError. Вместо изменения установленного пакета добавлен `tools/capa_file_audit.py`, вызывающий API `find_file_capabilities` исключительно для файловых признаков. Полноценный проход Vivisect ограничен 420 с и не дал завершённого отчёта. Покрытие функций capa этим не подтверждено.

### Ghidra и независимая сверка листинга

Общий auto-analysis достиг лимита 180 с; это **не полный анализ бинарника**. Затем выполнен адресный проход семи известных диапазонов без повторного общего анализа. Скрипт проверяет хеш EXE, задаёт ранее восстановленные границы, добавляет подписи выбранных констант и экспортирует `.asm`/`.c`. Границы и подписи взяты из нашего анализа, а не независимо выведены Ghidra.

| Функция | Инструкций Ghidra / Capstone | Сверка адресов начала инструкций |
| --- | --- | --- |
| GUI.closeEvent | 370 / 370 | Совпало |
| GUI.stop_bot | 199 / 199 | Совпало |
| CommercialGUI.closeEvent | 372 / 372 | Совпало |
| AutoPair error | 690 / 690 | Совпало |
| Sweep error | 313 / 313 | Совпало |
| Optional Trader.close | 257 / 257 | Совпало |
| Converter slippage | 443 / 447 | Только четыре trailing INT3 `0x141e7665c–0x141e7665f` у Capstone |

Декомпиляция согласуется с вызовами сохранения UI при закрытии, `_finish_wallet_sweep_ui`, динамическим получением `close`/проверкой `callable` и float→round→int в converter. Часть прототипов Nuitka helpers Ghidra выводит неполно; пропущенные в псевдокоде аргументы проверяются по инструкциям и таблицам констант. Декомпилированный C не является восстановленным исходным Python. Совпадение адресов инструкций не доказывает семантическую/runtime-эквивалентность.

Первый проход имел неполные метаданные тел функций, хотя декомпилятор уже выводил код. В итоговом адресном проходе границы заданы явно; все семь exports успешны. Новых подтверждённых расхождений стратегии этот набор инструментов не выявил.

## Локальные файлы и повторение

Проект Ghidra, сырые JSON/логи, C/ASM, сигнатуры и списки версий зависимостей хранятся **вне Git**:

```text
~/.local/share/dipbot-re-audit/
  projects/DipBotStatic.gpr
  decompiled/
  capa-rules/
  capa-sigs/
  floss-static.json
  capa-file-api.json
  ghidra-analysis.log
  ghidra-analysis-initial.log
```

Скрипт Ghidra и пример запуска: [tools/ghidra/README.md](../tools/ghidra/README.md). Для повторения адресного прохода используйте существующий проект, `-process <имя EXE>` и `-noanalysis` вместо `-import`.

`tools/compare_disassembly.py` сверяет экспортированные листинги через Capstone/pefile; `tools/summarize_re_tools.py` формирует публичный отчёт по разрешённому списку строк, без полного дампа и декомпилированного кода. Для `tools/capa_file_audit.py` нужен Python из установленного окружения capa; добавлять эти зависимости в приложение не требуется.

Новые результаты не закрывают runtime Windows, аварийное восстановление оригинала, реальную DPAPI-совместимость и все косвенные вызовы. Дополнительные общий capa и FLOSS decoded проходы потребуют большего бюджета анализа либо дальнейшего сужения входа. Commit/push в этом проходе не выполнялись.
