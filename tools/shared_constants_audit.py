"""Decode only the shared zero constant and inventory its native initialization."""
import argparse
import hashlib
import json
import struct
from pathlib import Path
from tools.audit_native import SHA256, Decoder


def find_stream(blob, wanted):
    """Resource has an 8-byte header, then name/NUL/size/payload records."""
    if len(blob) < 8:
        raise ValueError('Truncated resource header')
    pos = 8
    while pos < len(blob):
        end = blob.find(b'\0', pos)
        if end < 0 or end+5 > len(blob):
            raise ValueError('Truncated stream header')
        size = struct.unpack_from('<I', blob, end+1)[0]
        start = end+5
        if size < 3 or start+size > len(blob):
            raise ValueError('Invalid stream bounds')
        if blob[pos:end] == wanted:
            return start, blob[start:start+size]
        pos = start+size
    raise ValueError('Stream not found')


def report(exe):
    import pefile
    import capstone as cs
    data = exe.read_bytes()
    if hashlib.sha256(data).hexdigest() != SHA256:
        raise ValueError('Unexpected release')
    pe = pefile.PE(data=data)
    resource = next(lang.data.struct for t in pe.DIRECTORY_ENTRY_RESOURCE.entries if t.id == 10
                    for item in t.directory.entries if item.id == 3
                    for lang in item.directory.entries)
    blob = pe.get_data(resource.OffsetToData, resource.Size)
    start, stream = find_stream(blob, b'')
    count = struct.unpack_from('<H', stream)[0]
    decoder = Decoder(stream[2:])
    prefix = [decoder.read() for _ in range(3)]
    if prefix != [[], {'dict': []}, 0] or type(prefix[2]) is not int:
        raise ValueError('Shared zero prefix changed')
    base = pe.OPTIONAL_HEADER.ImageBase
    md = cs.Cs(cs.CS_ARCH_X86, cs.CS_MODE_64)
    md.detail = True
    imports = {i.address: i.name.decode() for d in pe.DIRECTORY_ENTRY_IMPORT for i in d.imports if i.name}
    ranges = {'shared_init': (0x140001000, 0x140001037),
              'constants_loader': (0x142925e40, 0x14292603d),
              'less_equal': (0x14291fc20, 0x14291fd80),
              'sweep_final_guard': (0x14209e494, 0x14209e4c5)}
    evidence = {}
    for name, (lo, hi) in ranges.items():
        raw = pe.get_data(lo-base, hi-lo)
        insns = list(md.disasm(raw, lo))
        imported = []
        for i in insns:
            if i.mnemonic != 'call':
                continue
            op = i.operands[0]
            if op.type == cs.CS_OP_MEM and op.mem.base == cs.x86.X86_REG_RIP:
                target = i.address+i.size+op.mem.disp
                if target in imports:
                    imported.append({'site': hex(i.address), 'import': imports[target]})
        evidence[name] = {'start': hex(lo), 'end': hex(hi),
                          'sha256': hashlib.sha256(raw).hexdigest(), 'import_calls': imported}
    return {'exe_sha256': SHA256, 'original_executed': False,
            'shared_stream': {'resource_type': 10, 'resource_id': 3,
                              'payload_file_offset': pe.get_offset_from_rva(resource.OffsetToData)+start,
                              'count': count, 'prefix_decoded_only': True,
                              'sha256': hashlib.sha256(stream).hexdigest()},
            'constant': {'table_base': '0x1429a02a0', 'index': 2, 'slot': '0x1429a02b0',
                         'value': prefix[2], 'type': 'int'},
            'ranges': evidence,
            'observations': ['0x140001024 supplies shared table; R8 points to empty stream name',
                             '0x142926019 decodes one object and 0x14292601e advances destination by 8',
                             'Comparator uses Py_LE=1 and reversed Py_GE=5; verified in local CPython 3.12 headers',
                             'Known final balance <= 0 bypasses remaining; None bypasses comparison and enters remaining',
                             'Loader imports include resource access and eight PyDict_New calls; this is not initialized emulator state'],
            'limitations': ['Only final-balance classification threshold, not all Sweep sell/dust thresholds',
                            'No runtime objects, imports or dictionaries initialized by this tool']}


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('exe', type=Path)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    a.output.write_text(json.dumps(report(a.exe), indent=2)+'\n')
