"""Read function constructors embedded in selected module initializers; never execute."""
import argparse
import hashlib
import json
from pathlib import Path
from tools.audit_native import SHA256, constants
from tools.full_static_audit import TABLES


def report(exe):
    import pefile,capstone as cs
    data=exe.read_bytes()
    if hashlib.sha256(data).hexdigest()!=SHA256:raise ValueError('Unexpected release')
    p=pefile.PE(data=data);base=p.OPTIONAL_HEADER.ImageBase
    ranges={base+e.struct.BeginAddress:base+e.struct.EndAddress for e in p.DIRECTORY_ENTRY_EXCEPTION}
    md=cs.Cs(cs.CS_ARCH_X86,cs.CS_MODE_64);md.detail=True
    rows=[];hashes={}
    for module,start in [('runtime_config',0x141dc1d2d),('secure_store',0x141dd1b77)]:
        blob=p.get_data(start-base,ranges[start]-start);hashes[module]=hashlib.sha256(blob).hexdigest()
        slots={TABLES[module]+8*r['index']:r['value'] for r in constants(data,module) if isinstance(r['value'],str) and r['value'].isidentifier()}
        name=None;body=None
        instructions=list(md.disasm(blob,start))
        targets={i.operands[0].imm for i in instructions if i.group(cs.CS_GRP_JUMP) and i.operands[0].type==cs.CS_OP_IMM}
        for ins in instructions:
            if ins.address in targets:name=body=None
            ops=ins.operands
            if ins.mnemonic=='call':
                if ops[0].type==cs.CS_OP_IMM and ops[0].imm==0x1428ed190 and name and body:
                    rows.append({'module':module,'name':name,'body':hex(body),'constructor_site':hex(ins.address)})
                name=body=None;continue
            _,writes=ins.regs_access()
            if cs.x86.X86_REG_RCX in writes or cs.x86.X86_REG_ECX in writes:body=None
            if cs.x86.X86_REG_RDX in writes or cs.x86.X86_REG_EDX in writes:name=None
            if len(ops)==2 and ops[1].type==cs.CS_OP_MEM and ops[1].mem.base==cs.x86.X86_REG_RIP:
                addr=ins.address+ins.size+ops[1].mem.disp
                if ins.mnemonic=='mov' and ops[0].type==cs.CS_OP_REG and ops[0].reg==cs.x86.X86_REG_RDX:name=slots.get(addr)
                if ins.mnemonic=='lea' and ops[0].type==cs.CS_OP_REG and ops[0].reg==cs.x86.X86_REG_RCX:body=addr
            if ins.group(cs.CS_GRP_JUMP) or ins.group(cs.CS_GRP_RET):name=body=None
    return {'exe_sha256':SHA256,'original_executed':False,'scope':'native function construction, not runtime module binding or activation','initializer_hashes':hashes,'definitions':rows}


if __name__=='__main__':
    a=argparse.ArgumentParser(description=__doc__);a.add_argument('exe',type=Path);a.add_argument('--output',type=Path,required=True);p=a.parse_args()
    p.output.write_text(json.dumps(report(p.exe),indent=2)+'\n')
