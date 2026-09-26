"""Use-case capabilities. Services depend on these contracts, not the QThread class."""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any, Protocol, TypeVar

from dipbot.application.messages import EventKind
from dipbot.application.state import Connections, MarketState, RecordingState, SessionState
from dipbot.domain.records import PositionRecord
from dipbot.domain.strategy import D
from dipbot.execution.accounting import RateBook
from dipbot.market.chain import Chain, Pool
from dipbot.persistence.ports import StateStore

T = TypeVar("T")


class LogSignal(Protocol):
    def emit(self, message: str, /) -> None: ...


class ObservationRuntime(Protocol):
    def adaptive_market_price(self) -> D: ...
    def backup_price(self) -> D: ...
    def close_position(self, reason: str) -> None: ...

    connections: Connections

    def emit_event(self, name: str | EventKind, payload: Any) -> None: ...

    exit_return: str | None

    @property
    def log(self) -> LogSignal: ...

    market: MarketState

    def market_price(self) -> D: ...
    def observe(self, read_only: bool = False) -> None: ...
    def open_position(self) -> None: ...
    def paper_operation_cost(self) -> D: ...
    def position(self) -> PositionRecord: ...

    position_watch_error: str
    rates: RateBook

    def read_price(self, force_chain: bool = False) -> D: ...
    def record_market(self, kind: str, **data: Any) -> None: ...
    def record_quote(
        self,
        source: Chain | None,
        purpose: str,
        side: str,
        amount: int,
        output: int,
        reverse: int | None = None,
    ) -> None: ...
    def require_chain(self) -> None: ...

    session: SessionState
    stop_event: threading.Event
    store: StateStore


class PositionRuntime(Protocol):
    def backup_price(self) -> D: ...

    connections: Connections

    def emit_event(self, name: str | EventKind, payload: Any) -> None: ...
    def exit_read(self, read: Callable[[Chain | None], T], *, stopping: bool = False) -> T: ...

    exit_return: str | None

    @property
    def log(self) -> LogSignal: ...

    market: MarketState

    def paper_operation_cost(self) -> D: ...
    def position(self) -> PositionRecord: ...

    quit_event: threading.Event
    rates: RateBook

    def read_price(self, force_chain: bool = False) -> D: ...
    def record_market(self, kind: str, **data: Any) -> None: ...
    def record_quote(
        self,
        source: Chain | None,
        purpose: str,
        side: str,
        amount: int,
        output: int,
        reverse: int | None = None,
    ) -> None: ...
    def require_live(self) -> None: ...

    session: SessionState

    def set_position(self, amount: int, entry: D | int) -> None: ...
    def status(self) -> None: ...

    stop_event: threading.Event
    store: StateStore


class CommandRuntime(Protocol):
    def close_position(self, reason: str) -> None: ...
    def configure(self, data: dict[str, Any]) -> None: ...

    connections: Connections

    def discovery_current(self, generation: int | None) -> bool: ...
    def discovery_emit(self, generation: int | None, name: str | EventKind, value: Any) -> None: ...
    def emit_event(self, name: str | EventKind, payload: Any) -> None: ...

    @property
    def log(self) -> LogSignal: ...

    market: MarketState

    def open_position(self) -> None: ...
    def position(self) -> PositionRecord: ...
    def position_key(self) -> str: ...

    rates: RateBook

    def read_price(self, force_chain: bool = False) -> D: ...

    recording: RecordingState

    def require_chain(self) -> None: ...
    def require_live(self) -> None: ...
    def select_pool(self, pool: Pool, *, generation: int | None = None) -> None: ...

    session: SessionState
    stop_event: threading.Event
    store: StateStore

    def sweep(self) -> None: ...
