"""Hash-locked evidence for close/stop/error dispatch, without running the EXE."""
import argparse
import json
from pathlib import Path
from tools.recovery_native import inspect

TABLES = {'gui':0x142a0cf60, 'commercial_features':0x1429db320, 'wallet_sweep':0x142a6fd50}
SELECTED = {'gui':[112,128,146,360,563,572,602,643,644,853,854],
            'commercial_features':[18,20,75,76,80,81,82,84,93,110,126,242,243,295,353,354,421,435,483],
            'wallet_sweep':[128,129]}
RANGES = {
    'gui_close':(0x140f37a90,0x140f37fb8),
    'gui_stop':(0x140f56f10,0x140f571e1),
    'gui_events':(0x140f3b270,0x140f4021c),
    'commercial_close':(0x140922010,0x140922552),
    'autopair_error':(0x14090fd90,0x140910779),
    'sweep_error':(0x14091a140,0x14091a54d),
    'optional_trader_close':(0x142093c20,0x142093f94),
}


def report(exe, disassembly=None):
    return inspect(exe, disassembly, tables=TABLES, selected=SELECTED, ranges=RANGES)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('exe',type=Path)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--disassembly',type=Path)
    args = parser.parse_args()
    args.output.write_text(json.dumps(report(args.exe,args.disassembly),ensure_ascii=False,indent=2)+'\n')
