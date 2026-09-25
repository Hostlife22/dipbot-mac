import json
import os
from pathlib import Path
import sys
import tempfile

from .telemetry import timed
from .ledger_cache import Ledger


def data_dir():
    root = Path.home() / "Library/Application Support/DipBotMac" if sys.platform == "darwin" else Path.home() / ".local/share/dipbot-mac"
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    return root


class SaveAfterReplaceError(OSError):
    """New file is visible, but its crash durability could not be confirmed."""


class Store:
    def __init__(self, path=None):
        self.path = Path(path) if path else data_dir() / "state.json"
        self.data = json.loads(self.path.read_text()) if self.path.exists() else {}
        if not isinstance(self.data, dict):
            raise ValueError("Повреждён state.json; торговля заблокирована")

    def ledger(self, name):
        value = self.data.setdefault(name, {})
        if not isinstance(value, dict):
            raise ValueError('Повреждён финансовый журнал; торговля заблокирована')
        if not isinstance(value, Ledger):
            value = Ledger(value)
            self.data[name] = value
        return value

    @timed("storage.save")
    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        # Serialize once before touching disk; preserve the durable replace protocol.
        for name in ('closed_trades', 'gas_ledger'):
            if name in self.data:
                self.ledger(name)
        if any(not isinstance(key, str) for key in self.data):
            raise ValueError('Имена полей состояния должны быть строками')
        encode = lambda value: json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        # Serialize all fields before creating the temporary file. Reuse cached
        # ledger strings without joining/copying the entire large state in RAM.
        payload = ['{']
        for index,(key,value) in enumerate(self.data.items()):
            if index:
                payload.append(',')
            payload.extend((encode(key), ':',
                value.encoded() if isinstance(value, Ledger) else encode(value)))
        payload.append('}')
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


class Vault:
    service = "DipBotMac"

    @staticmethod
    def backend():
        if sys.platform != "darwin":
            raise RuntimeError("LIVE-кошелёк доступен только через macOS Keychain")
        from keyring.backends.macOS import Keyring
        return Keyring()

    def save(self, name: str, value: str):
        self.backend().set_password(self.service, name, value)

    def get(self, name: str):
        return self.backend().get_password(self.service, name)

