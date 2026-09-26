"""Reconcile recorded executions and cooldown; never equate smoke with coverage."""

import argparse
import json
from collections import Counter
from pathlib import Path

from tools.replay_market import load


def audit(header, events, expected_fills=None):
    errors = []
    fills, exits = Counter(), Counter()
    position = bool(header.get("starts_with_position"))
    cooldown = float(header.get("exit_policy", {}).get("cooldown_seconds", 0))
    last_sell = None
    last_sell_reason = None
    pending = None
    reentries = []
    executed_reentries = []
    natural_cycles = 0
    natural_entry = False
    for event in events:
        kind = event["event"]
        if kind == "signal":
            action = event["action"]
            pending = action
            if action == "BUY" and last_sell is not None:
                gap = event["t"] - last_sell
                reentries.append(gap)
                # Execution recording follows Strategy.sold by a few microseconds.
                if gap + 0.01 < cooldown:
                    errors.append("BUY signal before cooldown ended")
        elif kind == "execution":
            side = event["side"]
            fills[side] += 1
            if side == "BUY":
                if position:
                    errors.append("Second BUY while position open")
                natural_entry = pending == "BUY"
                if not natural_entry:
                    errors.append("BUY without recorded signal")
                if last_sell is not None:
                    executed_reentries.append(
                        {
                            "previous_exit": last_sell_reason,
                            "seconds_after_sell": event["t"] - last_sell,
                            "natural_signal": natural_entry,
                        }
                    )
                position = True
            elif side == "SELL":
                if not position:
                    errors.append("SELL without position")
                reason = event.get("reason", "UNKNOWN")
                exits[reason] += 1
                if reason not in ("STOP", "MANUAL"):
                    if pending != reason:
                        errors.append("SELL reason differs from recorded signal")
                    elif position and natural_entry:
                        natural_cycles += 1
                position = False
                natural_entry = False
                last_sell = event["t"]
                last_sell_reason = reason
            else:
                errors.append("Unknown execution side")
            pending = None
    if expected_fills is not None and dict(fills) != expected_fills:
        errors.append("Archive executions differ from observed trade markers")
    return {
        "consistent": not errors,
        "errors": errors,
        "fills": dict(fills),
        "exits": dict(exits),
        "open_position": position,
        "natural_completed_cycles": natural_cycles,
        "reentry_signal_gaps_seconds": reentries,
        "executed_reentries": executed_reentries,
        "cooldown_seconds": cooldown,
        "observed_natural_exits": sorted(set(exits) - {"STOP", "MANUAL"}),
        "limitations": "Recorded chronology only; no profitability, execution-price or Windows parity proof",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    header, events = load(args.archive, all_events=True)
    result = audit(header, events)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result))
    return 0 if result["consistent"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
