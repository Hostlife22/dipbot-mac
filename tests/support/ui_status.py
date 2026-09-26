"""Shared ui_status fixtures/builders."""


def status(w, **changes):
    data = dict(
        mode=w.mode.currentText(),
        running=False,
        locked=False,
        position="0",
        base="1",
        realized="0",
        levels={},
    )
    data.update(changes)
    w.on_event("status", data)
