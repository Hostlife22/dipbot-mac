"""Compare Ghidra instruction start addresses with a Capstone linear sweep.

Requires capstone and pefile in an audit environment, not application dependencies.
Equal boundaries do not establish equal semantics or runtime behavior.
"""
import argparse
import hashlib
import json
from pathlib import Path
from tools.audit_native import SHA256
from tools.runtime_native import RANGES


def compare(exe, listings):
    import capstone
    import pefile
    data = exe.read_bytes()
    if hashlib.sha256(data).hexdigest() != SHA256:
        raise ValueError('Unexpected release')
    pe = pefile.PE(data=data)
    engine = capstone.Cs(capstone.CS_ARCH_X86,capstone.CS_MODE_64)
    ranges = {k:v for k,v in RANGES.items() if k!='gui_events'}
    ranges['converter_slippage'] = (0x141e76010,0x141e76660)
    result = {}
    for name,(start,end) in ranges.items():
        lines = (listings/(name+'.asm')).read_text().splitlines()
        ghidra = {int(line.split()[0],16) for line in lines}
        native = {i.address:i for i in engine.disasm(pe.get_data(start-pe.OPTIONAL_HEADER.ImageBase,end-start),start)}
        result[name] = {'capstone_instructions':len(native),'ghidra_instructions':len(ghidra),
                        'ghidra_starts_not_in_capstone':[hex(a) for a in sorted(ghidra-set(native))],
                        'capstone_only_mnemonics':{hex(a):native[a].mnemonic for a in sorted(set(native)-ghidra)}}
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('exe',type=Path)
    parser.add_argument('listings',type=Path)
    parser.add_argument('--output',type=Path,required=True)
    args = parser.parse_args()
    args.output.write_text(json.dumps(compare(args.exe,args.listings),indent=2)+'\n')
