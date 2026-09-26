"""Storage contract shared by execution and orchestration."""

from pathlib import Path
from typing import Any, Protocol


class StateStore(Protocol):
    path: Path
    data: dict[str, Any]

    def save(self) -> None: ...
