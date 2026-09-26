# Намеренные отличия защит Mac

2026-09-24. «Windows» ниже означает восстановленное статическое правило, если явно не написано UNKNOWN. Это не протокол реального исполнения оригинала. Защиты Mac не ослаблены ради совпадения.

| Защита | Windows / степень подтверждения | Mac и влияние | Сравнительная проверка |
|---|---|---|---|
| Allowance | Sweep вызывает ensure_approve_max | Точное разрешение суммы; ненулевой allowance сначала сбрасывается. Может потребоваться дополнительный approve | `tests/execution/test_execution.py::test_approve_is_exact_and_resets_nonzero` |
| V2 tax buffer | В converter preview добавляются 500 bps | Дополнительные 5% не разрешаются; некоторые tax-токены могут отклоняться | `tests/application/test_protection_comparison.py::test_native_preview_versus_mac_exact_bound` |
| Округление slippage | Восстановленная модель float → round → bps | Decimal без перехода к float; на границе minOut может быть строже | Та же таблица в test_protection_comparison, включая 0.005% и 20.004% |
| Пыль minOut | Native preview ограничивает результат снизу 1 raw | Mac отклоняет результат округления до 0; не подставляет произвольный выход | `test_one_raw_unit_native_clamps_mac_rejects_zero_output` |
| Round-trip loss | Потеря округляется вниз до целых bps, лимит 1500 | Точные ≤15%; пограничный маршрут может отклоняться | `tests/application/test_swap.py::test_converter_rejects_actual_amount_roundtrip_loss`, `tests/domain/test_roundtrip_boundary_comparison.py` — точная граница, дробные bps и одна raw unit при 10^30 |
| Котировка после approve | Полное runtime-сопоставление UNKNOWN | Повторная котировка не снижает сохранённый minOut | `tests/application/test_swap.py` — snapshot/signal minimum, ухудшение цены во время approve |
| Остаток после receipt | В Sweep неизвестный финальный баланс сохраняется в remaining/failed | Receipt не считается доказательством нулевого баланса; неизвестный остаток сохраняет журнал и позицию | `tests/application/test_remaining_branches.py`, `tests/application/test_expanded_scenarios.py` |
| Журнал до broadcast | Эквивалентная durable-схема Windows UNKNOWN | Хеш сохраняется до отправки; при ошибке диска broadcast запрещён | `tests/execution/test_execution.py::test_hash_persisted_before_broadcast_and_confirmed`, `tests/execution/test_crash_boundaries.py`, `tests/execution/test_receipt_storage_faults.py` |
| Неопределённая транзакция | Полные Windows-ветви после отправки UNKNOWN | Timeout/потеря ответа блокируют новую операцию; reconcile не отправляет повторно | `test_timeout_survives_restart_and_prevents_duplicate`, `test_restart_reconcile_after_lost_response_never_resends` |
| REMOVE профиля | Найдены pop → save и выход из lock; полный rollback памяти UNKNOWN | Проверяются позиции/остатки; до replace сохраняется старый реестр, после replace сохраняется новое видимое состояние с ошибкой durability | `tests/market/test_autopair_dynamic.py`, `tests/persistence/test_catalog_commit_boundaries.py` |
| STOP / частичная ошибка Sweep | Подтверждена ветвь Exception → failed → continuing после _sell_target, который может ошибиться после receipt; полный граф UNKNOWN | После начала durable-операции ошибка не даёт безусловно продолжить следующий токен. STOP проверяется между операциями | `tests/application/test_expanded_scenarios.py::test_uncertain_first_target_prevents_second_send`, `test_stop_after_first_receipt_accounts_first_and_keeps_second` |
| Canonical pool / маршрут | Полная эквивалентность всех проверок UNKNOWN | Проверяются factory/pool, маршрут и повторяющиеся токены | `tests/market/test_chain.py::test_reject_forged_pool`, `tests/application/test_parity_audit.py` |
| Секреты и настройки | Windows защищённый формат DPAPI | Mac Keychain; публичный импорт не переносит секреты или LIVE-resume | `tests/persistence/test_preferences.py`, `tests/application/test_receiver_audit_scenarios.py` |

После ошибки fsync каталога нельзя гарантировать переживание внезапного отключения питания даже при согласованной памяти и видимом файле. Ошибка не подавляется. Отдельная защита незавершённой LIVE-операции в `LiveTrader.finish` намеренно сохраняет блокировку процесса и не заменяется политикой публичных настроек/каталога.
# Дополнение текущего прохода

После V3 swap Windows проверяет прирост WBNB `<= 0` в разобранной локальной ветви. Mac дополнительно требует фактический выход не ниже minOut и сохраняет журнал между swap и unwrap. Границы и сбои проверены в `tests/execution/test_converter_stage_faults.py`.

Windows `DynamicPairRegistry._load` в найденном Exception-handler очищает `_records` и записывает имя типа ошибки в `load_error`. Mac при неизвестной версии реестра отказывает в catalog/ADD/REMOVE, сохраняя память и файл; три сценария в `tests/persistence/test_catalog_commit_boundaries.py`. Это не доказательство Windows rollback после `_save`. [Адреса, доказательства и ограничения](https://github.com/Hostlife22/dipbot-mac/blob/89e3a4f/docs/UNWIND_CONVERTER_AUDIT_RU.md).
