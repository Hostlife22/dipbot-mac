"""Shared ui_status fixtures/builders."""
from dataclasses import replace
from decimal import Decimal as D
from types import SimpleNamespace
import pytest
from tests.support.markets import POOL
from dipbot.ui.window import QMessageBox
from dipbot.application.worker import Worker
from dipbot.persistence.storage import Store
from tests.support.worker import config


def status(w, **changes):
    data = dict(mode=w.mode.currentText(), running=False, locked=False,
                position='0', base='1', realized='0', levels={})
    data.update(changes)
    w.on_event('status', data)
