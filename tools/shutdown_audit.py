"""Direct static evidence for optional Trader.close and GUI shutdown/REMOVE wiring."""
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


def report(exe):
    import pefile
    import capstone as cs
    data=exe.read_bytes()
    if hashlib.sha256(data).hexdigest()!=SHA256:raise ValueError('Unexpected release')
    pe=pefile.PE(data=data);base=pe.OPTIONAL_HEADER.ImageBase
    md=cs.Cs(cs.CS_ARCH_X86,cs.CS_MODE_64);md.detail=True
    text=next(s for s in pe.sections if s.Name.rstrip(b'\0')==b'.text');raw=text.get_data();lo=base+text.VirtualAddress
    slots={t+8*r['index']:r['value'] for m,t in TABLES.items() for r in constants(data,m)
           if isinstance(r['value'],str) and r['value'].isidentifier()}
    def decode(a):return next(md.disasm(raw[a-lo:a-lo+15],a,count=1),None)
    out={}
    for name,start in [('close_trader',0x142093c20),('gui_close',0x140f37a90),('commercial_close',0x140922010)]:
        ins,unresolved=walk(start,decode,lambda a:lo<=a<lo+len(raw) and abs(a-start)<0x10000)
        out[name]={'entry':hex(start),'instructions':len(ins),'unresolved_direct_edges':unresolved,
                   'sha256':hashlib.sha256(b''.join(i.bytes for i in ins)).hexdigest(),
                   'calls':analyse(ins,slots,{**{v:k for k,v in HELPERS.items()},**EXTRA})}
    a,b=0x1408f7a30,0x1408f7b36
    return {'exe_sha256':SHA256,'original_executed':False,'closures':out,
            'remove_signal_wiring':{'start':hex(a),'end':hex(b),
                'sha256':hashlib.sha256(pe.get_data(a-base,b-a)).hexdigest(),
                'chain':['clicked','connect','remove_current_live_pair'],'connect_call':'0x1408f7b31'},
            'close_trader_semantics':{'optional_getattr':'0x142093cc1','callable_check':'0x142093d1c',
                'invoke':'0x142093d9d','error_restored_to_tstate':'0x142093ee1','null_return':'0x142093eff',
                'scope':'Errors propagate from this helper; caller may catch or replace them'},
            'limitations':['No dynamic dispatch, Qt exception hook or SEH closure',
                            'Optional close does not prove Trader implements it or waits for nested tasks',
                            'No Windows runtime, process crash or transaction comparison']}


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('exe',type=Path);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();a.output.write_text(json.dumps(report(a.exe),indent=2)+'\n')
