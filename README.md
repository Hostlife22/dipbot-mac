# DipBot Mac

Компактный торговый терминал для BSC на macOS: PancakeSwap V2/V3, AutoPair, DIP-вход, TP/SL, trailing, Converter и Sweep. Python / PySide6, хранение ключей в macOS Keychain.

Независимая реализация по документации и статическому разбору Windows-бота. Полное совпадение с оригиналом и прибыльность стратегии не подтверждены.

## Запуск

```bash
uv sync --frozen --python 3.12
uv run --frozen python launcher.py
```

| Режим | Котировки | Исполнение |
| --- | --- | --- |
| DEMO | Синтетические | Виртуальное, без RPC и кошелька |
| PAPER | Реальные BSC | Виртуальное, с ограничениями модели расходов |
| LIVE | Реальные BSC | Реальные транзакции |

Начните с DEMO → START BOT. Для PAPER настройте RPC и выберите пул. Единица AMOUNT определяется выбранным режимом суммы: база пары или USD.

## Документация

- [Руководство пользователя](README_RU.md): настройка, управление, восстановление.
- [Индекс документации](docs/README.md): архитектура, стратегия и ограничения проверки.
- [Разработка](CONTRIBUTING.md), [инструкции агентам](AGENTS.md), [безопасность](SECURITY.md).
- [Инструменты проверки](tools/README.md), [тесты](tests/README.md), [история изменений](CHANGELOG.md).

## Проверка и сборка

```bash
uv sync --frozen --group dev
uv run --frozen pytest -q
QT_QPA_PLATFORM=offscreen uv run --frozen python launcher.py --smoke-test
./BUILD_MAC.command
open "dist/DipBot Mac.app"
```

Локально проверялась Intel-сборка с подписью ad-hoc. Нативная Apple Silicon-сборка и notarization требуют отдельной проверки. Результаты тестов и пределы подтверждённого поведения — в [текущем статусе](docs/CURRENT_STATUS_RU.md).
