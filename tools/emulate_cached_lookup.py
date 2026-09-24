"""Execute native cached/cold getters in Envi against synthetic dictionary memory.

No original process, DLL execution, OS calls, external-call stubs or newly decoded strings.
This is a narrow runtime component experiment, NOT complete Python initialization.
"""
import argparse
import hashlib
import json
import struct
from pathlib import Path
from tools.audit_native import SHA256
from tools.floss_names_model import compact_ascii
from tools.envi_lookup_step import step


def report(exe, evidence):
    import pefile
    import envi.archs.amd64
    data=exe.read_bytes()
    if hashlib.sha256(data).hexdigest()!=SHA256: raise ValueError('Unexpected release')
    rows=json.loads(evidence.read_text())
    if rows['exe_sha256']!=SHA256: raise ValueError('Mismatched getters')
    pe=pefile.PE(data=data);base=pe.OPTIONAL_HEADER.ImageBase
    dll=(exe.parent/'python312.dll').read_bytes()
    dll_hash=hashlib.sha256(dll).hexdigest()
    if dll_hash!='9a0e3435aaa680d868150f87ab3e388ad2eebc22f87e036155c7b4eda8cd2120':
        raise ValueError('Unexpected reference Python DLL')
    dp=pefile.PE(data=dll);db=dp.OPTIONAL_HEADER.ImageBase
    exports={e.name:db+e.address for e in dp.DIRECTORY_ENTRY_EXPORT.symbols}
    results=[]
    for row in rows['getters']:
        if row['name'] not in ('Trader','DipBot','REGISTRY_PURPOSE','WBNB'):continue
        a,b=int(row['entry'],16),int(row['end'],16)
        code=pe.get_data(a-base,b-a)
        if hashlib.sha256(code).hexdigest()!=row['sha256']:raise ValueError('Getter hash mismatch')
        for index, cache_version, missing in ((0,7,False),(1,7,False),(0,6,False),(0,6,True)):
            emu=envi.archs.amd64.Amd64Module().getEmulator()
            pages=set()
            def write(address,blob):
                for page in range(address&~4095,((address+len(blob)-1)&~4095)+1,4096):
                    if page not in pages:
                        emu.addMemoryMap(page,7,'synthetic-emulation-only',b'\0'*4096);pages.add(page)
                emu.writeMemory(address,blob)
            def q(address,value):write(address,struct.pack('<Q',value))
            dictionary,keys,key,value,stack=0x70000000,0x70001000,0x70002000,0x70003000,0x71000800
            write(a,code)
            lookup_start, lookup_end = 0x1428fe580, 0x1428fe6f0
            write(lookup_start,pe.get_data(lookup_start-base,lookup_end-lookup_start))
            # Type definitions are mapped as data; no DLL function is emulated.
            for name in (b'PyDict_Type',b'PyUnicode_Type'):
                va=exports[name];write(va,dp.get_data(va-db,408))
            write(key,compact_ascii(row['name'],exports[b'PyUnicode_Type']))
            q(key+24,0)  # Synthetic cached hash, shared with the synthetic index bucket.
            write(key+256,compact_ascii('SYNTH_OTHER_KEY',exports[b'PyUnicode_Type']))
            write(value,compact_ascii('SYNTHETIC_LOOKUP_VALUE',exports[b'PyUnicode_Type']))
            write(dictionary,struct.pack('<QQQQQQ',1,exports[b'PyDict_Type'],index+1,256,keys,0))
            # CPython 3.12 unicode-key layout: header32, indices8, entries16.
            write(keys,struct.pack('<QBBBBIQQ',1,3,3,1,0,7,5-index,index+1)+b'\xff'*8+b'\0'*32)
            for j in range(index+1):
                q(keys+40+j*16,key if j==index else key+256)
                q(keys+48+j*16,value)
            if not missing:write(keys+32,bytes([index]))
            q(int(row['module_dict_slot'],16),dictionary)
            q(int(row['fallback_dict_slot'],16),dictionary)
            q(int(row['name_slot'],16),key)
            write(int(row['cached_version_slot'],16),struct.pack('<I',cache_version))
            q(int(row['cached_index_slot'],16),index)
            write(stack-256,b'\0'*512);q(stack,0x72000000)
            emu.setRegisterByName('rsp',stack);emu.setRegisterByName('rcx',0)
            emu.setProgramCounter(a)
            steps=0
            isa_corrections=0
            blocked=None
            while emu.getProgramCounter()!=0x72000000:
                pc=emu.getProgramCounter()
                if not (a<=pc<b or lookup_start<=pc<lookup_end):
                    blocked=emu.getProgramCounter()
                    break
                if steps>=200:
                    raise RuntimeError('Fast-path experiment reached uninitialized runtime')
                try:
                    isa_corrections+=int(step(emu))
                except Exception as exc:
                    raise RuntimeError(f"{row['module']}.{row['name']} index={index} version={cache_version} missing={missing} pc={pc:#x}") from exc
                steps+=1
            result=emu.getRegisterByName('rax')
            if not missing and (blocked is not None or result!=value):
                raise AssertionError('Wrong cached dictionary value')
            if missing and blocked!=0x140006c30:
                raise AssertionError('Expected unresolved fallback dictionary lookup')
            results.append({'getter':row['entry'],'module':row['module'],'name':row['name'],
                            'cached_index':index,'cached_version':cache_version,'key_missing':missing,'steps':steps,
                            'isa_sign_extension_corrections':isa_corrections,
                            'returned_expected_synthetic_value':blocked is None,
                            'blocked_external_target':hex(blocked) if blocked else None})
    return {'exe_sha256':SHA256,'dll_sha256':dll_hash,
            'lookup_evidence_sha256':hashlib.sha256(evidence.read_bytes()).hexdigest(),
            'original_executed':False,'native_getter_instructions_emulated':True,
            'external_calls_stubbed':False,'full_python_initialization':False,
            'floss_string_decoding_run':False,
            'new_decoded_strings':0,'cases':results,
            'isa_correction':'exact instruction 48c7c0ffffffff: Envi gives 0xffffffff; x64 sign-extends to 0xffffffffffffffff; corrected locally, no package changes',
            'unicode_lookup_sha256':hashlib.sha256(pe.get_data(0x1428fe580-base,0x170)).hexdigest(),
            'limitations':['synthetic warm/cold hits by pointer identity; dictionary mutation and hash computation not initialized',
                           'uninitialized tstate, functions, modules, descriptors and missing-key fallback',
                           'known input strings returned; no claim of recovered original bindings']}


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('exe',type=Path)
    p.add_argument('--lookups',required=True,type=Path);p.add_argument('--output',required=True,type=Path)
    a=p.parse_args();a.output.write_text(json.dumps(report(a.exe,a.lookups),indent=2)+'\n')
