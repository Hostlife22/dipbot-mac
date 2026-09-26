"""Mac registry schema; Windows field semantics, without Windows vault migration."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from dipbot.market.chain import Pool
    from dipbot.persistence.ports import StateStore

import re
import time
from copy import deepcopy

from dipbot.domain.assets import ETH, FEES, USDT, WBNB
from dipbot.market.chain import address, profiles, route_path
from dipbot.persistence.storage import SaveAfterReplaceError

MODES = {"direct_v2", "direct_v3", "via_usdt_v3", "via_eth_v3", "native_wrap"}


def records(store: StateStore) -> dict[str, Any]:
    registry = store.data.get("dynamic_registry", {"version": 1, "records": {}})
    if not isinstance(registry, dict):
        raise ValueError("Повреждён реестр динамических профилей")
    if registry.get("version") != 1 or not isinstance(registry.get("records"), dict):
        raise ValueError("Повреждён реестр динамических профилей")
    result = registry["records"]
    for key, item in result.items():
        try:
            valid = (
                item["trade_router"] in ("V2", "V3")
                and key == item["trade_router"] + ":" + address(item["token_address"]).lower()
                and item["converter_mode"] in MODES
                and item["v3_quote_fee"] in FEES
                and 0 <= item["decimals"] <= 36
                and isinstance(item["name"], str)
                and (item["trade_fee"] in FEES if item["trade_router"] == "V3" else item["trade_fee"] == 0)
            )
            address(item["trade_pool"])
        except (KeyError, TypeError, AttributeError):
            valid = False
        if not valid:
            raise ValueError("Повреждён динамический профиль; повторная проверка обязательна")
    return cast(dict[str, Any], result)


def catalog(store: StateStore, router: str) -> dict[str, str]:
    registered = records(store)
    represented = {r["token_address"].lower() for r in registered.values()}
    # Old symbol->address entries remain router-agnostic until explicitly rechecked.
    legacy = {
        name: token
        for name, token in store.data.get("dynamic_profiles", {}).items()
        if token.lower() not in represented
    }
    current = {r["name"]: r["token_address"] for r in registered.values() if r["trade_router"] == router}
    return profiles() | legacy | current


def preference(store: StateStore, token: str, router: str | None = None) -> dict[str, Any] | None:
    token = address(token)
    matches = [
        r
        for r in records(store).values()
        if address(r["token_address"]) == token and (router is None or r["trade_router"] == router)
    ]
    if not matches:
        return None
    # Without trading context, do not guess between conflicting router preferences.
    values = {(r["converter_mode"], r["v3_quote_fee"]) for r in matches}
    if len(values) != 1:
        raise ValueError("Выберите router для динамического конвертера")
    mode, fee = next(iter(values))
    return {"converter_mode": mode, "converter_fee": fee}


def route_settings(route: list[Pool]) -> tuple[str, int]:
    tokens, _ = route_path(route)
    if tokens[0] != address(WBNB):
        raise ValueError("Нужен маршрут покупки базы за WBNB")
    if route[0].router == "V2":
        return "direct_v2", 500
    if len(route) == 1:
        return "direct_v3", route[0].fee
    if tokens[1] == address(USDT):
        return "via_usdt_v3", route[-1].fee
    if tokens[1] == address(ETH):
        return "via_eth_v3", route[-1].fee
    raise ValueError("Неизвестный мост динамического конвертера")


def persist(store: StateStore, updated: Any) -> None:
    previous = store.data
    store.data = updated
    try:
        store.save()
    except SaveAfterReplaceError:
        # Replacement already happened. Keep the visible new state, report the
        # durability error, and do not pretend the on-disk change was rolled back.
        raise
    except Exception:
        store.data = previous
        raise


def safe_symbol(raw: object, token: str) -> str:
    if isinstance(raw, bytes):
        raw = raw.rstrip(b"\0").decode("utf-8", "ignore")
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "", str(raw or "")).upper()[:16]
    return cleaned or "PAIR_" + token[-4:].upper()


def upsert(
    store: StateStore, pool: Pool, route: list[Pool], loss_bps: int, *, symbol: Any = None
) -> dict[str, Any]:
    old = records(store)
    mode, fee = route_settings(route)
    if route_path(route)[0][-1] != address(pool.quote):
        raise ValueError("Маршрут не соответствует базе профиля")
    key = pool.router + ":" + pool.quote.lower()
    previous = old.get(key)
    names = set(profiles()) | set(store.data.get("dynamic_profiles", {}))
    name = previous["name"] if previous else safe_symbol(symbol, pool.quote)
    if not previous:
        occupied = {n.upper() for n in names}
        stem = name[:19] + "_" + pool.quote[-4:].upper()
        if name.upper() in occupied:
            name = stem[:24]
        counter = 2
        while name.upper() in occupied:
            suffix = "_" + str(counter)
            name = stem[: 24 - len(suffix)] + suffix
            counter += 1
    now = int(time.time())
    record = {
        "name": name,
        "token_address": pool.quote,
        "trade_router": pool.router,
        "trade_pool": pool.address,
        "trade_fee": pool.fee,
        "converter_mode": mode,
        "v3_quote_fee": fee,
        "converter_route": [p.address for p in route],
        "roundtrip_loss_bps": loss_bps,
        "decimals": pool.quote_decimals,
        "created_at": previous["created_at"] if previous else now,
        "updated_at": now,
    }
    updated = deepcopy(store.data)
    updated.setdefault("dynamic_registry", {"version": 1, "records": {}})["records"][key] = record
    flat = updated.setdefault("dynamic_profiles", {})
    # Replace only legacy aliases; keep separately registered routers visible.
    registered_names = {r["name"] for r in old.values()}
    for alias in list(flat):
        if flat[alias].lower() == pool.quote.lower() and alias not in registered_names:
            del flat[alias]
    flat[name] = pool.quote
    persist(store, updated)
    return record


def remove(store: StateStore, name: str) -> None:
    registered = records(store)
    updated = deepcopy(store.data)
    updated.get("dynamic_profiles", {}).pop(name, None)
    for key, record in registered.items():
        if record["name"] == name:
            del updated["dynamic_registry"]["records"][key]
    persist(store, updated)
