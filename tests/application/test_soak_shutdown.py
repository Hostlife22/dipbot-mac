from pathlib import Path
from types import SimpleNamespace as NS

from PySide6.QtCore import QCoreApplication

from dipbot.application.worker import Worker
from dipbot.persistence.storage import Store
from tests.support.worker import config
from tools.paper_soak import recording_health, stop_workers


def test_operator_cleanup_closes_virtual_position_and_joins(tmp_path):
    app = QCoreApplication.instance() or QCoreApplication([])
    worker = Worker(Store(tmp_path / "state.json"))
    worker.command("buy", config())
    assert worker.paper.position > 0
    worker.start()
    result = stop_workers(app, [worker], timeout=5)
    assert result == {"workers_joined": True, "clean_stop": True}
    assert worker.paper.position == 0 and not worker.isRunning()


def test_archive_drops_are_not_a_successful_complete_recording():
    recorder = NS(
        completed=True,
        paths=[Path("synthetic.jsonl")],
        path=Path("synthetic.jsonl"),
        written=100,
        dropped=1,
        error_type="",
        thread=NS(is_alive=lambda: False),
    )
    assert not recording_health([NS(recorder=recorder)])["recordings_complete"]
    recorder.dropped = 0
    assert recording_health([NS(recorder=recorder)])["recordings_complete"]
    recorder.error_type = "OSError"
    assert not recording_health([NS(recorder=recorder)])["recordings_complete"]
    assert not recording_health([])["recordings_complete"]
