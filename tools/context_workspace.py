"""Expand audited callers and helpers; no synthetic call edges or Windows execution."""
from tools.scoped_workspace import FUNCTIONS, build
from tools.remaining_native import RANGES

HELPERS = {
 'method_call0':0x142911880,'method_call1':0x1429119e0,
 'get_attribute':0x1429125c0,'getattr_default':0x1428fccf0,
 'has_attribute':0x1429128d0,'set_attribute':0x142912980,
 'call0':0x142905d20,'call1':0x142906280,
}
GRAPH = {**FUNCTIONS, **{k:v[0] for k,v in RANGES.items() if k not in ('getattr_default_prefix_only',)}, **HELPERS}


def prepare(exe, output):
    build(exe,output,GRAPH)


if __name__=='__main__':
    import argparse
    from pathlib import Path
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('exe',type=Path)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    prepare(args.exe,args.output)
