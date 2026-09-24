"""Hash-locked static evidence for AutoPair, Sweep and settings; never loads EXE.

python -m tools.recovery_native EXE --output FILE [--disassembly DIR]
Only selected public field names/constants are exported, not credential values.
"""
import argparse
import hashlib
import json
from pathlib import Path
from tools.audit_native import SHA256, constants

TABLES = {'autopair': 0x1429d6180, 'dynamic_pairs': 0x1429ed8b0,
          'gui': 0x142a0cf60, 'runtime_config': 0x142a5a5c0, 'wallet_sweep': 0x142a6fd50}
SELECTED = {'autopair': [15, 16, 17, 18, 19, 83, 143, 144, 145, 146, 147, 218],
            'dynamic_pairs': [5, 31, 33, 38, 40, 41, 106, 141, 223, 229, 231],
            'gui': [609, 613, 614, 615, 616, 618, 619, 620, 621, 622, 623],
            'runtime_config': [6, 9, 10, 11, 12, 15, 38, 41, 42, 43, 45],
            'wallet_sweep': [31, 33, 43, 92, 103, 120, 141, 146, 160, 194, 201, 213]}
RANGES = {
    'autopair_route_key': (0x1408581a0, 0x1408584bc),
    'autopair_choose_candidate': (0x140858fd0, 0x14085a407),
    'dynamic_converter_settings': (0x140bacbf0, 0x140badc32),
    'sweep_choose_registered': (0x142091870, 0x1420925c1),
    'settings_save': (0x141dc0d20, 0x141dc1257),
    'settings_reset': (0x141dc1260, 0x141dc147d),
    'ui_load': (0x140f32a00, 0x140f35da2),
    'ui_save': (0x140f35db0, 0x140f374a0),
}


def inspect(exe, disassembly=None):
    data = exe.read_bytes()
    if hashlib.sha256(data).hexdigest() != SHA256:
        raise ValueError('Different release: re-establish native addresses')
    modules = {name: constants(data, name) for name in TABLES}
    report = {'sha256': SHA256, 'original_executed': False,
              'decoded_counts': {name: len(rows) for name, rows in modules.items()},
              'tables': {name: hex(addr) for name, addr in TABLES.items()},
              'selected_constants': {name: [rows[i] for i in SELECTED[name]]
                                     for name, rows in modules.items()}, 'native_ranges': {}}
    if disassembly:
        import capstone
        engine = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_64)
        engine.detail = True
        disassembly.mkdir(parents=True, exist_ok=True)
        slots = {TABLES[name] + row['index']*8: f"{name}[{row['index']}]={row['value']!r}"
                 for name, rows in modules.items() for row in rows}
    for name, (start, end) in RANGES.items():
        offset = start - 0x140001000 + 0x400
        code = data[offset:offset + end-start]
        report['native_ranges'][name] = {'va_start': hex(start), 'va_end': hex(end),
            'file_offset': offset, 'sha256': hashlib.sha256(code).hexdigest()}
        if disassembly:
            lines = []
            for ins in engine.disasm(code, start):
                refs = [slots[ins.address+ins.size+op.mem.disp] for op in ins.operands
                        if op.type == capstone.CS_OP_MEM and op.mem.base == capstone.x86.X86_REG_RIP
                        and ins.address+ins.size+op.mem.disp in slots]
                lines.append(f'{ins.address:#x} {ins.mnemonic} {ins.op_str}' +
                             (' ; ' + ' | '.join(refs) if refs else ''))
            (disassembly/(name+'.asm')).write_text('\n'.join(lines)+'\n')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('exe', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--disassembly', type=Path)
    args = parser.parse_args()
    args.output.write_text(json.dumps(inspect(args.exe, args.disassembly), ensure_ascii=False, indent=2)+'\n')
    print('Wrote selected static evidence:', args.output)
