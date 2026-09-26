"""Public per-wallet target registry, independent of transaction journal."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from dipbot.market.autopair import Candidate
    from dipbot.market.chain import Pool
    from dipbot.market.discovery import Resolution
    from dipbot.persistence.ports import StateStore

import time
from copy import deepcopy
from dataclasses import asdict

from dipbot.market.chain import address
from dipbot.persistence.dynamic import persist


def records(store: StateStore, owner: str) -> dict[str, Any]:
    registry = store.data.get("wallet_tokens", {"version": 1, "wallets": {}})
    if (
        not isinstance(registry, dict)
        or registry.get("version") != 1
        or not isinstance(registry.get("wallets"), dict)
    ):
        raise ValueError("Повреждён реестр токенов кошельков")
    rows = registry["wallets"].get(address(owner).lower(), {})
    if not isinstance(rows, dict):
        raise ValueError("Повреждён список токенов кошелька")
    for token, row in rows.items():
        if (
            not isinstance(row, dict)
            or token != address(row.get("address", "")).lower()
            or row.get("router") not in ("V2", "V3")
        ):
            raise ValueError("Повреждён маршрут токена кошелька")
    return rows


def register(store: StateStore, owner: str, pool: Pool, pair_name: str) -> None:
    old = records(store, owner).get(pool.token.lower())
    if old and old.get("pool") == asdict(pool) and old["pair_name"] == pair_name:
        return
    updated = deepcopy(store.data)
    wallets = updated.setdefault("wallet_tokens", {"version": 1, "wallets": {}})["wallets"]
    wallets.setdefault(address(owner).lower(), {})[pool.token.lower()] = {
        "address": pool.token,
        "router": pool.router,
        "pair_name": pair_name,
        "last_seen": int(time.time()),
        "pool": asdict(pool),
    }
    persist(store, updated)


def choose_registered(result: Resolution, record: dict[str, Any]) -> Candidate | None:
    if result.state == "RESOLVED" and result.selected and result.selected.ready:
        return result.selected
    candidates = [
        c
        for c in result.candidates
        if c.ready and c.pool.router == record["router"] and c.pair_name == record["pair_name"]
    ]
    return max(candidates, key=lambda c: c.liquidity_score) if candidates else None


def ordered_bases(catalog: dict[str, str]) -> list[tuple[str, str]]:
    """Group aliases by address; native order is (WBNB in aliases, first.casefold())."""
    grouped: dict[str, list[str]] = {}
    for name, token in catalog.items():
        grouped.setdefault(address(token), []).append(name)
    return [
        (names[0], token)
        for token, names in sorted(
            grouped.items(), key=lambda item: ("WBNB" in item[1], item[1][0].casefold())
        )
    ]


def base_router(pair_name: str, token: str, catalogs: dict[str, dict[str, str]]) -> str:
    """Native Sweep prefers an installed V2 base profile, then V3."""
    token = address(token)
    for router in ("V2", "V3"):
        value = catalogs[router].get(pair_name)
        if value is not None and address(value) == token:
            return router
    raise ValueError("Профиль базы больше не установлен")
