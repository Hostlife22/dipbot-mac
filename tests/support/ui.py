"""Shared ui fixtures/builders."""
import os
from types import SimpleNamespace
import pytest
from PySide6.QtWidgets import QApplication
from dipbot.ui.window import Window, QMessageBox
from dipbot.application.worker import Worker
from dipbot.persistence.storage import Store, SaveAfterReplaceError
from dipbot.persistence import dynamic
from tests.support.markets import POOL, TARGET, BASE, route


@pytest.fixture
def window(tmp_path, monkeypatch, qt_application):
    app = qt_application
    monkeypatch.setattr(Worker, 'start', lambda self: None)
    monkeypatch.setattr(QMessageBox, 'warning', lambda *args: None)
    w = Window(Store(tmp_path/'state.json'))
    yield w
    w.autopair_timer.stop()
    w.deleteLater()
    app.processEvents()
