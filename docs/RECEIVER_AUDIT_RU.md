# Получатели вызовов и диагностика FLOSS — 2026-09-24

Mac-only: Windows EXE не исполнялся, сеть, кошельки и Keychain в этом проходе не использовались. Production-код и защиты Mac не менялись.

## Что добавлено

`tools/receiver_flow.py` отслеживает происхождение значений в регистрах и локальных ячейках стека по прямым рёбрам CFG. В точках слияния сохраняется только одинаковое значение; вызовы сбрасывают volatile-регистры, переданные наружу адреса стека инвалидируют сохранённые значения. Частичные записи в регистры также сбрасывают происхождение. Это ограниченный анализ, а не исполнение Python.

- 59 диапазонов, 1355 обращений к выбранным помощникам; для 575 восстановлена цепочка доступа к аргументу/атрибуту.
- `tools/method_factories.py` связал 198 квалифицированных имён с указателями тел в коротких фабриках Nuitka, вызывающих 0x1428ed190.
- `tools/link_receivers.py` сопоставил 91 обращение с возможным определением метода того же класса. Получение атрибута и непосредственный вызов различаются полем helper.

`arg[0]` означает элемент входного массива аргументов R8, а не автоматически установленный тип объекта. Связь с методом класса предполагает self; наследование, monkey-patching, динамические замены и конкретные runtime-объекты не разрешены. `getattr_default` допускает fallback: цепочка атрибута условна. Не моделируются heap aliases, полная семантика исключений и косвенные переходы. Переходы за пределы диапазонов перечислены явно; они есть в пяти диапазонах, включая GUI.stop и load_config_values. Такие диапазоны не считаются полными телами функций.

Свидетельства: [цепочки](RECEIVER_FLOW_EVIDENCE.json), [фабрики](METHOD_FACTORY_EVIDENCE.json), [кандидаты методов](RECEIVER_METHOD_LINKS.json).

## Конкретные связи и ограничения

| Область | Результат |
| --- | --- |
| Завершение | GUI.stop_bot обращается к self.bot.stop (0x140f57037). GUI.closeEvent останавливает self._balance_timer и обращается к self._save_ui_state (0x140f37ca6). CommercialGUI.closeEvent останавливает self._autopair_timer. WalletSweepEngine._close_trader получает необязательный close аргумента и вызывает его через call0 (0x142093d9d). Это не доказывает наличие Trader.close во всех runtime-объектах. |
| AutoPair | _schedule_autopair вызывает self._autopair_timer.stop/start. Смена пары/роутера обращается к _confirm_manual_selection; тот обновляет selection epoch, manual confirmation и вызывает _register_current_wallet_token (0x1409114d0). _apply_autopair_selection обращается к router_selector/pair_selector.setCurrentText. Эти связи не доказывают периодический опрос PENDING. |
| Sweep | В completion получатель необязательного reconcile_external_flat — self.bot; call0 по 0x140919673. Результат содержит remaining/skipped/failed. Анализ не устанавливает, что этот callback достижим при каждом результате или что он ведёт durable journal. |
| REMOVE | DynamicPairRegistry.remove вызывает self._records.pop (0x140bc1227), затем self._save (0x140bc126e). Фабрика связывает _save с телом 0x140bb9150. В нём прослежены поиск write_protected_json и подготовка path/REGISTRY_PURPOSE. Порядок pop/save подтверждён; отсутствие rollback во всём оригинале не доказано. |
| Настройки | GUI._save_ui_state получает self.ui_state_path.write_text (0x140f36b53); это отдельная цепочка от защищённого реестра. WalletTokenRegistry._read_document читает path.read_text и использует json.loads. GUI._reload_saved_settings вызывает проверку безопасности, пересоздание Trader, затем _load_settings_from_config. Полные пути reload_runtime_settings и обработки всех исключений не восстановлены. |

Новые версии защищённого формата или миграции не подтверждены. Наличие строки version/REGISTRY_VERSION в ветви чтения само по себе не является доказательством миграции. Системная совместимость DPAPI остаётся непроверенной.

## FLOSS: причина части сбоев и эксперимент

