"""Inspect real extracted contexts for mapped receiver/name objects, without stubs."""
import argparse
import hashlib
from collections import Counter
import json
from pathlib import Path


def report(workspace):
    import floss.main
    import floss.utils
    import floss.function_argument_getter as getter
    import viv_utils
    vw=viv_utils.getWorkspace(str(workspace),analyze=False,should_save=False)
    index=viv_utils.InstructionFunctionIndex(vw)
    original=getter.get_contexts_via_monitor;initial={}
    def fresh(driver,*args):
        if driver not in initial:initial[driver]=driver._emu.getEmuSnap()
        driver._emu.setEmuSnap(initial[driver])
        return original(driver,*args)
    getter.get_contexts_via_monitor=fresh
    try:
        output={}
        for name,target in [('get_attribute',0x1429125c0),('call1',0x142906280)]:
            contexts=getter.extract_decoding_contexts(vw,target,index)
            emu=floss.utils.make_emulator(vw);counts=Counter();samples=[]
            def object_status(reg):
                value=emu.getRegisterByName(reg)
                if not emu.probeMemory(value,16,4):return 'unmapped_header'
                typ=emu.readMemoryPtr(value+8)
                if not emu.probeMemory(typ,16,4):return 'unmapped_type'
                return 'mapped_header_and_type_not_semantically_validated'
            for c in contexts:
                emu.setEmuSnap(c.emu_snap)
                receiver=object_status('rdx');counts['receiver:'+receiver]+=1
                row={'site':hex(c.decoded_at_va),'receiver':receiver}
                if name=='get_attribute':
                    row['attribute_name']=object_status('r8');counts['name:'+row['attribute_name']]+=1
                samples.append(row)
            output[name]={'contexts':len(contexts),'counts':dict(counts),'samples':samples}
        return {'scope':'fresh caller contexts; mapped headers are necessary but insufficient for valid CPython objects',
                'workspace_sha256':hashlib.sha256(workspace.read_bytes()).hexdigest(),
                'original_executed':False,'python_objects_injected':False,'functions':output}
    finally:getter.get_contexts_via_monitor=original


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('workspace',type=Path);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();a.output.write_text(json.dumps(report(a.workspace),indent=2)+'\n')
