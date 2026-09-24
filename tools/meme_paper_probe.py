"""Read-only meme-token market observations and explicitly synthetic PAPER cycles."""
import argparse
from collections import Counter
from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path
import statistics
import tempfile
import time
from types import SimpleNamespace
from unittest.mock import patch

from PySide6.QtCore import QCoreApplication

from dipbot.chain import Chain, WBNB, address
from dipbot.discovery import resolve
from dipbot.storage import Store, Vault
from dipbot.strategy import D, Settings
from dipbot.trader import LiveTrader
from dipbot.worker import Worker
from tools.read_only_probe import guard_provider

TOKENS = {
    'FLOKI': '0xfb5b838b6cfeedc2873ab27866079ac55363d37e',
    'BabyDoge': '0xc748673057861a797275cd8a068abb95a902e8de',
    'CHEEMS': '0x0df0587216a4a1bb7d5082fdc491d93d2dd4b413',
}
ENDPOINT = 'https://bsc-rpc.publicnode.com'
CATALOGS = {router: {'WBNB': WBNB} for router in ('V2', 'V3')}


def forbidden(*args, **kwargs):
    raise RuntimeError('Wallet access and live submission forbidden in this probe')


def config(pool):
    settings = Settings(amount=D('0.00003'))
    return {'mode': 'PAPER', 'settings': {k: str(v) for k, v in asdict(settings).items()
                                       if k != 'max_gap'},
            'interval': .1, 'gas': '0.1', 'token': pool.token,
            'pool': pool.address, 'router': pool.router}


def synthetic_cycles(directory, pool, base):
    results = []
    for end, reason in [('0.99', 'TAKE_PROFIT'), ('0.90', 'STOP_LOSS')]:
        worker = Worker(Store(directory / (reason + '.json')))
        state = {'price': base, 'now': 1.0}
        worker.pool = pool
        worker.chain = SimpleNamespace(verify_pool=lambda *a: pool,
                                       price=lambda p: state['price'])
        logs = []
        worker.log.connect(logs.append)
        with patch('dipbot.worker.time.monotonic', lambda: state['now']):
            worker.command('start', config(pool))
            for factor in ['1', '0.96', end]:
                state['now'] += .1
                state['price'] = base * D(factor)
                worker.observe()
                if factor == '0.96':
                    assert worker.paper.position > 0
            assert not worker.paper.position and worker.strategy.entry is None
            assert worker.strategy.stopped == (reason == 'STOP_LOSS')
            assert sum('PAPER BUY:' in line for line in logs) == 1
            assert any('SELL: ' + reason in line for line in logs)
        results.append({'synthetic': True, 'exit': reason, 'passed': True})
    return results


