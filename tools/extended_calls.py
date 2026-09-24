"""Release-specific inferred lookup, tuple/kwargs and three-argument vector helpers."""
import argparse
import hashlib
import json
from pathlib import Path
from tools.audit_native import SHA256, constants
from tools.full_static_audit import TABLES
from tools.receiver_flow import RANGES, analyse
from tools.context_workspace import HELPERS

EXTRA={0x140006c30:'dict_slot_lookup',0x140f02590:'global_lookup:reload_runtime_settings',
       0x14000d9e0:'call_tuple_kwargs',0x1429083a0:'call_vector3'}


def report(exe):
    import pefile,capstone as cs
    data=exe.read_bytes()
    if hashlib.sha256(data).hexdigest()!=SHA256:raise ValueError('Unexpected release')
    pe=pefile.PE(data=data);base=pe.OPTIONAL_HEADER.ImageBase
    md=cs.Cs(cs.CS_ARCH_X86,cs.CS_MODE_64);md.detail=True
    slots={t+8*r['index']:r['value'] for m,t in TABLES.items() for r in constants(data,m)
           if isinstance(r['value'],str) and r['value'].isidentifier()}
    helper_ranges={}
    for e in pe.DIRECTORY_ENTRY_EXCEPTION:
        a=base+e.struct.BeginAddress
        if a in EXTRA:
            blob=pe.get_data(e.struct.BeginAddress,e.struct.EndAddress-e.struct.BeginAddress)
            helper_ranges[hex(a)]={'name':EXTRA[a],'end':hex(base+e.struct.EndAddress),'sha256':hashlib.sha256(blob).hexdigest()}
    rows=[]
    for name,(start,end) in RANGES.items():
        for row in analyse(list(md.disasm(pe.get_data(start-base,end-start),start)),slots,{**{v:k for k,v in HELPERS.items()},**EXTRA}):
            if row['helper'] in EXTRA.values() or row.get('callable','').startswith('lookup:'):
                rows.append({'caller':name,**row})
    return {'exe_sha256':SHA256,'original_executed':False,'helpers':helper_ranges,
            'limitations':['lookup names are not proven global values or dynamic class identities','keyword dict and tuple contents are not reconstructed','source ranges remain bounded; no complete program CFG'], 'calls':rows}


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('exe',type=Path);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();a.output.write_text(json.dumps(report(a.exe),indent=2)+'\n')
