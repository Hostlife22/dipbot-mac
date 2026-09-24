"""Read x64 unwind chains; handler data is opaque, never assumed to be a scope table."""
import argparse
import bisect
import hashlib
import json
import struct
from pathlib import Path

from tools.audit_native import SHA256, constants
from tools.full_static_audit import TABLES
from tools.branch_closure import walk
from tools.receiver_flow import analyse
from tools.context_workspace import HELPERS
from tools.extended_calls import EXTRA
from tools.argument_audit import EXTRA_HELPERS


def public_constant(value):
    # No free text, addresses, deployment data or arbitrary dictionary payloads.
    if isinstance(value, str):
        return value.isascii() and value.isidentifier()
    if type(value) in (int, float, bool):
        return True
    return (isinstance(value, list) and len(value) <= 20
            and all(v is None or public_constant(v) and not isinstance(v, list) for v in value))


def unwind_chain(read, rva, max_depth=32):
    """Parse only fixed ABI headers/trailers, rejecting truncation, cycles and conflicts."""
    rows = []
    seen = set()

    def exact(address, size):
        data = read(address, size)
        if len(data) != size:
            raise ValueError('truncated unwind data')
        return data

    for _ in range(max_depth):
        if rva in seen:
            raise ValueError('cyclic unwind chain')
        seen.add(rva)
        header = exact(rva, 4)
        version, flags = header[0] & 7, header[0] >> 3
        if version not in (1, 2) or flags & ~7 or (flags & 4 and flags & 3):
            raise ValueError('unsupported unwind header')
        trailer = rva + 4 + ((header[2] + 1) & ~1) * 2
        exact(rva, trailer - rva)
        row = {'unwind_rva': hex(rva), 'version': version, 'flags': flags}
        rows.append(row)
        if flags & 4:
            start, end, rva = struct.unpack('<III', exact(trailer, 12))
            if start >= end:
                raise ValueError('invalid chained function range')
            row['chain_range_rva'] = [hex(start), hex(end)]
        else:
            if flags & 3:
                row['handler_rva'] = hex(struct.unpack('<I', exact(trailer, 4))[0])
                row['opaque_data_rva'] = hex(trailer + 4)
            return rows
    raise ValueError('unwind chain depth exceeded')


def report(exe, evidence_dir, lookups=None):
    import capstone as cs
    import pefile

    data = exe.read_bytes()
    if hashlib.sha256(data).hexdigest() != SHA256:
        raise ValueError('Unexpected release')
    pe = pefile.PE(data=data)
    base = pe.OPTIONAL_HEADER.ImageBase
    entries = sorted(pe.DIRECTORY_ENTRY_EXCEPTION, key=lambda e: e.struct.BeginAddress)
    starts = [base + e.struct.BeginAddress for e in entries]
    text = next(s for s in pe.sections if s.Name.rstrip(b'\0') == b'.text')
    raw, lo = text.get_data(), base + text.VirtualAddress
    md = cs.Cs(cs.CS_ARCH_X86, cs.CS_MODE_64)
    md.detail = True
    slots = {t + 8*r['index']: r['value'] for m, t in TABLES.items()
             for r in constants(data, m) if public_constant(r['value'])}
    identifier_slots = {a: v for a, v in slots.items() if isinstance(v, str)}
    helpers = {**{v:k for k,v in HELPERS.items()}, **EXTRA, **EXTRA_HELPERS,
               0x142907260: 'call_vector2', 0x142908ee0: 'call_tuple3',
               0x142912af0: 'get_special_attribute',
               0x142911b40: 'method_vector2', 0x142911ca0: 'method_vector3'}
    selected = {}
    inputs = {}
    if lookups is not None:
        blob = lookups.read_bytes()
        getters = json.loads(blob)
        if getters['exe_sha256'] != SHA256: raise ValueError('Mismatched lookup evidence')
        inputs[lookups.name] = hashlib.sha256(blob).hexdigest()
        helpers.update({int(r['entry'],16): 'global_lookup:'+r['module']+'.'+r['name'] for r in getters['getters']})
    for filename in ('METHOD_FACTORY_EVIDENCE.json', 'GLOBAL_FACTORY_EVIDENCE.json'):
        blob = (evidence_dir / filename).read_bytes()
        inputs[filename] = hashlib.sha256(blob).hexdigest()
        definitions = json.loads(blob)
        if definitions['exe_sha256'] != SHA256:
            raise ValueError('Mismatched factory evidence')
        for row in definitions['definitions']:
            name = row.get('qualified_name') or row['module'] + '.' + row['name']
            selected[name] = int(row['body'], 16)
    output = {}
    unwind = {}
    for name, start in sorted(selected.items()):
        instructions, missing = walk(start,
            lambda a: next(md.disasm(raw[a-lo:a-lo+15], a, count=1), None),
            lambda a: lo <= a < lo+len(raw) and abs(a-start) < 0x10000)
        owners = set()
        refs = {}
        direct = set()
        indirect = []
        for ins in instructions:
            index = bisect.bisect_right(starts, ins.address)-1
            if index >= 0 and ins.address < base + entries[index].struct.EndAddress:
                owners.add(index)
            for op in ins.operands:
                if op.type == cs.CS_OP_MEM and op.mem.base == cs.x86.X86_REG_RIP:
                    symbol = identifier_slots.get(ins.address+ins.size+op.mem.disp)
                    if symbol:
                        refs.setdefault(symbol, []).append(hex(ins.address))
            if ins.mnemonic == 'call':
                if ins.operands[0].type == cs.CS_OP_IMM:
                    direct.add(ins.operands[0].imm)
                else:
                    indirect.append(hex(ins.address))
        for index in sorted(owners):
            e = entries[index].struct
            key = hex(base+e.BeginAddress)
            if key not in unwind:
                unwind[key] = {'end': hex(base+e.EndAddress),
                               'chain': unwind_chain(pe.get_data, e.UnwindData)}
        output[name] = {'entry': hex(start), 'instruction_count': len(instructions),
            'instruction_sha256': hashlib.sha256(b''.join(i.bytes for i in instructions)).hexdigest(),
            'unresolved': missing, 'unwind_ranges': [hex(starts[i]) for i in sorted(owners)],
            'direct_call_targets': [hex(a) for a in sorted(direct)], 'indirect_call_sites': indirect,
            'public_identifier_references': refs,
            'helper_calls': analyse(instructions, slots, helpers)}
    return {'exe_sha256': SHA256, 'input_sha256': inputs, 'original_executed': False,
            'scope': 'all bodies in the two input factory inventories, direct CFG within 64KiB; not all executable functions; no callees, heap model or dynamic dispatch proof',
            'limitations': ['unwind metadata is not the Python exception graph',
                            'handler-specific data remains opaque',
                            'absence of a reference is not proof of absent behavior'],
            'functions': output, 'unwind_ranges': unwind}


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('exe', type=Path)
    p.add_argument('--evidence-dir', type=Path, default=Path('docs'))
    p.add_argument('--output', required=True, type=Path)
    p.add_argument('--lookups', type=Path)
    a = p.parse_args()
    a.output.write_text(json.dumps(report(a.exe, a.evidence_dir, a.lookups), indent=2)+'\n')
