import os
import pytest
import requests

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(autouse=True)
def no_http(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("Network forbidden in automated tests")
    monkeypatch.setattr(requests.Session, "request", denied)

