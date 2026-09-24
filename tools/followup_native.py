"""Reproduce selected REMOVE/PENDING/envelope evidence without executing EXE."""
import argparse
import json
import struct
import re
from pathlib import Path
from tools.recovery_native import inspect
from tools.audit_native import constants

TABLES = {'commercial_features':0x1429db320,'secure_store':0x142a5aa10,
          'wallet_sweep':0x142a6fd50}
SELECTED = {'commercial_features':[93,101,132,280,281,282,287,291,295,335],
            'secure_store':[109,111,112,113,114,115,120,126,128,130],
            'wallet_sweep':list(range(184,215))}
RANGES = {
    'remove_live_pair':(0x140907240,0x1409093a9),
    'autopair_close':(0x140922010,0x140922552),
    'autopair_execution_guard':(0x14091b630,0x14091c89d),
    'vault_version_assignment':(0x141dd2251,0x141dd2264),
    'read_protected_json':(0x141dce190,0x141dcf0c4),
    'write_protected_json':(0x141dcd550,0x141dce18d),
    'sweep_run':(0x142099f30,0x1420a00d6),
}


def report(exe):
    result=inspect(exe,tables=TABLES,selected=SELECTED,ranges=RANGES)
    data=exe.read_bytes()
    rows=constants(data,'commercial_features')
    wanted={TABLES['commercial_features']+row['index']*8:row['value'] for row in rows
            if row['value'] in ('_autopair_timer','_schedule_autopair','STATUS_PENDING')}
    refs=[]
    # Scan only the known release .text for RIP-relative MOV/LEA encodings.
    # This is supporting evidence, not a proof that indirect dispatch is absent.
    pattern=rb'[\x48\x4c][\x8b\x8d][\x05\x0d\x15\x1d\x25\x2d\x35\x3d]....'
    for match in re.finditer(pattern,data[0x400:0x292bc00],re.S):
        offset=match.start()+0x400
        va=0x140001000+offset-0x400
        dest=va+7+struct.unpack_from('<i',data,offset+3)[0]
        if dest in wanted:refs.append({'name':wanted[dest],'ref':hex(va)})
    result['direct_rip_references']=refs
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('exe',type=Path)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    args.output.write_text(json.dumps(report(args.exe),ensure_ascii=False,indent=2)+'\n')
    print('Wrote public static evidence:',args.output)
