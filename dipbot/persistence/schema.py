"""Versioned public state. Loading never writes or drops financial records."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypedDict

CURRENT_VERSION = 1
VERSION_FIELD = "state_version"
State = dict[str, Any]


class PublicState(TypedDict, total=False):
    state_version: int
    wallet_address: str
    positions: dict[str, Any]
    operation: dict[str, Any] | None
    history: list[dict[str, Any]]
    closed_trades: dict[str, Any]
    gas_ledger: dict[str, Any]
    ui_preferences: dict[str, Any]
    dynamic_profiles: dict[str, str]
    dynamic_registry: dict[str, Any]
    wallet_tokens: dict[str, Any]


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
