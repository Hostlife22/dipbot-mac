"""Hash-locked evidence for Sweep internals and dynamic registry removal."""
import argparse
import json
from pathlib import Path
from tools.recovery_native import inspect

TABLES = {'wallet_sweep':0x142a6fd50,'dynamic_pairs':0x1429ed8b0}
SELECTED = {'wallet_sweep':list(range(133,175)),
            'dynamic_pairs':[9,21,111,112,142,143,176,196,197,200]}
RANGES = {
    'registry_remove':(0x140bc0c60,0x140bc19b7),
    'target_min_out':(0x1420945f0,0x142095fb4),
    # PE unwind data splits this function into adjacent hot/cold fragments.
    'simulate_target_sell':(0x142095fc0,0x142097545),
    'sell_target':(0x142097550,0x142098884),
    'convert_base':(0x142098890,0x142099f28),
}


def report(exe, disassembly=None):
    return inspect(exe,disassembly,tables=TABLES,selected=SELECTED,ranges=RANGES)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('exe',type=Path)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--disassembly',type=Path)
    args=parser.parse_args()
    args.output.write_text(json.dumps(report(args.exe,args.disassembly),ensure_ascii=False,indent=2)+'\n')
    print('Wrote selected static evidence:',args.output)
