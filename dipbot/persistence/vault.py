"""macOS Keychain adapter; no plaintext credential fallback."""

import sys


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
