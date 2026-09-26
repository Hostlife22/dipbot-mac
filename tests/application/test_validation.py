from copy import deepcopy
from decimal import Decimal as D

from dipbot.domain.strategy import Settings
from dipbot.research.validation import walk_forward


def samples():
    return [{"t": i * 0.5, "price": str(D(100) + (D(i % 10) / 10))} for i in range(1801)]


def test_holdout_prices_cannot_change_first_training_selection():
    rows = samples()
    first = walk_forward(rows, Settings(), folds=2)
    altered = deepcopy(rows)
    for row in altered:
        if row["t"] > first["folds"][0]["training_end"]:
            row["price"] = "1"
    second = walk_forward(altered, Settings(), folds=2)
    assert first["folds"][0]["training_scores"] == second["folds"][0]["training_scores"]
    assert first["folds"][0]["chosen"] == second["folds"][0]["chosen"]


def test_insufficient_trades_does_not_declare_winning_parameters():
    report = walk_forward(samples(), Settings())
    assert all(r["chosen"] is None for r in report["folds"])
    assert not report["profitability_proven"]
    assert not report["automatic_parameter_changes"]
    assert all(r["validation_start_exclusive"] > r["training_end"] + 90 for r in report["folds"])
