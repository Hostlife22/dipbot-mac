"""Static public workflow evidence; hash-locked, never executes the supplied EXE.

python -m tools.workflow_native EXE --output FILE [--disassembly DIR]
"""
import argparse
import json
from pathlib import Path
from tools.recovery_native import inspect

TABLES = {'commercial_features':0x1429db320, 'secure_store':0x142a5aa10,
          'dynamic_pairs':0x1429ed8b0, 'gui':0x142a0cf60, 'wallet_sweep':0x142a6fd50}
SELECTED = {
    'commercial_features':[63,194,195,204,205,206,207,507,508],
    'secure_store':[87,98,103,111,112,113,115],
    'dynamic_pairs':[31,33,38,40,41],
    'gui':[300,304,308,315,609,613,614,615,616,618,619,620,621,622,623],
    'wallet_sweep':[31,33,43,79,92,103,120,141,146,160,194,201,213,216],
}
RANGES = {
    'converter_seed':(0x1408fed60,0x1408ff888),
    'converter_probe':(0x1408ff890,0x140900e81),
    'remove_live_pair':(0x140907240,0x1409093a9),
    'schedule_autopair':(0x1409093b0,0x140909f9f),
    'handle_autopair_result':(0x14090cac0,0x14090fd85),
    'commercial_gui_init':(0x1408f5f80,0x1408f9c4a),
    'unique_name':(0x140bbbcd0,0x140bbe272),
    'safe_symbol':(0x140bac2d0,0x140bacbe8),
    'pair_amount_key':(0x140f30970,0x140f30dc0),
    'sweep_run':(0x142099f30,0x1420a00d6),
    'sweep_base_order':(0x1420a0560,0x1420a0943),
    'wallet_registry_load':(0x142084980,0x1420867bb),
    'wallet_registry_save':(0x142086ef0,0x1420886a2),
    'atomic_write_bytes':(0x141dcbb00,0x141dcd548),
    'write_protected_json':(0x141dcd550,0x141dce18d),
    'read_protected_json':(0x141dce190,0x141dcf0c4),
}

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('exe',type=Path)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--disassembly',type=Path)
    args = parser.parse_args()
    report = inspect(args.exe,args.disassembly,tables=TABLES,selected=SELECTED,ranges=RANGES)
    args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print('Wrote selected static evidence:',args.output)
