from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

from dipbot.observability.telemetry import timed
from dipbot.persistence.ledger_cache import Ledger
from dipbot.persistence.schema import load_state


def data_dir() -> Path:
    root = (
        Path.home() / "Library/Application Support/DipBotMac"
        if sys.platform == "darwin"
        else Path.home() / ".local/share/dipbot-mac"
    )
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    return root


class SaveAfterReplaceError(OSError):
    """New file is visible, but its crash durability could not be confirmed."""


class Store:
    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path) if path else data_dir() / "state.json"
        self.data = load_state(json.loads(self.path.read_text()) if self.path.exists() else {})

    def ledger(self, name: str) -> Ledger:
        value = self.data.setdefault(name, {})
        if not isinstance(value, dict):
            raise ValueError("Повреждён финансовый журнал; торговля заблокирована")
        if not isinstance(value, Ledger):
            value = Ledger(value)
            self.data[name] = value
        return value

    @timed("storage.save")
    def save(self) -> None:
        self.data = load_state(self.data)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        # Serialize once before touching disk; preserve the durable replace protocol.
        for name in ("closed_trades", "gas_ledger"):
            if name in self.data:
                self.ledger(name)
        if any(not isinstance(key, str) for key in self.data):
            raise ValueError("Имена полей состояния должны быть строками")
        encode = lambda value: json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        # Serialize all fields before creating the temporary file. Reuse cached
        # ledger strings without joining/copying the entire large state in RAM.
        payload = ["{"]
        for index, (key, value) in enumerate(self.data.items()):
            if index:
                payload.append(",")
            payload.extend((encode(key), ":"))
            payload.extend(value.encoded_parts() if isinstance(value, Ledger) else (encode(value),))
        payload.append("}")
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".state-")
        replaced = False
        try:
            with os.fdopen(fd, "w") as stream:
                stream.writelines(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(tmp, self.path)
            replaced = True
            directory = os.open(self.path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        except OSError as exc:
            if replaced:
                raise SaveAfterReplaceError(
                    "Файл заменён, но надёжность сохранения не подтверждена; нужна проверка состояния"
                ) from exc
            raise
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
