#!/bin/zsh
set -eu
cd -- "${0:A:h}"
uv sync --frozen --group dev
uv run --frozen pyinstaller --noconfirm --windowed --name 'DipBot Mac' \
  --osx-bundle-identifier local.dipbot.mac \
  --collect-data dipbot --hidden-import keyring.backends.macOS \
  --recursive-copy-metadata web3 --recursive-copy-metadata keyring --copy-metadata py_ecc \
  --collect-submodules eth_keys.backends \
  --exclude-module PySide6.QtWebEngineCore --exclude-module PySide6.QtWebEngineWidgets \
  launcher.py
print 'Готово: dist/DipBot Mac.app'
