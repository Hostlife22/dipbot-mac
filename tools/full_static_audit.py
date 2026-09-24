"""Release-specific public evidence and direct storage/timer reference inventory.

Scans ten application constant tables, not arbitrary imported package internals.
References are supporting evidence, not a proof that indirect I/O is absent.
"""
import argparse
import json
import re
import struct
from pathlib import Path
from tools.audit_native import constants
from tools.recovery_native import inspect

TABLES = {'commercial_bootstrap':0x1429da7b0,'bot':0x1429d77d0,'trader':0x142a5f6d0,'gui':0x142a0cf60,
          'autopair':0x1429d6180,'dynamic_pairs':0x1429ed8b0,
          'runtime_config':0x142a5a5c0,'secure_store':0x142a5aa10,
          'wallet_sweep':0x142a6fd50,'commercial_features':0x1429db320}
SELECTED = {name:[] for name in TABLES}
SELECTED.update({'commercial_bootstrap':[88], 'gui':[300,304,308,315,320,609,613,614,615,616,618,619,620,621,622,623],
 'trader':[237,259,262,263,354,355,356], 'autopair':[114,115,116],
 'secure_store':[24,27,50,80,151,152,153,156],
 'dynamic_pairs':[146,148,149,150,152], 'commercial_features':[347,348,374,507]})
RANGES = {
 'trader_init':(0x141e55f60,0x141e58081),
 'converter_slippage':(0x141e76010,0x141e76660),
 'select_safe_route':(0x141e76660,0x141e795b2),
 'execute_quote_to_bnb':(0x141e7e6c0,0x141e8126f),
 'decode':(0x14085b8c0,0x14085bcc4),
 'catalog_matches':(0x140862160,0x14086337b),
 'resolve':(0x140868f00,0x14086a3f6),
 'autopair_start':(0x140909fa0,0x14090ab1a),
 'handle_event':(0x140910780,0x140911149),
 'handle_autopair_result':(0x14090cac0,0x14090fd85),
 'recreate_trader':(0x140921950,0x140922001),
 'change_router':(0x14091aa70,0x14091b042),
 'change_pair':(0x14091b050,0x14091b622),
 'registry_key':(0x140bb5430,0x140bb58d5),
 'registry_record':(0x140bb58e0,0x140bb7420),
 'registry_load':(0x140bb7420,0x140bb914c),
 'registry_unique_name':(0x140bbbcd0,0x140bbe272),
 'ui_load':(0x140f32a00,0x140f35da2),
 'ui_save':(0x140f35db0,0x140f374a0),
 'ui_trade_defaults':(0x140f144a0,0x140f14b90),
 'entropy':(0x141dc4550,0x141dc4c66),
 'protect':(0x141dcaf40,0x141dcb517),
 'unprotect':(0x141dcb520,0x141dcbaf7),
 'runtime_save':(0x141dc0d20,0x141dc1257),
 'runtime_reset':(0x141dc1260,0x141dc147d),
 'sweep_run':(0x142099f30,0x1420a00d6),
}
SYMBOLS = {'open','read_text','write_text','read_bytes','write_bytes','load','loads',
 'dump','dumps','save','_save','_load','replace','unlink','mkstemp','fsync','flush',
 'read_protected_json','write_protected_json','_atomic_write_bytes','load_runtime_settings',
 'save_runtime_credentials','load_secure_settings','save_secure_settings','delete_secure_settings',
 'get_vault_path','get_ui_state_path','path','ui_state_path','_nonce','send_raw_transaction',
 'QTimer','setInterval','singleShot','setSingleShot','_schedule_autopair','_autopair_timer',
 'retry','retries','dust','zero_balance','remaining','final_balances'}


def report(exe):
    result=inspect(exe,tables=TABLES,selected=SELECTED,ranges=RANGES)
    data=exe.read_bytes()
    slots={}
    markers=('save','load','write','read','persist','journal','nonce','receipt','transaction','state')
    for module,table in TABLES.items():
        for row in constants(data,module):
            if isinstance(row['value'],str) and (row['value'] in SYMBOLS or
                    (row['value'].isidentifier() and any(m in row['value'].lower() for m in markers))):
                slots[table+8*row['index']] = (module,row['value'])
    groups={}
    pattern=rb'[\x48\x4c][\x8b\x8d][\x05\x0d\x15\x1d\x25\x2d\x35\x3d]....'
    for match in re.finditer(pattern,data[0x400:0x292bc00],re.S):
        offset=match.start()+0x400
        va=0x140001000+offset-0x400
        dest=va+7+struct.unpack_from('<i',data,offset+3)[0]
        if dest in slots:
            module,symbol=slots[dest]
            groups.setdefault(module,{}).setdefault(symbol,[]).append(hex(va))
    result['direct_references']=groups
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('exe',type=Path)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    args.output.write_text(json.dumps(report(args.exe),ensure_ascii=False,indent=2)+'\n')
    print('Wrote public static inventory:',args.output)