def main(output, seconds, tokens=None, endpoint=ENDPOINT):
    tokens = TOKENS if tokens is None else tokens
    app = QCoreApplication.instance() or QCoreApplication([])
    report = {'utc': datetime.now(timezone.utc).isoformat(), 'mode': 'PAPER',
              'endpoint': endpoint, 'interval_s': .1, 'max_gap_s': .55,
              'dip_pct': 3, 'tp_pct': 2, 'sl_pct': 2,
              'transactions_sent': 0, 'tokens': []}
    sessions = []
    with tempfile.TemporaryDirectory() as temporary, \
            patch.object(Vault, 'get', forbidden), patch.object(Vault, 'save', forbidden), \
            patch.object(LiveTrader, 'send', forbidden):
        directory = Path(temporary)
        try:
            for name, token in tokens.items():
                row = {'name': name, 'token': token}
                report['tokens'].append(row)
                try:
                    chain = Chain(endpoint)
                    calls = guard_provider(chain.w3.provider)
                    resolution = resolve(chain, token, CATALOGS)
                    row['token_resolution'] = resolution.state
                    row['candidates'] = len(resolution.candidates)
                    row['routes'] = []
                    pools = []
                    for router in ('V2', 'V3'):
                        candidate = next((c for c in resolution.candidates
                                          if c.ready and c.pool.router == router), None)
                        if candidate is None:
                            continue
                        pool = chain.verify_pool(candidate.pool.address, address(token))
                        by_pool = resolve(chain, pool.address, CATALOGS)
                        assert by_pool.state == 'RESOLVED' and by_pool.target == address(token)
                        price = chain.price(pool)
                        bought = chain.quote(pool, 10**15, True)
                        sold = chain.quote(pool, bought, False)
                        assert price > 0 and bought > 0 and sold > 0
                        row['routes'].append({'router': router, 'pool': pool.address,
                            'decimals': pool.token_decimals, 'price_wbnb': str(price),
                            'buy_input_wbnb_raw': 10**15, 'buy_output_raw': bought,
                            'sell_output_wbnb_raw': sold, 'pool_resolution': by_pool.state})
                        pools.append(pool)
                    assert pools, 'No active canonical WBNB pool'
                    # Explicit V2-first selection; AMBIGUOUS is not an error or automatic choice.
                    worker = Worker(Store(directory / (name + '.json')))
                    worker.chain, worker.pool = chain, pools[0]
                    logs, events, samples = [], [], []
                    worker.log.connect(logs.append)
                    worker.event.connect(lambda kind, value, sink=events: sink.append((kind, value)))
                    original = chain.price
                    row['read_failures'] = []
                    def measured(pool, original=original, samples=samples, failures=row['read_failures']):
                        start = time.monotonic()
                        try:
                            value = original(pool)
                        except Exception as exc:
                            failures.append({'type': type(exc).__name__,
                                'http_status': getattr(getattr(exc, 'response', None), 'status_code', None)})
                            raise
                        samples.append((time.monotonic(), time.monotonic()-start, value))
                        return value
                    chain.price = measured
                    worker.command('start', config(worker.pool))
                    sessions.append((worker, row, logs, events, samples, calls))
                    row['selected_router'] = worker.pool.router
                    print(json.dumps({'ready': name, 'routes': len(pools)}), flush=True)
                except Exception as exc:
                    row.update(passed=False, error_type=type(exc).__name__, error=str(exc))
            for worker, *_ in sessions:
                worker.start()
            began = progress = time.monotonic()
            while sessions and time.monotonic() - began < seconds:
                app.processEvents()
                time.sleep(.01)
                if time.monotonic() - progress >= 30:
                    progress = time.monotonic()
                    print(json.dumps({'elapsed_s': round(progress-began),
                        'prices': {row['name']: len(samples) for _, row, _, _, samples, _ in sessions},
                        'running': {row['name']: w.running for w, row, *_ in sessions},
                        'errors': {row['name']: [v for k, v in events if k == 'error']
                                   for _, row, _, events, _, _ in sessions}}), flush=True)
            report['observation_seconds'] = round(time.monotonic()-began, 2)
            for worker, *_ in sessions:
                worker.stop_event.set()
            deadline = time.monotonic()+30
            while any(w.running or w.stop_event.is_set() for w, *_ in sessions):
                app.processEvents()
                time.sleep(.01)
                if time.monotonic() > deadline:
                    raise TimeoutError('STOP failed')
        finally:
            for worker, *_ in sessions:
                worker.quit_event.set()
            for worker, *_ in sessions:
                assert worker.wait(15000), 'Worker did not terminate'
            app.processEvents()
            for worker, row, logs, events, samples, calls in sessions:
                errors = [value for kind, value in events if kind == 'error']
                gaps = [b[0]-a[0] for a, b in zip(samples, samples[1:])]
                row.update(observations=len(samples), distinct_prices=len({s[2] for s in samples}),
                    errors=errors, buys=sum('PAPER BUY:' in line for line in logs),
                    exits=dict(Counter(line.split('SELL: ')[1] for line in logs if 'SELL: ' in line)),
                    gap_resets=sum('0.55' in line for line in logs),
                    gaps_over_550ms=sum(g > .55 for g in gaps),
                    clean_stop=not worker.paper.position and not worker.isRunning(), rpc_methods=dict(calls))
                if samples:
                    latencies = sorted(s[1]*1000 for s in samples)
                    row.update(active_span_s=round(samples[-1][0]-samples[0][0], 2),
                        median_price_ms=round(statistics.median(latencies), 2),
                        p95_price_ms=round(latencies[max(0, int(len(latencies)*.95)-1)], 2),
                        max_price_ms=round(max(latencies), 2),
                        min_price=str(min(s[2] for s in samples)), max_price=str(max(s[2] for s in samples)))
                    row['synthetic_cycles'] = synthetic_cycles(directory, worker.pool, samples[0][2])
                row['passed'] = bool(samples) and not errors and row['clean_stop']
            report['passed'] = len(report['tokens']) == len(tokens) and all(r.get('passed') for r in report['tokens'])
            output.write_text(json.dumps(report, indent=2, ensure_ascii=False)+'\n')
    print(json.dumps({'passed': report['passed'], 'output': str(output)}), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--seconds', type=int, default=180)
    parser.add_argument('--endpoint', choices=[ENDPOINT, 'https://bsc-dataseed.binance.org'], default=ENDPOINT)
    parser.add_argument('--token', action='append', help='Override sample: NAME=0xaddress (repeatable)')
    args = parser.parse_args()
    if args.seconds <= 0:
        parser.error('--seconds must be positive')
    tokens = None
    if args.token:
        try:
            tokens = dict(item.split('=', 1) for item in args.token)
            tokens = {name: address(token) for name, token in tokens.items()}
        except ValueError:
            parser.error('--token requires NAME=valid_address')
    main(args.output, args.seconds, tokens, args.endpoint)
