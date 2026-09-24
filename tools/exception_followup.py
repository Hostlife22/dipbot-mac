"""Hash-locked direct CFG and special-method/tuple call evidence; no native execution."""
import argparse
import hashlib
import json
from pathlib import Path
from tools.audit_native import SHA256, constants
from tools.full_static_audit import TABLES
from tools.receiver_flow import analyse, RANGES
from tools.branch_closure import walk
from tools.context_workspace import HELPERS
from tools.extended_calls import EXTRA
from tools.argument_audit import EXTRA_HELPERS

ADDED = {0x142912af0: 'get_special_attribute', 0x142908ee0: 'call_tuple3'}
ENTRIES = {'registry_remove': 0x140bc0c60, 'runtime_load': 0x141dbfd60,
           'runtime_reload': 0x141dc0230, 'live_pair_result': 0x140904000, 'secure_settings_load': 0x141dd0600}
ENTRIES.update({name: RANGES[name][0] for name in
                ('gui_stop', 'reload_saved_settings', 'load_config_values', 'remove_live_pair')})


def report(exe):
    import pefile
    import capstone as cs
    data = exe.read_bytes()
    if hashlib.sha256(data).hexdigest() != SHA256:
        raise ValueError('Unexpected release')
    pe = pefile.PE(data=data)
    base = pe.OPTIONAL_HEADER.ImageBase
    md = cs.Cs(cs.CS_ARCH_X86, cs.CS_MODE_64)
    md.detail = True
    section = next(s for s in pe.sections if s.Name.rstrip(b'\0') == b'.text')
    lo = base + section.VirtualAddress
    raw = section.get_data()
    slots = {table + 8*r['index']: r['value'] for module, table in TABLES.items()
             for r in constants(data, module)
             if isinstance(r['value'], str) and r['value'].isidentifier()}
    # Only this public tuple is exported, not arbitrary settings constants.
    value = constants(data, 'dynamic_pairs')[160]['value']
    if value != [None, None, None]:
        raise ValueError('Unexpected context-manager tuple')
    slots[TABLES['dynamic_pairs'] + 8*160] = value
    helpers = {**{v:k for k,v in HELPERS.items()}, **EXTRA, **EXTRA_HELPERS, **ADDED}
    def decode(addr):
        return next(md.disasm(raw[addr-lo:addr-lo+15], addr, count=1), None)
    output = {}
    for name, start in ENTRIES.items():
        insns, unresolved = walk(start, decode, lambda a: lo <= a < lo+len(raw) and abs(a-start) < 0x10000)
        references = [{'site': hex(i.address), 'constant': slots[i.address+i.size+op.mem.disp]}
                      for i in insns for op in i.operands
                      if op.type == cs.CS_OP_MEM and op.mem.base == cs.x86.X86_REG_RIP
                      and i.address+i.size+op.mem.disp in slots]
        output[name] = {'references': references, 'entry': hex(start), 'instructions': len(insns), 'unresolved': unresolved,
                        'sha256': hashlib.sha256(b''.join(i.bytes for i in insns)).hexdigest(),
                        'calls': analyse(insns, slots, helpers),
                        'indirect_calls': [{'site': hex(i.address), 'operand': i.op_str} for i in insns
                                           if i.mnemonic == 'call' and i.operands[0].type != cs.CS_OP_IMM]}
    helper_evidence = {}
    for e in pe.DIRECTORY_ENTRY_EXCEPTION:
        a = base + e.struct.BeginAddress
        if a in ADDED:
            blob = pe.get_data(e.struct.BeginAddress, e.struct.EndAddress-e.struct.BeginAddress)
            helper_evidence[hex(a)] = {'name': ADDED[a], 'end': hex(base+e.struct.EndAddress),
                                      'sha256': hashlib.sha256(blob).hexdigest()}
    return {'exe_sha256': SHA256, 'original_executed': False, 'helpers': helper_evidence,
            'observations': {
                'remove_context_exit': '0x140bc13d6 calls self._lock.__exit__ with exception vector; 0x140bc1593 and 0x140bc1619 use the literal (None,None,None) tuple',
                'runtime_load_error': '0x141dbff23 branches to error cleanup; 0x141dc01b0 clears RAX before returning; no local defaults on this path',
                'gui_reload_error': '0x140f2d40c skips successful Trader recreation when reload returns null; handler logs NODE / GAS reload error',
                'live_pair_retry': '0x140906185 calls _schedule_autopair(token.text()) inside _handle_live_pair_result',
                'sweep_threshold': 'Shared constant at 0x1429a02b0 remains unidentified; no numerical threshold claimed'},
            'limitations': ['Direct CFG only, no SEH or callee closure',
                            'Special method lookup uses receiver type and descriptor binding; expression is an access path, not proof of runtime identity',
                            'Indirect calls include reference-count destructors and imports; unresolved does not mean application callback'],
            'closures': output}


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('exe', type=Path)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    a.output.write_text(json.dumps(report(a.exe), indent=2)+'\n')
