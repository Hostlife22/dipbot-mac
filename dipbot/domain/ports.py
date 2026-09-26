"""Pure read contracts used by sizing and cost policies."""

from typing import Protocol

from dipbot.domain.records import RateMark


class RateSource(Protocol):
    def snapshot(self, token: str) -> RateMark | None: ...


class QuoteAsset(Protocol):
    @property
    def quote(self) -> str: ...
    @property
    def quote_decimals(self) -> int: ...
