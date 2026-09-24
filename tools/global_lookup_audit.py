"""Recognize exact native getter templates; resolve lookup names, never runtime values."""
import argparse
import hashlib
import json
from pathlib import Path
from tools.audit_native import SHA256, constants
from tools.full_static_audit import TABLES


def signature(instructions, start, end):
    import capstone as cs
    result = []
    for ins in instructions:
        operands = []
        for op in ins.operands:
            if op.type == cs.CS_OP_REG:
                value = ('reg', op.reg, op.size)
            elif op.type == cs.CS_OP_MEM:
                m = op.mem
                value = ('mem', m.segment, m.base, m.index, m.scale,
                         None if m.base == cs.x86.X86_REG_RIP else m.disp, op.size)
            elif op.type == cs.CS_OP_IMM:
                # Only internal branches move with a template. External calls
                # must target the identical implementation to qualify.
                value = ('local', op.imm-start) if ins.group(cs.CS_GRP_JUMP) and start <= op.imm < end else ('imm', op.imm)
            else:
                return None
            operands.append(value)
        result.append((ins.address-start, ins.size, ins.mnemonic, tuple(operands)))
    return tuple(result)


def report(exe):
    import capstone as cs
    import pefile
    data = exe.read_bytes()
    if hashlib.sha256(data).hexdigest() != SHA256: raise ValueError('Unexpected release')
    pe = pefile.PE(data=data)
    base = pe.OPTIONAL_HEADER.ImageBase
    md = cs.Cs(cs.CS_ARCH_X86, cs.CS_MODE_64); md.detail = True
    slots = {t+8*r['index']: (m, r['value']) for m,t in TABLES.items()
             for r in constants(data,m) if isinstance(r['value'],str)
             and r['value'].isascii() and r['value'].isidentifier()}
    start, end = 0x140f01dd0, 0x140f01ee0
    template = pe.get_data(start-base, end-start)
    expected = signature(list(md.disasm(template,start)), start,end)
    rows = []
    for entry in pe.DIRECTORY_ENTRY_EXCEPTION:
        s = entry.struct
        if s.EndAddress-s.BeginAddress != end-start: continue
        a,b = base+s.BeginAddress,base+s.EndAddress
        blob = pe.get_data(s.BeginAddress, b-a)
        insns = list(md.disasm(blob,a))
        if signature(insns,a,b) != expected: continue
        refs, dicts, versions, indices = [], [], [], []
        for ins in insns:
            ops = ins.operands
            if len(ops) != 2 or ops[1].type != cs.CS_OP_MEM or ops[1].mem.base != cs.x86.X86_REG_RIP: continue
            slot = ins.address+ins.size+ops[1].mem.disp
            if ins.mnemonic == 'cmp' and ops[0].type == cs.CS_OP_REG and ops[0].reg == cs.x86.X86_REG_EDX:
                versions.append(slot)
            if ins.mnemonic == 'mov' and ops[0].type == cs.CS_OP_REG and ops[0].reg == cs.x86.X86_REG_RDX and slot not in slots:
                indices.append(slot)
            if ops[0].type == cs.CS_OP_REG and ops[0].reg == cs.x86.X86_REG_RDX and slot in slots:
                refs.append(slot)
            if ops[0].type == cs.CS_OP_REG and ops[0].reg == cs.x86.X86_REG_RCX:
                dicts.append(slot)
        if len(refs) != 4 or len(set(refs)) != 1 or len(dicts) != 2 or len(versions) != 1 or len(indices) != 1: continue
        module, name = slots[refs[0]]
        rows.append({'entry':hex(a),'end':hex(b),'module':module,'name':name,
                     'name_slot':hex(refs[0]),'module_dict_slot':hex(dicts[0]),
                     'fallback_dict_slot':hex(dicts[1]),'sha256':hashlib.sha256(blob).hexdigest()})
        rows[-1].update({'cached_version_slot':hex(versions[0]),'cached_index_slot':hex(indices[0])})
    return {'exe_sha256':SHA256,'original_executed':False,
            'scope':'exact 272-byte getter template modulo RIP-relative addresses; same external call targets and internal CFG',
            'template_entry':hex(start),'template_sha256':hashlib.sha256(template).hexdigest(),
            'limitations':['names and dictionary slots only, not runtime bindings',
                           'other getter layouts are not recognized','fallback dictionary identity not proved here'],
            'getters':rows}


if __name__ == '__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('exe',type=Path)
    p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    a.output.write_text(json.dumps(report(a.exe),indent=2)+'\n')
