"""Compare legacy/window on recorded public prices with explicit stress assumptions."""
import argparse
from dataclasses import asdict
from decimal import Decimal as D
import json
from pathlib import Path

from dipbot.replay import replay, ReplayCosts, pool_fee_bps
from dipbot.signal_policy import SignalPolicy
from dipbot.exit_policy import ExitPolicy
from dipbot.strategy import Settings


def load(path):
    path = Path(path)
    first = None
    body = []
    seen = set()
    previous = None
    index = 0
    while True:
        if path.resolve() in seen:
            raise ValueError('Цикл частей рыночной записи')
        seen.add(path.resolve())
        with path.open() as stream:
            rows = [json.loads(line) for line in stream if line.strip()]
        if not rows or rows[0].get('event') != 'header' or rows[0].get('version') not in (1,2):
            raise ValueError('Неизвестный формат рыночной записи')
        header,end = rows[0],rows[-1]
        if first is None:first=header
        if header.get('version') != first['version']:
            raise ValueError('Разные версии частей записи')
        if header['version']==2:
            if (not header.get('session_id') or header.get('session_id')!=first.get('session_id')
                    or header.get('segment')!=index or header.get('previous_file')!=previous):
                raise ValueError('Неверная последовательность частей записи; нужен первый файл')
            excluded={'segment','previous_file','created_at'}
            if {k:v for k,v in header.items() if k not in excluded}!={k:v for k,v in first.items() if k not in excluded}:
                raise ValueError('Метаданные частей записи изменились')
            if end.get('session_id')!=header['session_id'] or end.get('segment')!=index:
                raise ValueError('Неполная часть рыночной записи')
        if end.get('event')!='end' or end.get('dropped')!=0:
            raise ValueError('Неполная запись: нет завершения или есть пропуски')
        part=rows[1:-1]
        offset=len(body)
        if end.get('written')!=len(part) or end.get('last_sequence')!=offset+len(part):
            raise ValueError('Счётчики рыночной записи не совпадают')
        if [r.get('sequence') for r in part]!=list(range(offset+1,offset+len(part)+1)):
            raise ValueError('Пропущены события рыночной записи')
        body.extend(part)
        following=end.get('next_file')
        if header['version']==2 and end.get('session_complete') is not (following is None):
            raise ValueError('Неполная цепочка рыночной записи')
        if following is None:break
        if (header['version']!=2 or not isinstance(following,str) or Path(following).name!=following
                or not following.startswith('market-') or not following.endswith('.jsonl')):
            raise ValueError('Некорректная ссылка на следующую часть')
        next_path=path.parent/following
        if next_path.resolve().parent!=path.parent.resolve():
            raise ValueError('Следующая часть вне каталога архива')
        previous=path.name;path=next_path;index+=1
    rows=[first]
    if D(str(rows[0].get('entry_cost_policy',{}).get('maximum_pct',0))):
        raise ValueError('Replay цен не воспроизводит включённый фильтр расходов RPC/газа')
    if rows[0].get('starts_with_position'):
        raise ValueError('Запись начинается с открытой позицией; нужен её полный контекст')
    kind = 'observation' if any(r['event']=='observation' for r in body) else 'price'
    return rows[0], [r for r in body if r['event']==kind]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input',type=Path)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--latency',type=float)
    parser.add_argument('--fee-bps')
    parser.add_argument('--impact-bps',default='0')
    parser.add_argument('--tax-bps',default='0')
    parser.add_argument('--gas-quote')
    parser.add_argument('--modes', nargs='+', choices=['legacy','window','volatility'], default=['legacy','window','volatility'])
    parser.add_argument('--window',type=float,default=60)
    parser.add_argument('--rebound',default='0')
    args=parser.parse_args()
    header,samples=load(args.input)
    raw=header['settings']
    settings=Settings(**{k:(float(v) if k=='max_gap' else D(str(v))) for k,v in raw.items()})
    paper=header.get('paper_policy',{})
    if paper.get('gas_units') and args.gas_quote is None:
        parser.error('Модель газа PAPER требует явного --gas-quote: исторические gas/FX не восстановлены')
    costs=ReplayCosts(pool_fee_bps(header,args.fee_bps),D(args.impact_bps),D(args.tax_bps),
        D(args.gas_quote if args.gas_quote is not None else str(paper.get('fee_quote',0))),
        args.latency if args.latency is not None else float(paper.get('latency_seconds',.25)))
    results=[replay(samples,settings,SignalPolicy(mode,args.window,D(args.rebound)),costs,
                    size_unit=header.get('sizing',{}).get('unit','quote'),
                    requested_amount=header.get('requested_amount'),
                    exit_policy=ExitPolicy.parse(header.get('exit_policy',{})))
             for mode in args.modes]
    args.output.write_text(json.dumps({'source':'recorded_market','results':results,
        'profitability_proven':False,'transactions_sent':0},indent=2)+'\n')
    print(json.dumps([{'policy':r['policy']['mode'],'trades':len(r['trades']),
                      'realized_quote':r['realized_quote'],'pending':r['pending']} for r in results]))


if __name__=='__main__': main()
