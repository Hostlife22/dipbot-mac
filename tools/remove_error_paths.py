"""Inventory direct-reachable REMOVE failure code, init and save; no runtime claims."""
import argparse
import hashlib
import json
from pathlib import Path
from tools.audit_native import SHA256, constants
from tools.branch_closure import walk
from tools.full_static_audit import TABLES


def report(exe):
    import pefile
    import capstone as cs
    data=exe.read_bytes()
    if hashlib.sha256(data).hexdigest()!=SHA256: raise ValueError('Unexpected release')
    pe=pefile.PE(data=data);base=pe.OPTIONAL_HEADER.ImageBase
    section=next(s for s in pe.sections if s.Name.rstrip(b'\0')==b'.text')
    lo=base+section.VirtualAddress;raw=section.get_data()
    md=cs.Cs(cs.CS_ARCH_X86,cs.CS_MODE_64);md.detail=True
    slots={TABLES['dynamic_pairs']+8*r['index']:r['value'] for r in constants(data,'dynamic_pairs')
           if isinstance(r['value'],str) and r['value'].isidentifier()}
    imports={i.address:i.name.decode() for d in pe.DIRECTORY_ENTRY_IMPORT for i in d.imports if i.name}
    def decode(a): return next(md.disasm(raw[a-lo:a-lo+15],a,count=1),None)
    paths={}
    for name,start in [('save_failure',0x140bc127f),('registry_init',0x140bb4e60),('registry_save',0x140bb9150)]:
        insns,unresolved=walk(start,decode,lambda a:lo<=a<lo+len(raw) and abs(a-start)<0x10000)
        refs=[];calls=[]
        for i in insns:
            for op in i.operands:
                if op.type==cs.CS_OP_MEM and op.mem.base==cs.x86.X86_REG_RIP:
                    addr=i.address+i.size+op.mem.disp
                    if addr in slots: refs.append({'site':hex(i.address),'name':slots[addr]})
            if i.mnemonic=='call':
                op=i.operands[0];row={'site':hex(i.address),'operand':i.op_str}
                if op.type==cs.CS_OP_MEM and op.mem.base==cs.x86.X86_REG_RIP:
                    row['import']=imports.get(i.address+i.size+op.mem.disp)
                calls.append(row)
        paths[name]={'entry':hex(start),'instructions':len(insns),'unresolved_direct_edges':unresolved,
                     'sha256':hashlib.sha256(b''.join(i.bytes for i in insns)).hexdigest(),
                     'references':refs,'calls':calls}
    return {'exe_sha256':SHA256,'original_executed':False,'paths':paths,
            'limitations':['Failure entry has no reconstructed initial register state',
                            'Callees, SEH edges and dynamic side effects are not followed',
                            'Absence of a named _records access is not proof of no rollback']}


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('exe',type=Path);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();a.output.write_text(json.dumps(report(a.exe),indent=2)+'\n')
