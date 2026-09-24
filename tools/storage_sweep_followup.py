"""Public constant/vector arguments and direct CFG of protected writes and Sweep."""
import argparse
import hashlib
import json
from pathlib import Path
from tools.audit_native import SHA256, constants
from tools.full_static_audit import TABLES
from tools.branch_closure import walk
from tools.receiver_flow import analyse
from tools.context_workspace import HELPERS
from tools.extended_calls import EXTRA
from tools.argument_audit import EXTRA_HELPERS
from tools.exception_followup import ADDED
from tools.shared_constants_audit import report as shared_report

ENTRIES={'atomic_write':0x141dcbb00,'write_protected':0x141dcd550,
         'read_protected':0x141dce190,'registry_save':0x140bb9150,
         'sell_target':0x142097550,'convert_base':0x142098890,'sweep_run':0x142099f30}


def report(exe):
    import pefile
    import capstone as cs
    proof=shared_report(exe)  # Validates release and the actual shared zero value.
    data=exe.read_bytes();pe=pefile.PE(data=data);base=pe.OPTIONAL_HEADER.ImageBase
    md=cs.Cs(cs.CS_ARCH_X86,cs.CS_MODE_64);md.detail=True
    sec=next(s for s in pe.sections if s.Name.rstrip(b'\0')==b'.text');raw=sec.get_data();lo=base+sec.VirtualAddress
    slots={t+8*r['index']:r['value'] for m,t in TABLES.items() for r in constants(data,m)
           if isinstance(r['value'],str) and r['value'].isidentifier()}
    for index in (84,85,91,100,102,105,106,109,119,120):
        slots[TABLES['secure_store']+8*index]=constants(data,'secure_store')[index]['value']
    slots[0x1429a02b0]=proof['constant']['value']
    def decode(a):return next(md.disasm(raw[a-lo:a-lo+15],a,count=1),None)
    out={}
    for name,start in ENTRIES.items():
        ins,unknown=walk(start,decode,lambda a:lo<=a<lo+len(raw) and abs(a-start)<0x10000)
        zero_refs=[hex(i.address) for i in ins for op in i.operands if op.type==cs.CS_OP_MEM
                   and op.mem.base==cs.x86.X86_REG_RIP and i.address+i.size+op.mem.disp==0x1429a02b0]
        out[name]={'entry':hex(start),'instructions':len(ins),'unresolved_direct_edges':unknown,
                   'sha256':hashlib.sha256(b''.join(i.bytes for i in ins)).hexdigest(),
                   'shared_zero_references':zero_refs,
                   'calls':analyse(ins,slots,{**{v:k for k,v in HELPERS.items()},**EXTRA,**EXTRA_HELPERS,**ADDED})}
    return {'exe_sha256':SHA256,'original_executed':False,'closures':out,
            'limitations':['Direct CFG only; dynamic calls and SEH not closed',
                            'Literal vectors assume validated CPython tuple layout; arbitrary heap pointers not interpreted',
                            'File replacement is not in-memory registry rollback'],
            'observations':{'target_balance_guard':'0x14209b1cf compares <=0 and enters zero_balance branch',
                            'base_balance_guard':'0x14209d168 compares <=0 and skips conversion',
                            'quote_guard':'0x142097aed compares <=0 and raises live target-token quote is zero',
                            'storage_sequence':'write, flush, fsync, close context, replace; error path includes unlink(missing_ok=True)'}}


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('exe',type=Path);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();a.output.write_text(json.dumps(report(a.exe),indent=2)+'\n')
