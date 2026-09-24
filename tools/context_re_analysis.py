"""Observe actual FLOSS context extraction on expanded callers, without injecting edges."""
import json
from pathlib import Path
from tools.context_workspace import GRAPH,HELPERS
from tools.scoped_re_analysis import run


def analyse(mode, root, exe, rules, fresh_callers=False):
    output=root/(mode+'-expanded.json')
    if mode=='capa':
        return run(mode,root/'expanded.viv',exe,rules,output,GRAPH)
    import floss.main
    import floss.string_decoder as decoder
    import floss.function_argument_getter as getter
    original=decoder.extract_decoding_contexts
    original_context=getter.get_contexts_via_monitor
    initial={}
    def fresh(driver,*args):
        if driver not in initial:initial[driver]=driver._emu.getEmuSnap()
        driver._emu.setEmuSnap(initial[driver])
        return original_context(driver,*args)
    counts={}
    def observe(vw,address,index):
        contexts=original(vw,address,index)
        counts[hex(address)]=len(contexts)
        (root/'floss-context-counts.json').write_text(json.dumps(counts,indent=2)+'\n')
        return contexts
    decoder.extract_decoding_contexts=observe
    if fresh_callers:getter.get_contexts_via_monitor=fresh
    try:
        run(mode,root/'expanded.viv',exe,rules,output,HELPERS)
    finally:
        decoder.extract_decoding_contexts=original
        getter.get_contexts_via_monitor=original_context


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode',choices=('capa','floss'))
    parser.add_argument('exe',type=Path)
    parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--rules',type=Path,required=True)
    parser.add_argument('--fresh-callers',action='store_true')
    args=parser.parse_args()
    analyse(args.mode,args.root,args.exe,args.rules,args.fresh_callers)
