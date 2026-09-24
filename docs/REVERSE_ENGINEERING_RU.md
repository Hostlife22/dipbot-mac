# Статический разбор NRNF DipBot v1.4.14

> Исторический отчёт. Актуальные исправления конвертера, STOP и журнала, а также оставшиеся различия: [PARITY_AUDIT_RU.md](PARITY_AUDIT_RU.md). Ниже описано состояние предыдущего этапа.

Дата: 2026-09-24. Анализировался только EXE и общедоступные инструкции релиза. Windows-программа не запускалась. `.nrnflic`, private key и данные кошельков не читались. Исходный релиз не изменён.

> Этот первоначальный отчёт дополнен [нативным аудитом](NATIVE_AUDIT_RU.md): восстановлены условия переноса базы, gap 0,55 с, формула snapshot minOut и другие параметры. Ниже сохранено описание первоначального этапа; раздел «Что не восстановлено» относится к нему.

## Что подтверждено

- PE machine `0x8664`, Windows x64, EXE 52 700 160 байт.
- Маркеры Nuitka (`__nuitka_version__`, loader, compiled module); PyInstaller cookie не найден.
- Рядом присутствуют Python 3.12 runtime, PyQt5, web3/eth-зависимости, win32api и win32file.
- В сегменте констант сохранились имена исходных модулей `bot.py`, `pair_profiles.py`, AutoPair, GUI, Trader, имена методов, параметров и строки интерфейса.
- Из блока `pair_profiles` извлечены 42 имени/адреса и подписи режимов конвертера. Это публичные константы, а не полностью восстановленные объекты Python.
- У `DipBot` найдены `dip_pct`, `tp_pct`, `sl_pct`, `slippage_pct`, `dynamic_dip_factor`, состояния monitoring/open/pending_buy/pending_sell и методы ожидания receipt.
- Найдена UI-формула `allowable BUY recovery = Slippage - Dynamic/100`.
- Строки показывают STOP с продажей позиции, ожидание pending BUY/SELL, остановку при SL и сброс базы после SELL confirm.
- Документы описывают DPAPI, Device ID, подписанную лицензию, HTTP DIRECT и интервалы V2 100 мс / V3 103 мс.

Смещения маркеров, SHA-256 EXE и адресов находятся в [REVERSE_EVIDENCE.json](REVERSE_EVIDENCE.json). Извлечение воспроизводится:

```bash
python3 tools/inspect_release.py /путь/к/DipBot.exe --output /tmp/REVERSE_EVIDENCE.json
```

## Что не восстановлено

- Полные тела Python-функций, исходная структура проекта и условия переходов стратегии.
- Точная логика обновления базы и `2 down moves`.
- Полная формула minOut по резервам, особенности V3 execution core и все константы защит.
- Поведение на реальных сделках и точная задержка цикла.
- Авторская система активации и DPAPI не переносились. В новом приложении они не требуются; оригинальный EXE и лицензия не модифицируются.

Наличие строк в бинарнике подтверждает названия и сообщения, но само по себе не доказывает точную исполняемую логику. Новый код не следует считать декомпиляцией оригинала.

## Реализация для macOS

Интерфейс создан на PySide6. Торговое взаимодействие написано по официальным контрактным интерфейсам PancakeSwap. Добавлены независимые DEMO/PAPER, Keychain и журнал намерений перед broadcast. Реальный режим реализован в новом коде; в блокчейне его исполнение не тестировалось.

Оставшиеся отличия от оригинала после повторного аудита: дополнительные RPC-котировки и preflight перед сделкой; последовательные многошаговые конвертации; последовательное чтение балансов; более строгая остановка Sweep при ошибке отправки. Поддержка всех прежних торговых сценариев один в один не подтверждена.

## Первичные источники контрактов и платформы

- [PancakeSwap V2 deployments](https://developer.pancakeswap.finance/contracts/v2/addresses)
- [PancakeSwap V3 deployments](https://developer.pancakeswap.finance/contracts/v3/addresses)
- [V3 ISwapRouter](https://github.com/pancakeswap/pancake-v3-contracts/blob/main/projects/v3-periphery/contracts/interfaces/ISwapRouter.sol)
- [V3 IQuoterV2](https://github.com/pancakeswap/pancake-v3-contracts/blob/main/projects/v3-periphery/contracts/interfaces/IQuoterV2.sol)
- [web3.py middleware](https://web3py.readthedocs.io/en/stable/middleware.html)
- [Qt for Python](https://doc.qt.io/qtforpython-6/)
- [Nuitka manual](https://nuitka.net/user-documentation/user-manual.html)

