"""Explicit differences, not an assertion of full Windows equivalence."""

import pytest

from dipbot.domain.strategy import D, minimum_out
from tools.native_models import converter_preview


@pytest.mark.parametrize(
    "router,amount,slip,native,mac",
    [
        ("V2", 10000, "2", 9300, 9800),
        ("V3", 10000, "2", 9800, 9800),
        ("V3", 10000, "0.005", 10000, 9999),
        ("V3", 10000, "0.015", 9998, 9998),
        ("V2", 10000, "20", 7500, 8000),
        ("V3", 10**24, "2", 98 * 10**22, 98 * 10**22),
    ],
)
def test_native_preview_versus_mac_exact_bound(router, amount, slip, native, mac):
    assert converter_preview(amount, slip, router)["min_out_raw"] == native
    assert minimum_out(amount, D(slip)) == mac


def test_one_raw_unit_native_clamps_mac_rejects_zero_output():
    assert converter_preview(1, 20, "V2")["min_out_raw"] == 1
    with pytest.raises(ValueError, match="нуля"):
        minimum_out(1, D(20))


def test_native_rounding_can_admit_value_above_mac_limit():
    assert converter_preview(10000, "20.004", "V3")["slippage_bps"] == 2000
    with pytest.raises(ValueError):
        minimum_out(10000, D("20.004"))


def test_dpapi_probe_refuses_non_windows_before_spawning(monkeypatch):
    from tools import windows_dpapi_probe as probe

    monkeypatch.setattr(probe.sys, "platform", "darwin")
    monkeypatch.setattr(probe.subprocess, "run", lambda *a, **k: pytest.fail("must not spawn"))
    with pytest.raises(RuntimeError, match="Windows"):
        probe.probe()
