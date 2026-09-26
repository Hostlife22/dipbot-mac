"""Shared ui fixtures/builders."""

import pytest
from PySide6.QtWidgets import QMessageBox

from dipbot.application.worker import Worker
from dipbot.persistence.storage import Store
from dipbot.ui.window import Window


@pytest.fixture
def window(tmp_path, monkeypatch, qt_application):
    app = qt_application
    monkeypatch.setattr(Worker, "start", lambda self: None)
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: None)
    w = Window(Store(tmp_path / "state.json"))
    yield w
    w.autopair_timer.stop()
    w.deleteLater()
    app.processEvents()
