import os
import pytest
import requests

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(autouse=True)
def no_http(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("Network forbidden in automated tests")
    monkeypatch.setattr(requests.Session, "request", denied)


pytest_plugins = ['tests.support.execution', 'tests.support.ui']


@pytest.fixture(scope="session", autouse=True)
def qt_application():
    """Keep one GUI application alive even when worker tests run first."""
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app
    app.processEvents()
