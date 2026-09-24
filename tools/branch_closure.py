"""Follow direct intra-text control flow without running code or following calls."""
import argparse
from collections import deque
import hashlib
import json
from pathlib import Path
from tools.audit_native import SHA256, constants
from tools.full_static_audit import TABLES
from tools.receiver_flow import RANGES, analyse
from tools.context_workspace import HELPERS

TARGETS=('ui_trade_defaults','runtime_reset','gui_stop','getattr_default_prefix_only','load_config_values')


def walk(start, decode, allowed, limit=30000):
    queue=deque([start]);instructions={};unresolved=[]
    import capstone as cs
    while queue:
        addr=queue.popleft()
        if addr in instructions:continue
        if not allowed(addr) or len(instructions)>=limit:
            unresolved.append(hex(addr));continue
        ins=decode(addr)
        if ins is None:unresolved.append(hex(addr));continue
        instructions[addr]=ins
        if ins.mnemonic in ('int3','ud2','hlt'):
            unresolved.append('trap@'+hex(addr));continue
        if ins.group(cs.CS_GRP_RET):continue
        if ins.group(cs.CS_GRP_JUMP):
            if ins.operands[0].type==cs.CS_OP_IMM:queue.append(ins.operands[0].imm)
            else:unresolved.append('indirect@'+hex(addr))
            if ins.mnemonic=='jmp':continue
        queue.append(addr+ins.size)
    return [instructions[start]]+[v for k,v in sorted(instructions.items()) if k!=start],sorted(set(unresolved))


def report(exe):
    import pefile
    import capstone as cs
    data=exe.read_bytes()
    if hashlib.sha256(data).hexdigest()!=SHA256:raise ValueError('Unexpected release')
    pe=pefile.PE(data=data);base=pe.OPTIONAL_HEADER.ImageBase
    unwind={base+e.struct.BeginAddress:base+e.struct.EndAddress for e in pe.DIRECTORY_ENTRY_EXCEPTION}
    text=next(s for s in pe.sections if s.Name.rstrip(b'\0')==b'.text');raw=text.get_data();lo=base+text.VirtualAddress
    md=cs.Cs(cs.CS_ARCH_X86,cs.CS_MODE_64);md.detail=True
    def decode(addr):return next(md.disasm(raw[addr-lo:addr-lo+15],addr,count=1),None)
    slots={t+8*r['index']:r['value'] for m,t in TABLES.items() for r in constants(data,m)
           if isinstance(r['value'],str) and r['value'].isidentifier()}
    out={}
    selected={name:RANGES[name] for name in TARGETS}
    selected.update({name:(start,unwind[start]) for name,start in
                     [('runtime_reload',0x141dc0230),('runtime_load',0x141dbfd60),('secure_settings_load',0x141dd0600)]})
    for name,(start,end) in selected.items():
        # Nearby direct branches only; cross-function tail jumps remain possible.
        instructions,unresolved=walk(start,decode,lambda a:lo<=a<lo+len(raw) and abs(a-start)<0x10000)
        new=[i for i in instructions if not start<=i.address<end]
        refs=[]
        for ins in instructions:
            for op in ins.operands:
                if op.type==cs.CS_OP_MEM and op.mem.base==cs.x86.X86_REG_RIP:
                    symbol=slots.get(ins.address+ins.size+op.mem.disp)
                    if symbol:refs.append({'site':hex(ins.address),'name':symbol})
        out[name]={'entry':hex(start),'original_end':hex(end),'instructions':len(instructions),
                   'added_instructions':len(new),'unresolved':unresolved,
                   'instruction_bytes_sha256':hashlib.sha256(b''.join(i.bytes for i in instructions)).hexdigest(),
                   'references':refs,'calls':analyse(instructions,slots,{v:k for k,v in HELPERS.items()})}
    return {'exe_sha256':SHA256,'original_executed':False,
            'scope':'direct reachable instructions only; no exception table edges or callees; ui_trade_defaults begins mid-function and has no proven incoming arguments',
            'closures':out}


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('exe',type=Path);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();a.output.write_text(json.dumps(report(a.exe),indent=2)+'\n')
