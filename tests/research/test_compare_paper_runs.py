import json
from dataclasses import asdict

from dipbot.domain.signal_policy import SignalPolicy
from dipbot.domain.strategy import Settings
from dipbot.research.market_tape import MarketTape
from tools.compare_paper_runs import compare, inspect


def session(root, minimum=0):
    from decimal import Decimal as D

    tape = MarketTape(
        root / "market-recordings",
        {
            "mode": "PAPER",
            "pool": {"address": "pool"},
            "settings": asdict(Settings(min_swaps=D(minimum))),
            "signal_policy": SignalPolicy(mode="window").export(),
        },
    )
    tape.record("observation", price="100", block=1, block_hash="a")
    assert tape.close()
    return tape.path


def reports(tmp_path, minimum=0):
    a, b = tmp_path / "worker", tmp_path / "ui"
    tape = session(a)
    session(b, minimum)
    (a / "report.json").write_text(
        json.dumps(
            {
                "passed": True,
                "natural_signals_only": True,
                "test_driver_restarts": 0,
                "recordings": [{"file": tape.name}],
                "markets": [
                    {
                        "pool": "pool",
                        "fills": {},
                        "interval": 0.1,
                        "effective_interval": 0.103,
                        "adaptive_rpc": False,
                    }
                ],
            }
        )
    )
    (b / "report.json").write_text(
        json.dumps(
            {
                "passed": True,
                "automatic_only": True,
                "test_restarts": 0,
                "pool": "pool",
                "trades": [],
                "interval": 0.1,
                "effective_interval": 0.103,
                "adaptive_rpc": False,
            }
        )
    )
    return a, b


def test_matching_runs_do_not_invent_completed_cycles(tmp_path):
    result = compare(*reports(tmp_path))
    assert result["passed"]
    assert result["ui"]["natural_completed_cycles"] == 0


def test_different_activity_filter_is_not_a_matched_experiment(tmp_path):
    result = compare(*reports(tmp_path, 1))
    assert not result["passed"] and result["configuration_differences"] == ["settings"]


def test_missing_recorded_buy_is_detected_by_first_signal_replay(tmp_path):
    tape = MarketTape(
        tmp_path, {"settings": asdict(Settings()), "signal_policy": SignalPolicy(mode="window").export()}
    )
    tape.record("observation", price="100", block=1)
    tape.record("observation", price="90", block=2)
    assert tape.close()
    _, result = inspect(tape.path, {})
    assert result["first_signal_replay"] is not None
    assert not result["first_signal_matches"]
