import json
import os
from pathlib import Path
import sys
import tempfile


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

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".state-")
        replaced = False
        try:
            with os.fdopen(fd, "w") as stream:
                json.dump(self.data, stream, ensure_ascii=False, indent=2)
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

