"""Link qualified public method names to Nuitka function-factory code pointers."""
import argparse
import bisect
import hashlib
import json
from pathlib import Path
import re
import struct
from tools.audit_native import SHA256, constants
from tools.full_static_audit import TABLES


def report(exe):
    import pefile
    import capstone as cs
    data=exe.read_bytes()
    if hashlib.sha256(data).hexdigest()!=SHA256:raise ValueError('Unexpected release')
    pe=pefile.PE(data=data);base=pe.OPTIONAL_HEADER.ImageBase
    ranges=[(base+x.struct.BeginAddress,base+x.struct.EndAddress) for x in pe.DIRECTORY_ENTRY_EXCEPTION]
    starts=[a for a,b in ranges]
    names={table+8*r['index']:r['value'] for module,table in TABLES.items()
           for r in constants(data,module) if isinstance(r['value'],str) and
           re.fullmatch(r'[A-Za-z_]\w*\.[A-Za-z_]\w*',r['value'])}
    engine=cs.Cs(cs.CS_ARCH_X86,cs.CS_MODE_64);engine.detail=True
    found=[];seen=set()
    section=next(s for s in pe.sections if s.Name.rstrip(b'\0')==b'.text')
    raw=section.get_data()
    for m in re.finditer(rb'[\x48\x4c][\x8b\x8d][\x05\x0d\x15\x1d\x25\x2d\x35\x3d]....',raw,re.S):
        va=base+section.VirtualAddress+m.start();slot=va+7+struct.unpack_from('<i',raw,m.start()+3)[0]
        if slot not in names:continue
        index=bisect.bisect_right(starts,va)-1
        if index<0:continue
        start,end=ranges[index]
        if not start<=va<end or end-start>256 or start in seen:continue
        blob=pe.get_data(start-base,end-start);instructions=list(engine.disasm(blob,start))
        calls=[i for i in instructions if i.mnemonic=='call' and i.operands[0].type==cs.CS_OP_IMM and i.operands[0].imm==0x1428ed190]
        if len(calls)!=1:continue
        pointers=[i.address+i.size+i.operands[1].mem.disp for i in instructions if i.address<calls[0].address and
                  i.mnemonic=='lea' and len(i.operands)==2 and i.reg_name(i.operands[0].reg)=='rcx'
                  and i.operands[1].type==cs.CS_OP_MEM and i.operands[1].mem.base==cs.x86.X86_REG_RIP]
        if len(pointers)!=1:continue
        seen.add(start)
        found.append({'qualified_name':names[slot],'name_reference':hex(va),'factory':hex(start),
                      'body':hex(pointers[0]),'factory_sha256':hashlib.sha256(blob).hexdigest()})
    return {'exe_sha256':SHA256,'scope':'short factory ranges calling 0x1428ed190, single RIP-relative RCX code pointer; not dynamic dispatch proof','definitions':found}


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('exe',type=Path);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();a.output.write_text(json.dumps(report(a.exe),indent=2)+'\n')
