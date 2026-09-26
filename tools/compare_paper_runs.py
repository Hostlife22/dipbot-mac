"""Compare finished UI/worker PAPER configurations and validated market archives."""
import argparse
from collections import Counter
from decimal import Decimal as D, InvalidOperation
import json
from pathlib import Path
import statistics

from dipbot.domain.strategy import Settings, Strategy
from dipbot.domain.signal_policy import SignalPolicy
from tools.replay_market import load
from tools.market_cycle_audit import audit


def equivalent(a, b):
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(equivalent(a[k], b[k]) for k in a)
    if isinstance(a, bool) or isinstance(b, bool):
        return type(a) is type(b) and a == b
    try:
        return D(str(a)) == D(str(b))
    except InvalidOperation:
        return a == b


def inspect(path, expected):
    header, events = load(path, all_events=True)
    result = audit(header, events, expected)
    observations = [r for r in events if r['event'] == 'observation']
    gaps = [b['t']-a['t'] for a, b in zip(observations, observations[1:])]
    settings = Settings(**{k: float(v) if k == 'max_gap' else D(str(v))
                           for k, v in header['settings'].items()})
    strategy = Strategy(settings, SignalPolicy.parse(header['signal_policy']))
    first = None
    for row in observations:
        action = strategy.observe(D(row['price']), row['t'],
            observation_id=(row.get('block'), row.get('block_hash'), D(row['price'])))
        if action == 'BUY':
            first = {'t': row['t'], 'price': row['price'], 'block': row.get('block')}
            break
    recorded = next((r for r in events if r['event'] == 'signal' and r['action'] == 'BUY'), None)
    matches = ((first is None and recorded is None) or
               (first is not None and recorded is not None and
                first['price'] == recorded['price'] and abs(first['t']-recorded['t']) < .05))
    result.update(observations=len(observations), distinct_prices=len({r['price'] for r in observations}),
                  gaps_over_limit=sum(g > settings.max_gap for g in gaps),
                  gap_median_seconds=statistics.median(gaps) if gaps else None,
                  first_signal_replay=first, first_signal_matches=bool(matches))
    return header, result


def compare(headless, ui):
    reports = [json.loads((p/'report.json').read_text()) for p in (headless, ui)]
    h, u = reports
    index = next(i for i, m in enumerate(h['markets']) if m['pool'].lower() == u['pool'].lower())
    a_path = headless/'market-recordings'/h['recordings'][index]['file']
    b_paths = list((ui/'market-recordings').glob('*-0.jsonl'))
    if len(b_paths) != 1:
        raise ValueError('Expected one autonomous UI session without restarts')
    a_header, a = inspect(a_path, h['markets'][index]['fills'])
    b_header, b = inspect(b_paths[0], dict(Counter(t['side'] for t in u['trades'])))
    keys = ('pool', 'settings', 'signal_policy', 'exit_policy', 'paper_policy',
            'entry_cost_policy', 'sizing', 'requested_amount')
    differences = [k for k in keys if not equivalent(a_header.get(k), b_header.get(k))]
    for key in ('interval', 'effective_interval', 'adaptive_rpc'):
        if key not in h['markets'][index] or key not in u or not equivalent(h['markets'][index][key], u[key]):
            differences.append(key)
    passed = (not differences and h.get('passed') and u.get('passed')
              and h.get('natural_signals_only') and u.get('automatic_only')
              and h.get('test_driver_restarts') == 0 and u.get('test_restarts') == 0
              and a['consistent'] and b['consistent'] and a['first_signal_matches'] and b['first_signal_matches'])
    return {'passed': bool(passed), 'configuration_differences': differences,
            'headless': a, 'ui': b, 'transactions_sent': 0,
            'limits': 'Independent RPC observations, not identical price streams. First-signal replay excludes execution filters. No full quote replay or profit proof.'}


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('headless', type=Path);p.add_argument('ui', type=Path)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    result = compare(args.headless, args.ui)
    args.output.write_text(json.dumps(result, indent=2)+'\n')
    print(json.dumps(result))
    raise SystemExit(0 if result['passed'] else 1)
