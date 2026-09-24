"""Controlled FLOSS pass with optional exact MOV imm32 sign-extension correction."""
import argparse
from collections import Counter
import hashlib
import json
import logging
from pathlib import Path
from tools.context_re_analysis import analyse


def run(exe, workspace, output_dir, corrected=False, trace_memcpy=False):
    import floss.main
    from envi.archs.i386.emu import IntelEmulator
    import viv_utils.emulator_drivers as drivers
    output_dir.mkdir(parents=True, exist_ok=True)
    link=output_dir/'expanded.viv'
    if not link.exists(): link.symlink_to(workspace.resolve())
    if link.resolve()!=workspace.resolve(): raise ValueError('Workspace link mismatch')
    fixes=Counter();errors=[]
    original=IntelEmulator.executeOpcode
    original_call=drivers.EmulatorDriver.handle_call
    copies=[]
    def handle_call(self, op, *args, **kwargs):
        if op.va not in (0x142905efc,0x14290635d,0x142906cc1):
            return original_call(self,op,*args,**kwargs)
        emu=self._emu
        dest,source,size=[emu.getRegisterByName(r) for r in ('rcx','rdx','r8')]
        sp=emu.getStackCounter()
        record={'site':hex(op.va),'dest':hex(dest),'source':hex(source),'requested_size':size,
                'sp_before':hex(sp),'return_slot':hex(sp-8),
                'requested_copy_overlaps_return_slot':dest <= sp-8 < dest+size,
                'dest_mapped':emu.getMemoryMap(dest) is not None,
                'source_mapped':emu.getMemoryMap(source) is not None}
        try:
            return original_call(self,op,*args,**kwargs)
        except drivers.StopEmulation:
            record['stop_emulation']=True
            raise
        finally:
            record['pc_after']=hex(emu.getProgramCounter())
            record['sp_delta']=emu.getStackCounter()-sp
            try:record['return_memory_after']=hex(emu.readMemoryPtr(sp-8))
            except Exception:record['return_memory_after']=None
            copies.append(record)
    class Observe(logging.Handler):
        def emit(self, record):
            message=record.getMessage()
            if 'hook failed to restore PC correctly after call' in message:
                errors.append(message)
    observer=Observe()
    root=logging.getLogger();root.addHandler(observer)
    def execute(self, op):
        if op.mnem=='mov' and self.readMemory(op.va,op.size)==bytes.fromhex('48c7c0ffffffff'):
            fixes[hex(op.va)]+=1
            self.setRegisterByName('rax',0xffffffffffffffff)
            self.setProgramCounter(op.va+op.size)
            return
        return original(self,op)
    if corrected: IntelEmulator.executeOpcode=execute
    if trace_memcpy:drivers.EmulatorDriver.handle_call=handle_call
    try:
        analyse('floss',output_dir,exe,None,fresh_callers=True)
    finally:
        IntelEmulator.executeOpcode=original
        drivers.EmulatorDriver.handle_call=original_call
        root.removeHandler(observer)
    report={'workspace_sha256':hashlib.sha256(workspace.read_bytes()).hexdigest(),
            'corrected':corrected,'fresh_callers':True,'original_executed':False,
            'isa_correction_hits':dict(fixes),'return_errors':errors,
            'memcpy_trace_enabled':trace_memcpy,'memcpy_calls':copies,
            'contexts':json.loads((output_dir/'floss-context-counts.json').read_text()),
            'floss':json.loads((output_dir/'floss-expanded.json').read_text())}
    (output_dir/'comparison.json').write_text(json.dumps(report,indent=2)+'\n')
    return report


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('exe',type=Path)
    p.add_argument('--workspace',type=Path,required=True);p.add_argument('--output-dir',type=Path,required=True)
    p.add_argument('--corrected',action='store_true');p.add_argument('--trace-memcpy',action='store_true');a=p.parse_args()
    r=run(a.exe,a.workspace,a.output_dir,a.corrected,a.trace_memcpy)
    print('contexts',sum(r['contexts'].values()),'return_errors',len(r['return_errors']),flush=True)
