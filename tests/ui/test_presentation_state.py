"""One UI state owner; no leakage between windows or worker event snapshots."""

from decimal import Decimal

from dipbot.ui.presentation import PresentationAccess, PresentationState
from tests.support.markets import POOL


def test_state_can_exist_without_widgets_and_has_independent_containers():
    first, second = PresentationState(), PresentationState()
    first.pair_amounts["V2:WBNB"] = "0.1"
    first.pnl_status["mode"] = "PAPER"
    assert not second.pair_amounts and not second.pnl_status
    assert second.last_price is None and not second.running


def test_window_aliases_and_controller_events_use_single_state(window):
    window.mode.setCurrentText("PAPER")
    window.on_event("selected", POOL)
    window.on_event("price", "0.0000000000123")
    assert window.presentation.last_price == Decimal("0.0000000000123")
    assert window.last_price == window.presentation.last_price
    window.busy = True
    assert window.presentation.busy
    window.presentation.busy = False
    assert not window.busy
    window.invalidate_discovery()
    assert window.presentation.last_price is None
    assert not window.presentation.selection_ready
    assert not window.chart.values
    assert "last_price" not in window.__dict__ and "busy" not in window.__dict__


def test_all_compatibility_aliases_forward_without_shadow_storage():
    owner = PresentationAccess()
    owner.presentation = PresentationState()
    for name in PresentationState.__dataclass_fields__:
        value = getattr(owner.presentation, name)
        assert getattr(owner, name) == value
        setattr(owner, name, value)
        assert name not in vars(owner)
