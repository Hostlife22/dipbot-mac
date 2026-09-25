"""Compare legacy/window on recorded public prices with explicit stress assumptions."""
import argparse
from dataclasses import asdict
from decimal import Decimal as D
import json
from pathlib import Path

from dipbot.replay import replay, ReplayCosts
from dipbot.signal_policy import SignalPolicy
from dipbot.strategy import Settings


def load(path):
    with path.open() as stream:
        rows = [json.loads(line) for line in stream if line.strip()]
    if not rows or rows[0].get('event') != 'header' or rows[0].get('version') != 1:
        raise ValueError('Неизвестный формат рыночной записи')
    if rows[0].get('starts_with_position'):
        raise ValueError('Запись начинается с открытой позицией; нужен её полный контекст')
    if rows[-1].get('event') != 'end' or rows[-1].get('dropped') != 0:
        raise ValueError('Неполная запись: нет завершения или есть пропуски')
    body=rows[1:-1]
    if rows[-1].get('written') != len(body) or rows[-1].get('last_sequence') != len(body):
        raise ValueError('Счётчики рыночной записи не совпадают')
    if [r.get('sequence') for r in body] != list(range(1,len(body)+1)):
        raise ValueError('Пропущены события рыночной записи')
    kind = 'observation' if any(r['event']=='observation' for r in body) else 'price'
    return rows[0], [r for r in body if r['event']==kind]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input',type=Path)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--latency',type=float,default=.25)
    parser.add_argument('--fee-bps',default='25')
    parser.add_argument('--impact-bps',default='0')
    parser.add_argument('--tax-bps',default='0')
    parser.add_argument('--gas-quote',default='0')
    parser.add_argument('--window',type=float,default=60)
    parser.add_argument('--rebound',default='0')
    args=parser.parse_args()
    header,samples=load(args.input)
    raw=header['settings']
    settings=Settings(**{k:(float(v) if k=='max_gap' else D(str(v))) for k,v in raw.items()})
    costs=ReplayCosts(D(args.fee_bps),D(args.impact_bps),D(args.tax_bps),D(args.gas_quote),args.latency)
    results=[replay(samples,settings,SignalPolicy(mode,args.window,D(args.rebound)),costs)
             for mode in ('legacy','window')]
    args.output.write_text(json.dumps({'source':'recorded_market','results':results,
        'profitability_proven':False,'transactions_sent':0},indent=2)+'\n')
    print(json.dumps([{'policy':r['policy']['mode'],'trades':len(r['trades']),
                      'realized_quote':r['realized_quote'],'pending':r['pending']} for r in results]))


if __name__=='__main__': main()
