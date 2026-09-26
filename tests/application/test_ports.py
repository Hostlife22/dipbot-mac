"""Use-case ports can be supplied without constructing RPC, Qt or a wallet."""

from threading import Event
from types import SimpleNamespace

from dipbot.application.sweep import SweepService
from dipbot.domain.strategy import Settings, Strategy
from dipbot.persistence.storage import Store


def test_stopped_sweep_with_injected_read_adapter_never_trades(tmp_path):
    store = Store(tmp_path / "state.json")
    stop = Event()
    stop.set()
    events = []

    def forbidden(*args, **kwargs):
        raise AssertionError("STOP must not read balances or start transactions")

    service = SweepService(
        store=store,
        chain=SimpleNamespace(balance=forbidden),
        live=SimpleNamespace(owner="0x" + "34" * 20, begin=forbidden),
        strategy=Strategy(Settings()),
        rates=SimpleNamespace(snapshot=forbidden),
        stop_event=stop,
        log=lambda _: None,
        emit=lambda name, value: events.append((name, value)),
        position=lambda: {},
    )
    service.run()
    assert events[-1][0] == "sweep_report"
    assert events[-1][1]["status"] == "stopped"
    assert events[-1][1]["sold"] == []
    assert not store.data.get("operation")
