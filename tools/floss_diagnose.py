"""Observe failed emulated call returns without changing PC, hooks or PE state."""
import argparse
from collections import Counter
import json
from pathlib import Path


def diagnose(workspace,output,fresh_callers=False):
    import floss.main
    import floss.utils
    import floss.function_argument_getter as getter
    import viv_utils
    import viv_utils.emulator_drivers as drivers
    vw=viv_utils.getWorkspace(str(workspace),analyze=False,should_save=False)
    index=viv_utils.InstructionFunctionIndex(vw)
    failures=[];original=drivers.EmulatorDriver.handle_call
    original_context=getter.get_contexts_via_monitor
    initial={}
    def fresh(driver,*args):
        if driver not in initial:initial[driver]=driver._emu.getEmuSnap()
        driver._emu.setEmuSnap(initial[driver])
        return original_context(driver,*args)
    def observe(self,op,*args,**kwargs):
        emu=self._emu;before=emu.getStackCounter();pc=emu.getProgramCounter()
        mapped_before=emu.getMemoryMap(before) is not None
        try:return original(self,op,*args,**kwargs)
        except drivers.StopEmulation:
            after=emu.getStackCounter()
            def read(sp):
                try:return hex(emu.readMemoryPtr(sp))
                except Exception:return None
            failures.append({'site':hex(pc),'instruction':str(op),'sp_delta':after-before,
                             'stack_mapped_before':mapped_before,
                             'pc_after':hex(emu.getProgramCounter()),
                             'original_sp_memory_after_failure':read(before),'return_slot_after_failure':read(before-8)})
            raise
    drivers.EmulatorDriver.handle_call=observe
    if fresh_callers:getter.get_contexts_via_monitor=fresh
    try:
        contexts=getter.extract_decoding_contexts(vw,0x142905d20,index)
        emu=floss.utils.make_emulator(vw);counts=Counter()
        samples=[]
        for context in contexts:
            emu.setEmuSnap(context.emu_snap)
            # Win64 RDX is callable argument for the inferred call0 helper.
            reg=emu.getRegisterByName('rdx')
            taint=emu.getVivTaint(reg)
            quality=('null' if reg==0 else 'filler' if reg==0xfefefefefefefefe else
                     'tainted' if taint else 'mapped' if emu.getMemoryMap(reg) else 'unmapped')
            counts[quality]+=1
            samples.append({'site':hex(context.decoded_at_va),'callable':hex(reg),
                            'mapped':emu.getMemoryMap(reg) is not None,'tainted':bool(taint)})
        result={'scope':'call0 caller-context extraction only; no PC patch or synthetic PyObjects',
                'fresh_initial_snapshot_per_caller':fresh_callers,
                'contexts':len(contexts),'callable_quality':dict(counts),'samples':samples,'failures':failures}
        output.write_text(json.dumps(result,indent=2)+'\n')
    finally:
        drivers.EmulatorDriver.handle_call=original
        getter.get_contexts_via_monitor=original_context


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('workspace',type=Path)
    p.add_argument('--output',type=Path,required=True);p.add_argument('--fresh-callers',action='store_true')
    a=p.parse_args();diagnose(a.workspace,a.output,a.fresh_callers)
