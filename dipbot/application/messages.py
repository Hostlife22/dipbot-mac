"""Typed envelopes at the queue/Qt boundary; credentials are excluded from repr."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Generic, Mapping, TypedDict, TypeVar, cast

from dipbot.domain.records import (
    ExitRetry,
    OpenEstimate,
    PaperSettings,
    RpcHealthRow,
    TradeDetail,
    UsdSummary,
)


class CommandKind(StrEnum):
    CONNECT = "connect"
    WALLET = "wallet"
    DISCOVER = "discover"
    COMPARE_ROUTES = "compare_routes"
    VERIFY = "verify"
    SELECT = "select"
    ADD_PROFILE = "add_profile"
    REMOVE_PROFILE = "remove_profile"
    START = "start"
    BUY = "buy"
    SELL = "sell"
    ACCOUNTING_REPORT = "accounting_report"
    BALANCE = "balance"
    CONVERT = "convert"
    SWEEP = "sweep"
    CANCEL_PENDING = "cancel_pending"
    COMPARE_POSITIONS = "compare_positions"
    RECONCILE = "reconcile"
    UNLOCK = "unlock"


class EventKind(StrEnum):
    ACCOUNTING_REPORT = "accounting_report"
    BALANCES = "balances"
    BUSY = "busy"
    DISCOVERY_EVENT = "discovery_event"
    ENTRY_CHECK = "entry_check"
    ERROR = "error"
    EXIT_RETRY = "exit_retry"
    POOLS = "pools"
    POSITION_COMPARISON = "position_comparison"
    POSITION_COMPARISON_ERROR = "position_comparison_error"
    PRICE = "price"
    PRICE_CONTEXT = "price_context"
    PROFILE_REMOVED = "profile_removed"
    PROFILES = "profiles"
    RECEIPT_REVIEW = "receipt_review"
    SELECTED = "selected"
    STATUS = "status"
    TRADE_MARKER = "trade_marker"
    WALLET = "wallet"
    AUTOPAIR = "autopair"
    ROUTE_COMPARISON_ERROR = "route_comparison_error"
    ROUTE_COMPARISON = "route_comparison"
    SWEEP_REPORT = "sweep_report"


class CommandPayload(TypedDict, total=False):
    mode: str
    rpc: str
    backup_rpc: str
    send_rpc: str
    ws_rpc: str
    save: bool
    key: str
    token: str
    quote: str
    router: str
    pool: object
    pools: list[object]
    generation: int | None
    settings: dict[str, object]
    gas: str
    interval: str
    amount: str
    buy: bool
    wallet: str
    symbol: str
    expected_hash: str
    expected_gas_price: int
    adaptive_rpc: bool
    record_market: bool
    signal_policy: dict[str, object]
    sizing: dict[str, object]
    entry_cost_policy: dict[str, object]
    cost_policy: dict[str, object]
    paper_policy: dict[str, object]
    exit_policy: dict[str, object]
    maximum: object
    reference: object


class StatusPayload(TypedDict, total=False):
    running: bool
    mode: str
    position: str
    base: str
    entry: str
    realized: str
    levels: dict[str, str]
    waiting: str
    halt_reason: str
    quote_unavailable: bool
    exit_retry: ExitRetry | None
    trade_detail: TradeDetail | None
    open_estimate: OpenEstimate | None
    position_watch_error: str
    rpc_health: list[RpcHealthRow]
    exit_basis: str
    exit_return: str | None
    signal_mode: str
    signal_notice: str
    wait_reason: str
    effective_dip: str
    base_reason: str
    base_age: float | None
    paper_cost_model: PaperSettings
    historical_usd: UsdSummary
    pnl_quote: str
    entry_notice: str
    locked: bool


@dataclass(frozen=True, slots=True)
class Command:
    kind: CommandKind
    payload: Mapping[str, object] = field(repr=False)

    @classmethod
    def from_wire(cls, name: str | CommandKind, payload: Mapping[str, object]) -> Command:
        kind = CommandKind(name)
        if not isinstance(payload, Mapping):
            raise ValueError("Повреждены данные команды")
        if any(not isinstance(key, str) for key in payload):
            raise ValueError("Имена полей команды должны быть строками")
        if payload.keys() - CommandPayload.__annotations__.keys():
            raise ValueError("Неизвестное поле команды")
        if "mode" in payload and payload["mode"] not in ("DEMO", "PAPER", "LIVE"):
            raise ValueError("Неизвестный режим торговли")
        generation = payload.get("generation")
        if generation is not None and type(generation) is not int:
            raise ValueError("Некорректное поколение поиска")
        # Nested UI settings must not change after enqueueing.
        return cls(kind, MappingProxyType(deepcopy(dict(payload))))

    def data(self) -> CommandPayload:
        return cast(CommandPayload, dict(self.payload))


Payload = TypeVar("Payload")


@dataclass(frozen=True, slots=True)
class Event(Generic[Payload]):
    kind: EventKind
    payload: Payload

    @classmethod
    def from_wire(cls, name: str | EventKind, payload: Payload) -> Event[Payload]:
        return cls(EventKind(name), payload)
