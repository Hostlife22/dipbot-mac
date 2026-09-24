"""Additional timer, Sweep completion, REMOVE and helper-prefix evidence."""
import argparse
import json
from pathlib import Path
from tools.recovery_native import inspect

TABLES={'commercial_features':0x1429db320,'dynamic_pairs':0x1429ed8b0,'secure_store':0x142a5aa10}
SELECTED={'commercial_features':[14,16,17,27,29,92,101,106,132,159,179,180,301,303,304,307,309]+list(range(410,435)),
          'dynamic_pairs':[9,21,111,112,142,143,176,196,197,200],
          'secure_store':list(range(109,131))}
RANGES={
 'schedule_autopair':(0x1409093b0,0x140909f9f),
 'autopair_worker':(0x14090ab20,0x14090bbc9),
 'holdings_error':(0x1409143f0,0x140914863),
 'sweep_worker':(0x140917680,0x1409184e5),
 'finish_sweep_ui':(0x1409187e0,0x140918d55),
 'sweep_complete':(0x140918d60,0x14091a140),
 'registry_remove':(0x140bc0c60,0x140bc19b7),
 'read_protected_json':(0x141dce190,0x141dcf0c4),
 'method_call0':(0x142911880,0x1429119d7),
 'method_call1':(0x1429119e0,0x142911b3c),
 'get_attribute':(0x1429125c0,0x14291263a),
 'getattr_default_prefix_only':(0x1428fccf0,0x1428fcd25),
}


def report(exe,disassembly=None):
    return inspect(exe,disassembly,tables=TABLES,selected=SELECTED,ranges=RANGES)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('exe',type=Path);parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--disassembly',type=Path)
    args=parser.parse_args()
    args.output.write_text(json.dumps(report(args.exe,args.disassembly),ensure_ascii=False,indent=2)+'\n')
