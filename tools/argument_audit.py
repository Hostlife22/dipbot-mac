"""Trace selected GUI construction/save arguments; literal values come from PE constants."""
import argparse
import hashlib
import json
from pathlib import Path
from tools.audit_native import SHA256, constants
from tools.full_static_audit import TABLES
from tools.receiver_flow import analyse
from tools.context_workspace import HELPERS
from tools.extended_calls import EXTRA

RANGES={'gui_construct':(0x140f0b550,0x140f0ca67),
        'start_bot':(0x140f54250,0x140f55f64),'ui_save':(0x140f35db0,0x140f374a0)}
EXTRA_HELPERS={0x140f01dd0:'global_lookup:Trader',0x140f00bc0:'global_lookup:DipBot',
               0x14290f950:'call_keyword_vector',0x14290fcd0:'call_pos_keyword_vectors',
               0x1428fd940:'copy_constant_dict'}


def report(exe):
    import pefile,capstone as cs
    data=exe.read_bytes()
    if hashlib.sha256(data).hexdigest()!=SHA256:raise ValueError('Unexpected release')
    p=pefile.PE(data=data);base=p.OPTIONAL_HEADER.ImageBase
    md=cs.Cs(cs.CS_ARCH_X86,cs.CS_MODE_64);md.detail=True
    # Only public GUI scalar names and the argument-name/serialization constants.
    slots={TABLES['gui']+8*r['index']:r['value'] for r in constants(data,'gui')
           if (isinstance(r['value'],str) and r['value'].isidentifier()) or r['index'] in (126,634,635,840)}
    rows={}
    for name,(start,end) in RANGES.items():
        blob=p.get_data(start-base,end-start)
        calls=analyse(list(md.disasm(blob,start)),slots,{**{v:k for k,v in HELPERS.items()},**EXTRA,**EXTRA_HELPERS})
        rows[name]={'start':hex(start),'end':hex(end),'sha256':hashlib.sha256(blob).hexdigest(),'calls':calls}
    return {'exe_sha256':SHA256,'original_executed':False,
            'scope':'selected GUI ranges, known helper hypotheses; constructor provenance does not exclude later reassignment; unknown values remain null',
            'ranges':rows}


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('exe',type=Path);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();a.output.write_text(json.dumps(report(a.exe),indent=2)+'\n')
