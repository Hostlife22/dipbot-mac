#!/bin/zsh
set -eu
cd -- "${0:A:h}"
if [[ -x .venv/bin/python ]]; then
  exec .venv/bin/python launcher.py
elif command -v uv >/dev/null 2>&1; then
  exec uv run --frozen python launcher.py
else
  print 'Нужен uv или окружение .venv. См. README_RU.md.'
  read '?Нажмите Enter…'
fi

