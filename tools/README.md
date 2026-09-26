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

`windows_dpapi_probe` проверяет системный DPAPI только на Windows. Проверки fork требуют локального узла; PAPER/read-only требуют RPC. `token_ui_paper_check` также вызывается приложением: не удаляйте его как одноразовый скрипт.

Вывод прогонов направляйте в `.local-artifacts/` или `/tmp`, без секретов. Старые анализаторы Nuitka/Ghidra/FLOSS и промежуточные отчёты доступны в Git на `89e3a4f`; они не нужны для запуска Mac-приложения.
