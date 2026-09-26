"""Bounded historical Swap retrieval, independent of current-price trading."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Self

if TYPE_CHECKING:
    from dipbot.market.chain import Chain, Pool

import threading

from dipbot.market.activity import read_swaps
from dipbot.market.chain import Chain


class GapRecovery:
    def __init__(
        self, endpoint: str, pool: Pool, start: int, end: int, *, factory: Callable[[], Chain] | None = None
    ) -> None:
        self.pool = pool
        self.start_block, self.end_block = max(start, end - 31, 0), end
        self.truncated = self.start_block > start
        self.factory = factory or (lambda: Chain(endpoint, request_timeout=2))
        self.stop_event = threading.Event()
        self.result: dict[str, Any] | None = None
        self.thread: threading.Thread | None = None

    def start(self) -> Self:
        self.thread = threading.Thread(target=self.run, name="swap-gap-recovery", daemon=True)
        self.thread.start()
        return self

    def run(self) -> None:
        if self.stop_event.is_set():
            return
        result: dict[str, Any] = {
            "pool": self.pool.address,
            "from_block": self.start_block,
            "to_block": self.end_block,
            "truncated": self.truncated,
        }
        try:
            chain = self.factory()
            chain.restrict_to_reads()
            chain.check()
            if self.stop_event.is_set():
                return
            header = chain.w3.eth.get_block(self.end_block)
            if header["number"] != self.end_block:
                raise ValueError("Wrong backfill block")
            events = read_swaps(chain, self.pool, self.start_block, self.end_block, header, limit=512)
            result.update(events=events, count=len(events), error_type=None)
        except Exception as exc:
            result.update(error_type=type(exc).__name__, events=[], count=None)
        if not self.stop_event.is_set():
            self.result = result

    def stop(self) -> None:
        self.stop_event.set()
        if self.thread is not None:
            self.thread.join(timeout=0.1)
