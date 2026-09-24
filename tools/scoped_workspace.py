"""Build a non-recursive Vivisect view of selected functions in the full PE.

Not whole-program analysis: callees and decoding call contexts may be missing.
Requires viv-utils 0.8.1 / vivisect 1.3.2 in the audit tools environment.
"""
import argparse
import hashlib
from pathlib import Path
from tools.audit_native import SHA256
from tools.runtime_native import RANGES

FUNCTIONS = {name:start for name,(start,_) in RANGES.items()}
FUNCTIONS.update({'remove_live_pair':0x140907240,'trader_init':0x141e55f60,
                  'converter_slippage':0x141e76010,'read_protected_json':0x141dce190,
                  'write_protected_json':0x141dcd550})


def build(exe, output, functions=None):
    import viv_utils
    if hashlib.sha256(exe.read_bytes()).hexdigest()!=SHA256:
        raise ValueError('Unexpected release')
    vw=viv_utils.getWorkspace(str(exe),analyze=False,should_save=False)
    vw.cfctx._cf_recurse=False  # Version-pinned audit adapter; never global analyze().
    for name,address in (FUNCTIONS if functions is None else functions).items():
        vw.makeFunction(address)
        if not vw.isFunction(address):raise RuntimeError('No function: '+name)
        print(name,hex(address),len(vw.getFunctionBlocks(address)),flush=True)
    vw.setMeta('StorageName',str(output))
    vw.saveWorkspace()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('exe',type=Path);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();build(args.exe,args.output)
