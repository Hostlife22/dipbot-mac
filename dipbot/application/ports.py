"""Structural ports for use cases; adapters do not inherit application classes."""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING, Callable, Protocol

from dipbot.domain.ports import RateSource as RateSource
from dipbot.domain.records import OperationRecord, PositionRecord
from dipbot.persistence.ports import StateStore as StateStore

if TYPE_CHECKING:
    from dipbot.market.chain import Pool
    from dipbot.market.discovery import Resolution

LogSink = Callable[[str], None]
EventSink = Callable[[str, object], None]
PositionReader = Callable[[], PositionRecord]


class SecretStore(Protocol):
    def get(self, name: str) -> str | None: ...
    def save(self, name: str, value: str) -> None: ...


class StopSignal(Protocol):
    def is_set(self) -> bool: ...


class MarketReader(Protocol):
    """Read operations only. Broadcasting belongs to the executor."""

    def balance(self, token: str, owner: str) -> int: ...
    def verify_pool(self, pool_address: str, target: str, *, require_liquidity: bool = True) -> Pool: ...
    def quote(self, pool: Pool, amount: int, buy: bool, *, block: int | str = "latest") -> int: ...
    def resolve_address(self, raw: str, catalogs: dict[str, dict[str, str]]) -> Resolution: ...


class TradeExecutor(Protocol):
    owner: str
    operation: OperationRecord | None
    trade_router: str | None

    def begin(self, description: str) -> None: ...
    def finish(self) -> None: ...
    def swap(
        self,
        pool: Pool,
        amount: int,
        buy: bool,
        tolerance: Decimal,
        *,
        signal_minimum: int | None = None,
        simulate: bool = False,
        deadline_seconds: int = 30,
        quote_reader: Callable[..., int] | None = None,
    ) -> int: ...
    def conversion_route(self, src: str, dest: str, amount: int) -> list[Pool]: ...
    def convert(self, quote: str, amount: int, buy: bool, slippage: Decimal) -> int: ...
