"""Read a complete market tape and produce a chronological holdout report."""
import argparse
from decimal import Decimal as D
import json
from pathlib import Path
from dipbot.strategy import Settings
from dipbot.replay import ReplayCosts, pool_fee_bps
from dipbot.validation import walk_forward
from tools.replay_market import load


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('input',type=Path)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--minimum-closed',type=int,default=3)
    p.add_argument('--folds',type=int,default=3)
    p.add_argument('--gas-quote')
    p.add_argument('--fee-bps')
    p.add_argument('--tax-bps',default='0')
    p.add_argument('--impact-bps',default='0')
    args=p.parse_args()
    header,samples=load(args.input)
    if header.get('sizing',{}).get('unit','quote') != 'quote':
        raise ValueError('Holdout пока поддерживает только фиксированную сумму в базе')
    settings=Settings(**{k:float(v) if k=='max_gap' else D(str(v)) for k,v in header['settings'].items()})
    report=walk_forward(samples,settings,costs=ReplayCosts(fee_bps=pool_fee_bps(header,args.fee_bps),impact_bps=D(args.impact_bps),
        tax_bps=D(args.tax_bps),gas_quote=D(args.gas_quote if args.gas_quote is not None else str(header.get('paper_policy',{}).get('fee_quote',0))),
        latency_seconds=float(header.get('paper_policy',{}).get('latency_seconds',.25))),folds=args.folds,minimum_closed=args.minimum_closed)
    args.output.write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps([{'fold':r['fold'],'chosen':r['chosen'],'status':r['status']} for r in report['folds']]))


if __name__=='__main__':main()
