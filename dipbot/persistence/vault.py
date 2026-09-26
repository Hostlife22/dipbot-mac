"""macOS Keychain adapter; no plaintext credential fallback."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from keyring.backends.macOS import Keyring

import sys


class Vault:
    service = "DipBotMac"

    @staticmethod
    def backend() -> Keyring:
        if sys.platform != "darwin":
            raise RuntimeError("LIVE-кошелёк доступен только через macOS Keychain")
        from keyring.backends.macOS import Keyring

        return Keyring()

    def save(self, name: str, value: str) -> None:
        self.backend().set_password(self.service, name, value)

    def get(self, name: str) -> str | None:
        return cast(str | None, self.backend().get_password(self.service, name))
