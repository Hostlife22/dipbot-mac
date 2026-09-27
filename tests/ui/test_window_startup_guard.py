"""Invalid Qt callers must fail in Python instead of aborting the interpreter."""

import os
import subprocess
import sys

import pytest


@pytest.mark.parametrize(
    "setup", ["", "from PySide6.QtCore import QCoreApplication; app=QCoreApplication([])"]
)
def test_non_gui_application_cannot_construct_window(setup):
    script = (
        setup
        + """
from dipbot.ui.window import Window
try:
    Window()
except RuntimeError as error:
    assert 'QApplication' in str(error)
else:
    raise AssertionError('Invalid Qt setup was accepted')
"""
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        timeout=20,
        env={**os.environ, "QT_QPA_PLATFORM": "offscreen"},
    )
    assert result.returncode == 0, result.stderr.decode()


def test_window_from_worker_thread_is_rejected_before_qwidget_init():
    script = """
import threading
from PySide6.QtWidgets import QApplication
from dipbot.ui.window import Window
app=QApplication([])
errors=[]
def wrong_thread():
    try:
        Window()
    except RuntimeError as error:
        errors.append(str(error))
t=threading.Thread(target=wrong_thread);t.start();t.join(5)
assert not t.is_alive() and errors and 'QApplication thread' in errors[0]
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        timeout=20,
        env={**os.environ, "QT_QPA_PLATFORM": "offscreen"},
    )
    assert result.returncode == 0, result.stderr.decode()