В установленной версии `floss.utils.make_emulator` использует taintbyte FE. `vivisect.impemu.emulator.readMemory` возвращает этот заполнитель при недоступном чтении; `writeMemory` в safe_mem может пропускать запись по недоступному адресу. Поэтому 0xfefefefefefefefe в адресе возврата — следствие ограничений эмуляции, а не извлечённое значение программы.

`extract_decoding_contexts` переиспользует один driver для разных caller sites. Наблюдатель `tools/floss_diagnose.py` не меняет PC и не подставляет Python-объекты. Дополнительный режим восстанавливает исходный снимок CPU/памяти перед каждым caller, чтобы не переносить конечное состояние предыдущего обхода.

| Проверка | Исходный режим | Свежий снимок на caller |
| --- | ---: | ---: |
| Контексты call0 | 8 | 23 |
| Ошибки возврата в диагностике call0 | 18 | 2 |
| Контексты восьми помощников | 361 | 426 |
| Hook restore failures полного прохода восьми помощников | 74 | 8 |
| Извлечённые stack/tight/decoded строки | 0 | 0 |

В двух оставшихся ошибках диагностики call0 стек перед вызовом не отображён в памяти. Все 23 callable-аргумента также вне отображённой памяти: это нули, заполнитель или tainted pointers. Состояние Python/Nuitka **не восстановлено**. Увеличение числа контекстов не означает появление корректных объектов. Восемь помощников — диспетчеры/атрибутные операции; они не установлены как самостоятельные строковые декодеры. Полноценная модель инициализации интерпретатора и объектов остаётся отдельной задачей.

Режим добавлен как опция `--fresh-callers`; исходный режим сохранён. Глобальные функции инструмента восстанавливаются в finally, установленные пакеты не изменены. [Результаты и хеши](FLOSS_RECEIVER_EVIDENCE.json).

## Проверки Mac

Семь новых сценариев в `tests/test_receiver_audit_scenarios.py` проверяют:

- удаление регистрации V3 сохраняет V2 того же базового актива после перезапуска;
- ошибка сохранения при удалении сохраняет оба профиля в памяти и на диске;
- повреждённый либо не являющийся объектом расшифрованный JSON отклоняется синтетическим codec;
- преобразование публичных Windows-настроек не изменяет исходный объект, сумма выбранной пары имеет приоритет.

Windows-порядок pop/save не переносился как требование ослабить rollback Mac. Синтетический codec не проверяет настоящий DPAPI.

Полный pytest: **290 passed**, 12.29 с; source offscreen GUI smoke: exit 0. Отдельно **6 тестов анализатора** на искусственном машинном коде прошли: слияние ветвей, clobber, частичные регистры, перенос self через стек. Bundle не пересобирался: production/packaging не менялись.

## Воспроизведение

Пути EXE/OUT задаются явно; сырые PE, workspace, ASM и логи хранятся вне Git. Для receiver/factory анализа нужен изолированный Python с Capstone/pefile; для FLOSS — окружение flare-floss из RE_TOOLING_RU.md.

```bash
python -m tools.receiver_flow "$EXE" --output "$OUT/flow.json"
python -m tools.method_factories "$EXE" --output "$OUT/factories.json"
python -m tools.link_receivers "$OUT/flow.json" "$OUT/factories.json" --output "$OUT/links.json"
python -m unittest discover -s tools/tests -v
python -m tools.floss_diagnose "$OUT/expanded.viv" --output "$OUT/diagnostic.json" --fresh-callers
python -m tools.context_re_analysis floss "$EXE" --root "$OUT" --rules "$RULES" --fresh-callers
uv run --frozen pytest -q
```

Для сравнения результатов использовать отдельные каталоги для исходного и fresh режима, с копией/ссылкой на один expanded.viv. Карта не является полным call graph. Следующие незакрытые задачи: глобальные lookup/вызовы с keyword/vector аргументами, вынесенные ветви исключений, связывание объектов Trader/bot с конкретными определениями и валидная модель Python для FLOSS. Runtime Windows, DPAPI и полное совпадение с оригиналом не подтверждены.
