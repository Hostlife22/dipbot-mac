"""Run capa/FLOSS on an explicit non-recursive workspace, not whole-program proof."""
import argparse
import hashlib
import json
from pathlib import Path
from tools.scoped_workspace import FUNCTIONS
from tools.audit_native import SHA256


def run(mode, workspace, exe, rules, output, functions=None):
    if hashlib.sha256(exe.read_bytes()).hexdigest()!=SHA256:
        raise ValueError("Unexpected release")
    import viv_utils
    vw=viv_utils.getWorkspace(str(workspace),analyze=False,should_save=False)
    report={'exe_sha256':SHA256,'scope':'selected functions; non-recursive; incomplete caller/callee contexts',
            'mode':mode,'functions':{}}
    if mode=='capa':
        import capa.rules
        import capa.rules.cache
        from capa.features.extractors.viv.extractor import VivisectFeatureExtractor
        from capa.capabilities.static import find_code_capabilities
        ruleset=capa.rules.get_rules([rules])
        extractor=VivisectFeatureExtractor(vw,exe,'windows')
        handles={int(f.address):f for f in extractor.get_functions()}
    else:
        import floss.main  # Establish the package's supported import order.
        from floss.stackstrings import extract_stackstrings
        from floss.tightstrings import extract_tightstrings
        from floss.string_decoder import decode_strings
        from floss.identify import find_decoding_function_features, get_functions_with_tightloops
    for name,address in (FUNCTIONS if functions is None else functions).items():
        if mode=='capa':
            result=find_code_capabilities(ruleset,extractor,handles[address])
            matches=set(result.function_matches)|set(result.basic_block_matches)|set(result.instruction_matches)
            value={'features':result.feature_count,
                   'rules':sorted(n for n in matches if not ruleset[n].is_subscope_rule()),
                   'internal_subscope_matches':sum(ruleset[n].is_subscope_rule() for n in matches)}
        else:
            features,_=find_decoding_function_features(vw,[address],disable_progress=True)
            tight=get_functions_with_tightloops(features)
            stack=[] if tight else extract_stackstrings(vw,[address],4,disable_progress=True)
            tightstrings=extract_tightstrings(vw,tight,4,disable_progress=True)
            decoded=decode_strings(vw,[address],4,max_insn_count=20000,disable_progress=True)
            value={'stack_strings':len(stack),'tight_strings':len(tightstrings),'decoded_strings':len(decoded),
                   'direct_code_refs':len(vw.getXrefsTo(address))}
        report['functions'][name]={'address':hex(address),**value}
        output.write_text(json.dumps(report,indent=2)+'\n')
        print(name,value,flush=True)
    report['completed']=True
    output.write_text(json.dumps(report,indent=2)+'\n')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode',choices=['capa','floss']);parser.add_argument('workspace',type=Path)
    parser.add_argument('exe',type=Path);parser.add_argument('--rules',type=Path)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();run(args.mode,args.workspace,args.exe,args.rules,args.output)
