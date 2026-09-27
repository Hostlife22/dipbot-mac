"""Packaged PAPER audits must not silently substitute more sensitive entry settings."""

import sys

import pytest

from dipbot import bootstrap
from dipbot.checks import token_ui_paper


def test_packaged_audit_uses_application_defaults_and_preserves_failure(
    tmp_path, monkeypatch, qt_application
):
    received = {}

    def run(*args, **kwargs):
        received.update(kwargs)
        return 2

    monkeypatch.setattr(token_ui_paper, "run", run)
    monkeypatch.setattr(bootstrap, "QApplication", lambda args: qt_application)
    monkeypatch.setattr(
        sys, "argv", ["dipbot", "--market-paper-token", "synthetic", "--market-paper-output", str(tmp_path)]
    )
    assert bootstrap.main() == 2
    assert {key: received[key] for key in ("dip", "take_profit", "stop_loss", "slippage", "dynamic")} == {
        "dip": "10",
        "take_profit": "15",
        "stop_loss": "15",
        "slippage": "5",
        "dynamic": "120",
    }
    assert received["close_after"] and received["exercise_recovery"]


def test_packaged_audit_honours_explicit_dip(tmp_path, monkeypatch, qt_application):
    received = {}
    monkeypatch.setattr(token_ui_paper, "run", lambda *args, **kw: received.update(kw))
    monkeypatch.setattr(bootstrap, "QApplication", lambda args: qt_application)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "dipbot",
            "--market-paper-token",
            "synthetic",
            "--market-paper-output",
            str(tmp_path),
            "--market-paper-automatic-only",
            "--market-paper-continue-after-sl",
            "--market-paper-cooldown",
            "3",
            "--market-paper-trailing",
            "3",
            "--market-paper-dip",
            "12",
            "--market-paper-amount-usd",
            "20",
        ],
    )
    assert bootstrap.main() == 0
    assert received["dip"] == "12" and received["take_profit"] == "15"
    assert received["automatic_only"] and not received["exercise_recovery"]
    assert received["continue_after_sl"] is True and received["cooldown"] == 3
    assert received["amount_usd"] == "20"
    assert received["trailing"] == 3


@pytest.mark.parametrize("amount", ["0", "-1", "NaN", "Infinity"])
def test_invalid_virtual_amount_fails_before_startup(tmp_path, amount):
    directory = tmp_path / "audit"
    with pytest.raises(ValueError, match="finite and positive"):
        token_ui_paper.run("synthetic", directory, 600, amount_usd=amount)
    assert not directory.exists()
