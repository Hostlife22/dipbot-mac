"""CLI for the packaged isolated PAPER verification workflow."""
import argparse
from pathlib import Path
from dipbot.checks.token_ui_paper import run

if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--token',required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--seconds',type=int,default=300)
    parser.add_argument('--pool',help='Explicit pool, still verified against canonical factory')
    parser.add_argument('--exercise-recovery',action='store_true',help='Controlled quote timeout and STOP/restart during PAPER')
    parser.add_argument('--modern', action='store_true')
    parser.add_argument('--close-after', action='store_true')
    parser.add_argument('--amount-usd', help='Virtual USD amount, at most 1; no real trades')
    parser.add_argument('--automatic-only', action='store_true', help='Observe natural entries without forcing manual BUY')
    parser.add_argument('--fee-usd', default='0.01', help='Fixed PAPER operation cost converted at setup; an assumption, not actual gas')
    parser.add_argument('--adaptive-rpc', action='store_true')
    parser.add_argument('--min-swaps',default='1')
    parser.add_argument('--observe-manual-position',action='store_true')
    parser.add_argument('--backup-rpc',default='')
    parser.add_argument('--rpc',default='https://bsc-dataseed.binance.org')
    parser.add_argument('--take-profit',default='2');parser.add_argument('--stop-loss',default='2')
    parser.add_argument('--dip');parser.add_argument('--slippage');parser.add_argument('--dynamic')
    parser.add_argument('--continue-after-sl', action=argparse.BooleanOptionalAction, default=None)
    parser.add_argument('--cooldown', type=float, help='Cooldown seconds; preserves selected signal mode')
    parser.add_argument('--trailing', type=float, help='Trailing percent; 0 disables it')
    parser.add_argument('--preflight-fault', choices=('http429','rpc-limit'), help='Inject one labelled read-only preflight failure at a natural signal; no injected BUY')
    args=parser.parse_args()
    if args.automatic_only and args.exercise_recovery:
        parser.error('--automatic-only cannot include controlled STOP/restart or injected signals')
    raise SystemExit(run(args.token,args.output,args.seconds,args.pool,args.exercise_recovery,
        close_after=args.close_after,modern=args.modern,amount_usd=args.amount_usd,automatic_only=args.automatic_only,fee_usd=args.fee_usd,adaptive_rpc=args.adaptive_rpc,endpoint=args.rpc,take_profit=args.take_profit,stop_loss=args.stop_loss,backup_rpc=args.backup_rpc,observe_manual_position=args.observe_manual_position,min_swaps=args.min_swaps,dip=args.dip,slippage=args.slippage,dynamic=args.dynamic,continue_after_sl=args.continue_after_sl,cooldown=args.cooldown,trailing=args.trailing,preflight_fault=args.preflight_fault))
