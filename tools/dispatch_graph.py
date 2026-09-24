"""Basic-block-local attribute arguments and direct helper calls, hash-locked PE."""
import argparse
import hashlib
import json
from pathlib import Path
from tools.audit_native import SHA256, constants
from tools.full_static_audit import TABLES, RANGES as FULL
from tools.remaining_native import RANGES as REMAINING
from tools.runtime_native import RANGES as RUNTIME
from tools.context_workspace import HELPERS

RANGES={**FULL,**RUNTIME,**REMAINING,
        'commercial_init':(0x1408f5f80,0x1408f9c4a),
        'start_bot':(0x140f54250,0x140f55f64),
        'run_bot_thread':(0x140f55f70,0x140f56b31)}


def report(exe):
    import capstone
    import pefile
    data=exe.read_bytes()
    if hashlib.sha256(data).hexdigest()!=SHA256:raise ValueError('Unexpected release')
    pe=pefile.PE(data=data);engine=capstone.Cs(capstone.CS_ARCH_X86,capstone.CS_MODE_64);engine.detail=True
    slots={}
    for module,table in TABLES.items():
        for row in constants(data,module):
            value=row['value']
            if isinstance(value,str) and value.isidentifier():slots[table+8*row['index']]=(module,value)
    helpers={v:k for k,v in HELPERS.items()};calls=[];seen=set();hashes={}
    for name,(start,end) in RANGES.items():
        if start in seen:continue
        seen.add(start);blob=pe.get_data(start-pe.OPTIONAL_HEADER.ImageBase,end-start)
        hashes[name]=hashlib.sha256(blob).hexdigest();argument=None
        instructions=list(engine.disasm(blob,start))
        targets={ins.operands[0].imm for ins in instructions
                 if ins.group(capstone.CS_GRP_JUMP) and ins.operands
                 and ins.operands[0].type==capstone.CS_OP_IMM}
        for ins in instructions:
            if ins.address in targets:argument=None
            _,writes=ins.regs_access()
            if any(ins.reg_name(r) in ('r8','r8d','r8w','r8b') for r in writes):argument=None
            ops=ins.operands
            if (ins.mnemonic=='mov' and len(ops)==2 and ops[0].type==capstone.CS_OP_REG
                and ins.reg_name(ops[0].reg)=='r8' and ops[1].type==capstone.CS_OP_MEM
                and ops[1].mem.base==capstone.x86.X86_REG_RIP):
                argument=slots.get(ins.address+ins.size+ops[1].mem.disp)
            if ins.mnemonic=='call':
                if ops[0].type==capstone.CS_OP_IMM and ops[0].imm in helpers:
                    row={'caller':name,'site':hex(ins.address),'helper':helpers[ops[0].imm]}
                    if argument and helpers[ops[0].imm] in ('get_attribute','has_attribute','set_attribute','method_call0','method_call1','getattr_default'):
                        row['attribute_module'],row['attribute']=argument
                    calls.append(row)
                argument=None
            elif ins.group(capstone.CS_GRP_JUMP) or ins.group(capstone.CS_GRP_RET):argument=None
    return {'exe_sha256':SHA256,'original_executed':False,
            'scope':'explicit ranges; R8 name flow only within straight-line blocks; names not runtime object identities',
            'range_hashes':hashes,'calls':calls}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('exe',type=Path)
    parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    args.output.write_text(json.dumps(report(args.exe),ensure_ascii=False,indent=2)+'\n')
