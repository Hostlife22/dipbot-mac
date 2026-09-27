"""Versioned public state. Loading never writes or drops financial records."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypedDict

from dipbot.domain.records import ClosedTrade, GasRecord, OperationRecord, PositionRecord
from dipbot.persistence.records import DynamicRegistry, PreferencesRecord, WalletRegistry

CURRENT_VERSION = 1
VERSION_FIELD = "state_version"
State = dict[str, Any]


class PublicState(TypedDict, total=False):
    state_version: int
    wallet_address: str
    positions: dict[str, PositionRecord]
    operation: OperationRecord | None
    history: list[OperationRecord]
    closed_trades: dict[str, ClosedTrade]
    gas_ledger: dict[str, GasRecord]
    ui_preferences: PreferencesRecord
    dynamic_profiles: dict[str, str]
    dynamic_registry: DynamicRegistry
    wallet_tokens: WalletRegistry


def validate_state(value: State) -> None:
    if type(value.get(VERSION_FIELD)) is not int or value[VERSION_FIELD] != CURRENT_VERSION:
        raise ValueError("Неизвестная версия state.json; торговля заблокирована")
    for key in (
        "positions",
        "closed_trades",
        "gas_ledger",
        "ui_preferences",
        "dynamic_profiles",
        "dynamic_registry",
        "wallet_tokens",
        "known_pools",
        "realized_quote",
    ):
        if key in value and not isinstance(value[key], dict):
            raise ValueError("Повреждена структура state.json; торговля заблокирована")
    if value.get("operation") is not None and not isinstance(value["operation"], dict):
        raise ValueError("Повреждён журнал операции; торговля заблокирована")
    if "history" in value and not isinstance(value["history"], list):
        raise ValueError("Повреждена история операций; торговля заблокирована")

    validate_records(value)
    validate_financial_ledgers(value)


def validate_records(value: State) -> None:
    """Validate present legacy fields without guessing defaults or dropping extensions."""

    def mapping(raw: object, label: str) -> dict[str, Any]:
        if not isinstance(raw, dict) or any(not isinstance(k, str) for k in raw):
            raise ValueError(f"Повреждена запись {label}; торговля заблокирована")
        return raw

    def fields(raw: dict[str, Any], expected: dict[str, tuple[type, ...]], label: str) -> None:
        for key, types in expected.items():
            if key in raw and type(raw[key]) not in types:
                raise ValueError(f"Повреждено поле {label}.{key}; торговля заблокирована")

    for raw in value.get("positions", {}).values():
        position = mapping(raw, "position")
        fields(
            position,
            {
                "amount": (int,),
                "entry": (str,),
                "peak_price": (str,),
                "opened_at": (float, int),
                "entry_cost_usd": (str, type(None)),
                "cost_quote": (str,),
            },
            "position",
        )
        if "pool" in position:
            pool = mapping(position["pool"], "pool")
            fields(
                pool,
                {
                    "address": (str,),
                    "token": (str,),
                    "quote": (str,),
                    "router": (str,),
                    "token_decimals": (int,),
                    "quote_decimals": (int,),
                    "fee": (int,),
                    "token_is_0": (bool,),
                },
                "pool",
            )
    operations = list(value.get("history", []))
    if value.get("operation") is not None:
        operations.append(value["operation"])
    for raw in operations:
        operation = mapping(raw, "operation")
        fields(
            operation,
            {
                "operation_id": (str,),
                "signal_cycle_id": (str,),
                "wallet": (str,),
                "description": (str,),
                "started": (int,),
                "transactions": (list,),
            },
            "operation",
        )
        for raw_transaction in operation.get("transactions", []):
            transaction = mapping(raw_transaction, "transaction")
            fields(
                transaction,
                {
                    "hash": (str,),
                    "transaction_id": (str,),
                    "signal_cycle_id": (str,),
                    "nonce": (int,),
                    "status": (str,),
                    "stage": (str,),
                    "block": (int,),
                    "gas_fee_wei": (int,),
                    "request": (dict,),
                },
                "transaction",
            )

            if "request" in transaction:
                fields(
                    transaction["request"],
                    {
                        "chainId": (int,),
                        "nonce": (int,),
                        "value": (int,),
                        "gas": (int,),
                        "gasPrice": (int,),
                        "to": (str,),
                    },
                    "request",
                )


def migrate_v0(value: State) -> State:
    # Existing records, unknown extension fields and pending intent are preserved.
    return {**value, VERSION_FIELD: 1}


MIGRATIONS: dict[int, Callable[[State], State]] = {0: migrate_v0}


def load_state(raw: object) -> State:
    if not isinstance(raw, dict):
        raise ValueError("Повреждён state.json; торговля заблокирована")
    version = raw.get(VERSION_FIELD, 0)
    if type(version) is not int or not 0 <= version <= CURRENT_VERSION:
        raise ValueError("Неизвестная версия state.json; торговля заблокирована")
    value: State = dict(raw)
    while version < CURRENT_VERSION:
        value = MIGRATIONS[version](value)
        version = value[VERSION_FIELD]
    validate_state(value)
    return value


# Validators accept sparse legacy records, but reject malformed known values.
def validate_financial_row(raw: object) -> None:
    from decimal import Decimal, InvalidOperation

    if not isinstance(raw, dict) or any(not isinstance(key, str) for key in raw):
        raise ValueError("Повреждена запись финансового журнала")
    for key in ("wallet", "pool", "token", "quote", "label", "status"):
        if key in raw and not isinstance(raw[key], str):
            raise ValueError(f"Повреждено поле финансового журнала: {key}")
    for key in ("wei", "usd", "cost_quote", "proceeds_quote", "entry_cost_usd", "proceeds_usd", "net_usd"):
        value = raw.get(key)
        if value is None:
            continue
        if not isinstance(value, str):
            raise ValueError(f"Денежное поле должно быть строкой: {key}")
        try:
            number = Decimal(value)
        except InvalidOperation as exc:
            raise ValueError(f"Некорректное денежное поле: {key}") from exc
        if not number.is_finite():
            raise ValueError(f"Нефинитное денежное поле: {key}")
    for key in ("block", "closed_at", "sold_raw", "residual_raw"):
        if raw.get(key) is not None and (type(raw[key]) is not int or raw[key] < 0):
            raise ValueError(f"Некорректный счётчик финансового журнала: {key}")
    if "inventory_matches" in raw and type(raw["inventory_matches"]) is not bool:
        raise ValueError("Некорректная сверка количества")
    for key in ("entry_gas_hashes", "exit_gas_hashes", "source_pools"):
        value = raw.get(key)
        if value is not None and (not isinstance(value, list) or any(not isinstance(v, str) for v in value)):
            raise ValueError(f"Повреждён список финансового журнала: {key}")
    for key in ("rate", "exit_rate", "exit_fees"):
        value = raw.get(key)
        if value is not None:
            validate_financial_row(value)


def validate_financial_ledgers(value: State) -> None:
    from dipbot.persistence.ledger_cache import Ledger

    for name in ("closed_trades", "gas_ledger"):
        ledger = value.get(name, {})
        if isinstance(ledger, Ledger):
            ledger.validate_rows(validate_financial_row)
        else:
            for row in ledger.values():
                validate_financial_row(row)
