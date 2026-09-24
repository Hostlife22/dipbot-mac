"""Read-only timing probe. Fixed public RPC, no keys, signing, wallet or state."""
import argparse
import json
import statistics
import time
from dipbot.chain import Chain, USDT


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--count', type=int, default=5)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    if not 1 <= args.count <= 20:
        parser.error('count must be 1..20')
    report = {'kind': 'READ_ONLY_RPC_MAC', 'runtime_original': False,
              'rpc': 'https://bsc-dataseed.binance.org', 'router': 'V2',
              'pool': '0x16b9a82891338f9bA80E2D6970FddA79D1eb0daE',
              'time_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
              'pause_seconds': 0.1, 'samples': []}
    try:
        chain = Chain(report['rpc'])
        pool = chain.verify_pool(report['pool'], USDT)
        previous = None
        for _ in range(args.count):
            start = time.monotonic()
            price = chain.price(pool)
            end = time.monotonic()
            report['samples'].append({'read_seconds': end-start,
                'observation_gap_seconds': None if previous is None else end-previous,
                'price': str(price)})
            previous = end
            time.sleep(0.1)
        values = [s['read_seconds'] for s in report['samples']]
        report['median_read_seconds'] = statistics.median(values)
        report['max_read_seconds'] = max(values)
    except Exception as exc:
        report['error_type'] = type(exc).__name__
    with open(args.output, 'w') as file:
        json.dump(report, file, indent=2)
        file.write('\n')
    print('Samples:', len(report['samples']), 'error:', report.get('error_type', 'none'))


if __name__ == '__main__':
    main()
