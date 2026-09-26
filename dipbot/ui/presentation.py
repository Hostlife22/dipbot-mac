"""UI-owned data, independent of Qt widgets and the execution runtime.

Window aliases remain for audit adapters; controllers use presentation directly.
A descriptor forwards aliases to the one state object, never copying values.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Generic, Protocol, TypeVar, cast, overload

from dipbot.application.messages import StatusPayload
from dipbot.domain.records import ExitRetry


@dataclass(slots=True)
class PresentationState:
    busy: bool = False
    searching: bool = False
    running: bool = False
    stop_pending: bool = False
    active_mode: str = "DEMO"
    locked: bool = False
    auto_generation: int = 0
    selection_ready: bool = False
    ui_command: str = ""
    base_price: Decimal | None = None
    last_price: Decimal | None = None
    last_quote_at: float | None = None
    display_position: Decimal = Decimal(0)
    display_unit: str = "условных единиц (DEMO)"
    price_source: str = "DEMO"
    market_block: int | None = None
    market_block_timestamp: int | None = None
    market_rpc_source: str = "BSC"
    same_block_cache: bool = False
    quote_unavailable: bool = False
    exit_retry: ExitRetry | None = None
    entry_notice: str = ""
    signal_notice: str = ""
    wait_reason: str = ""
    halt_reason: str = ""
    ui_error: str = ""
    pnl_status: StatusPayload = field(default_factory=StatusPayload)
    pair_amounts: dict[str, str] = field(default_factory=dict)
    usd_pair_amounts: dict[str, str] = field(default_factory=dict)
    amount_key: str = ""
    amount_currency: str = "quote"
    _monitor_revision: tuple[int, int] | None = None
    _recovery_signature: str = ""


class StateOwner(Protocol):
    presentation: PresentationState


T = TypeVar("T")


class StateField(Generic[T]):
    """Compatibility attribute whose only storage is PresentationState."""

    def __init__(self, name: str) -> None:
        if name not in PresentationState.__dataclass_fields__:
            raise ValueError("Unknown presentation field")
        self.name = name

    @overload
    def __get__(self, instance: None, owner: type | None = None) -> StateField[T]: ...
    @overload
    def __get__(self, instance: StateOwner, owner: type | None = None) -> T: ...
    def __get__(self, instance: StateOwner | None, owner: type | None = None) -> StateField[T] | T:
        if instance is None:
            return self
        return cast(T, getattr(instance.presentation, self.name))

    def __set__(self, instance: StateOwner, value: T) -> None:
        setattr(instance.presentation, self.name, value)


class PresentationAccess:
    presentation: PresentationState
    busy = StateField[bool]("busy")
    searching = StateField[bool]("searching")
    running = StateField[bool]("running")
    stop_pending = StateField[bool]("stop_pending")
    active_mode = StateField[str]("active_mode")
    locked = StateField[bool]("locked")
    auto_generation = StateField[int]("auto_generation")
    selection_ready = StateField[bool]("selection_ready")
    ui_command = StateField[str]("ui_command")
    base_price = StateField[Decimal | None]("base_price")
    last_price = StateField[Decimal | None]("last_price")
    last_quote_at = StateField[float | None]("last_quote_at")
    display_position = StateField[Decimal]("display_position")
    display_unit = StateField[str]("display_unit")
    price_source = StateField[str]("price_source")
    market_block = StateField[int | None]("market_block")
    market_block_timestamp = StateField[int | None]("market_block_timestamp")
    market_rpc_source = StateField[str]("market_rpc_source")
    same_block_cache = StateField[bool]("same_block_cache")
    quote_unavailable = StateField[bool]("quote_unavailable")
    exit_retry = StateField[ExitRetry | None]("exit_retry")
    entry_notice = StateField[str]("entry_notice")
    signal_notice = StateField[str]("signal_notice")
    wait_reason = StateField[str]("wait_reason")
    halt_reason = StateField[str]("halt_reason")
    ui_error = StateField[str]("ui_error")
    pnl_status = StateField[StatusPayload]("pnl_status")
    pair_amounts = StateField[dict[str, str]]("pair_amounts")
    usd_pair_amounts = StateField[dict[str, str]]("usd_pair_amounts")
    amount_key = StateField[str]("amount_key")
    amount_currency = StateField[str]("amount_currency")
    _monitor_revision = StateField[tuple[int, int] | None]("_monitor_revision")
    _recovery_signature = StateField[str]("_recovery_signature")
