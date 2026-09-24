"""Hash-locked public evidence for nonce, send, receipt wait and close dispatch."""
import argparse
import json
from pathlib import Path
from tools.recovery_native import inspect

TABLES = {'trader':0x142a5f6d0,'wallet_sweep':0x142a6fd50}
SELECTED = {'trader':[65,66,67,68,147,148,156,157,158,159,160,161,162,163,164,165,166,426],
            'wallet_sweep':[128,129,166,213]}
RANGES = {
    'next_nonce':(0x141e65a10,0x141e6629b),
    'sync_nonce':(0x141e662a0,0x141e66c05),
    'tx_base':(0x141e66c10,0x141e670ef),
    'send':(0x141e670f0,0x141e67cfb),
    'wait':(0x141e67d00,0x141e68294),
    'close_trader':(0x142093c20,0x142093f94),
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
