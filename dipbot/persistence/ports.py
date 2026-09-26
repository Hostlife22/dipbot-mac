"""Storage contract shared by execution and orchestration."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from dipbot.persistence.ledger_cache import Ledger


class StateStore(Protocol):
    path: Path
    data: dict[str, Any]

    def save(self) -> None: ...

    def ledger(self, name: str) -> Ledger: ...
