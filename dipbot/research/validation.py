"""Chronological selection with an embargo; validation never selects parameters."""

from __future__ import annotations

from decimal import Decimal as D
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from dipbot.domain.signal_policy import SignalPolicy
    from dipbot.domain.strategy import Settings
    from dipbot.research.replay import ReplayCosts

import math
from dataclasses import replace

from dipbot.domain.signal_policy import SignalPolicy
from dipbot.research.replay import ReplayCosts, replay


def walk_forward(
    samples: list[dict[str, Any]],
    settings: Settings,
    *,
    costs: ReplayCosts | None = None,
    folds: int = 3,
    minimum_closed: int = 3,
) -> dict[str, Any]:
    if type(folds) is not int or not 1 <= folds <= 10 or minimum_closed < 1:
        raise ValueError("Некорректные параметры проверки")
    if len(samples) < 100:
        raise ValueError("Недостаточно наблюдений")
    times = [float(row["t"]) for row in samples]
    if any(not math.isfinite(t) for t in times):
        raise ValueError("Некорректное время наблюдения")
    if any(b <= a for a, b in zip(times, times[1:])):
        raise ValueError("Нарушен порядок времени")
    costs = costs or ReplayCosts()
    candidates = [
        (f"{mode}:dip={dip}", replace(settings, dip=D(dip)), SignalPolicy(mode=mode))
        for mode in ("legacy", "window", "volatility")
        for dip in ("0.5", "1", "2", "3")
    ]
    begin, end = times[0], times[-1]
    span = end - begin
    if span < 300:
        raise ValueError("Для отделённых окон нужно хотя бы 300 секунд")
    # Fixed 60s window, 30s maximum simulated execution deadline, plus latency.
    embargo = 90 + costs.latency_seconds
    results = []
    for fold in range(folds):
        cut = begin + span * (0.5 + 0.5 * fold / folds)
        stop = begin + span * (0.5 + 0.5 * (fold + 1) / folds)
        training = [r for r in samples if float(r["t"]) <= cut]
        validation = [r for r in samples if cut + embargo < float(r["t"]) <= stop]
        scores = []
        for name, setting, policy in candidates:
            row = replay(training, setting, policy, costs)
            closed = sum(t["side"] != "BUY" for t in row["trades"])
            eligible = closed >= minimum_closed and not row["pending"] and D(row["open_quantity"]) == 0
            scores.append(
                {
                    "candidate": name,
                    "closed": closed,
                    "eligible": eligible,
                    "realized_quote": row["realized_quote"],
                    "drawdown_quote": row["max_drawdown_quote"],
                }
            )
        eligible = [r for r in scores if r["eligible"]]
        chosen = (
            max(eligible, key=lambda r: (D(r["realized_quote"]), -D(r["drawdown_quote"]), r["candidate"]))
            if eligible
            else None
        )
        result = {
            "fold": fold,
            "training_end": cut,
            "validation_start_exclusive": cut + embargo,
            "validation_end": stop,
            "training_scores": scores,
            "chosen": chosen["candidate"] if chosen else None,
            "validation": None,
        }
        if chosen and len(validation) >= 2:
            _, setting, policy = next(c for c in candidates if c[0] == chosen["candidate"])
            result["validation"] = replay(validation, setting, policy, costs)
        result["status"] = (
            "evaluated"
            if result["validation"] is not None
            else "insufficient_training_trades"
            if not chosen
            else "insufficient_holdout"
        )
        results.append(result)
    return {
        "model": "chronological_fixed_grid_v1",
        "folds": results,
        "embargo_seconds": embargo,
        "profitability_proven": False,
        "automatic_parameter_changes": False,
        "limitations": "Each split starts flat. No forced final sale. Fixed-cost model; correlated folds, small samples and token survivorship remain.",
    }
