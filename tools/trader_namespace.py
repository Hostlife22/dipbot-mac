"""Inventory explicit Trader class namespace writes and class construction anchors."""
import argparse
import hashlib
import json
from pathlib import Path
from tools.audit_native import SHA256, constants
from tools.shared_constants_audit import find_stream


def report(exe):
    import pefile,capstone as cs
    data=exe.read_bytes()
    if hashlib.sha256(data).hexdigest()!=SHA256:raise ValueError('Unexpected release')
    pe=pefile.PE(data=data);base=pe.OPTIONAL_HEADER.ImageBase
    md=cs.Cs(cs.CS_ARCH_X86,cs.CS_MODE_64);md.detail=True
    start,end=0x141e84d3e,0x141e86cb1
    blob=pe.get_data(start-base,end-start)
    slots={0x142a5f6d0+8*r['index']:r['value'] for r in constants(data,'trader')
           if isinstance(r['value'],str) and r['value'].isidentifier()}
    writes=[];unknown=[];rdx=None;rcx_is_namespace=False
    for ins in md.disasm(blob,start):
        ops=ins.operands
        if ins.mnemonic=='call':
            if ops[0].type==cs.CS_OP_IMM and ops[0].imm==0x1401d14f0:
                if rcx_is_namespace and rdx is not None:writes.append({'site':hex(ins.address),'name':rdx})
                else:unknown.append(hex(ins.address))
            rdx=None;rcx_is_namespace=False
            continue
        _,changed=ins.regs_access()
        if cs.x86.X86_REG_RDX in changed or cs.x86.X86_REG_EDX in changed:rdx=None
        if cs.x86.X86_REG_RCX in changed or cs.x86.X86_REG_ECX in changed:rcx_is_namespace=False
        if ins.mnemonic=='mov' and len(ops)==2 and ops[0].type==cs.CS_OP_REG:
            if ops[0].reg==cs.x86.X86_REG_RCX and ops[1].type==cs.CS_OP_REG:
                rcx_is_namespace=ops[1].reg==cs.x86.X86_REG_R14
            if ops[0].reg==cs.x86.X86_REG_RDX and ops[1].type==cs.CS_OP_MEM and ops[1].mem.base==cs.x86.X86_REG_RIP:
                rdx=slots.get(ins.address+ins.size+ops[1].mem.disp)
        if ins.group(cs.CS_GRP_JUMP):rdx=None;rcx_is_namespace=False
    resource=next(lang.data.struct for t in pe.DIRECTORY_ENTRY_RESOURCE.entries if t.id==10
                  for item in t.directory.entries if item.id==3 for lang in item.directory.entries)
    _,stream=find_stream(pe.get_data(resource.OffsetToData,resource.Size),b'')
    if stream[2:4]!=b'T\0':raise ValueError('Expected shared empty tuple tag/count')
    return {'exe_sha256':SHA256,'original_executed':False,'start':hex(start),'end':hex(end),
            'sha256':hashlib.sha256(blob).hexdigest(),'namespace_writes':writes,'unresolved_setters':unknown,
            'shared_empty_bases':{'slot':'0x1429a02a0','serialized_tag':'T','length':0},
            'anchors':{'namespace_empty_dict':'0x141e84d50','namespace_register':'r14',
                       'metaclass_import_PyType_Type':'0x141e86a2e','bases_load':'0x141e86a62',
                       'namespace_tuple_item_2':'0x141e86a95','type_call':'0x141e86ab5',
                       'module_Trader_assignment':'0x141e86c17'},
            'limitations':['linear setter-site inventory, not an arbitrary heap mutation proof',
                           'fresh built-in type construction only; later monkey-patching and alternate Trader bindings unresolved']}


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('exe',type=Path)
    p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    a.output.write_text(json.dumps(report(a.exe),indent=2)+'\n')
