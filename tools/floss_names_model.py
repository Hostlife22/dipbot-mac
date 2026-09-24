"""Experimental reconstruction of public ASCII name constants, not a Python runtime.

Maps the bundled python312.dll as DATA only and constructs compact ASCII headers.
No DLL entry point, original program, native OS API or string decoder is invoked.
The strings are known inputs, never reported as newly recovered FLOSS strings.
"""
import argparse
import hashlib
import json
from pathlib import Path
import struct
from tools.audit_native import SHA256, constants
from tools.full_static_audit import TABLES

MODEL_BASE=0x700000000000


def compact_ascii(value, unicode_type):
    encoded=value.encode('ascii')
    # CPython 3.12 x64: refcount/type/length/hash/state, sizeof(PyASCIIObject)=40.
    return struct.pack('<QQQqI4x',0xffffffff,unicode_type,len(encoded),-1,0x64)+encoded+b'\0'


def run(exe, workspace):
    import pefile
    import floss.main
    import floss.utils
    from tools.floss_object_audit import report
    data=exe.read_bytes()
    if hashlib.sha256(data).hexdigest()!=SHA256:raise ValueError('Unexpected release')
    dll=exe.parent/'python312.dll';dll_bytes=dll.read_bytes();pe=pefile.PE(data=dll_bytes)
    exports={e.name:e.address for e in pe.DIRECTORY_ENTRY_EXPORT.symbols}
    dll_base=pe.OPTIONAL_HEADER.ImageBase
    if pe.FILE_HEADER.Machine!=0x8664:raise ValueError('Expected x64 Python DLL')
    unicode_type=dll_base+exports[b'PyUnicode_Type']
    image=pe.get_memory_mapped_image()
    objects=bytearray();patches=[];unique={}
    for module,table in TABLES.items():
        for row in constants(data,module):
            value=row['value']
            if not isinstance(value,str) or not value.isascii() or not value.isidentifier():continue
            if value not in unique:
                objects.extend(b'\0'*((-len(objects))%8))
                unique[value]=MODEL_BASE+len(objects)
                objects.extend(compact_ascii(value,unicode_type))
            patches.append((table+row['index']*8,unique[value]))
    original=floss.utils.make_emulator
    def model(vw):
        emu=original(vw)
        if emu.getMemoryMap(dll_base) or emu.getMemoryMap(MODEL_BASE):raise RuntimeError('Model address collision')
        # Readable data, never executable; no imports/relocations/runtime init performed.
        emu.addMemoryMap(dll_base,4,'python312-data-model',image)
        emu.addMemoryMap(MODEL_BASE,6,'reconstructed-public-ascii',bytes(objects))
        for slot,pointer in patches:
            emu.writeMemory(slot,struct.pack('<Q',pointer))
            if emu.readMemoryPtr(slot)!=pointer:raise RuntimeError('Constant slot not patched')
        return emu
    floss.utils.make_emulator=model
    try:
        result=report(workspace)
    finally:floss.utils.make_emulator=original
    result.update({'model_applied':True,'python_objects_injected':True,'known_ascii_names':len(unique),
                   'patched_constant_slots':len(patches),'dll_sha256':hashlib.sha256(dll_bytes).hexdigest(),
                   'unicode_type_export':hex(unicode_type),'decoded_strings_claimed':False,
                   'limitations':['constructed headers from recovered inputs, not observed original runtime state',
                   'DLL data at preferred image base only; imports and Python type initialization not performed',
                   'receivers, modules, dictionaries and tstate not initialized']})
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('exe',type=Path);p.add_argument('workspace',type=Path)
    p.add_argument('--output',type=Path,required=True);a=p.parse_args();a.output.write_text(json.dumps(run(a.exe,a.workspace),indent=2)+'\n')
