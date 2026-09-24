"""Hash-locked arithmetic helpers, save arguments and GUI failure reachability."""
import argparse
import hashlib
import json
from pathlib import Path
from tools.audit_native import SHA256, constants
from tools.full_static_audit import TABLES
from tools.receiver_flow import analyse
from tools.branch_closure import walk
from tools.context_workspace import HELPERS
from tools.extended_calls import EXTRA
from tools.argument_audit import EXTRA_HELPERS
from tools.exception_followup import ADDED


def report(exe):
    import pefile
    import capstone as cs
    data=exe.read_bytes()
    if hashlib.sha256(data).hexdigest()!=SHA256:raise ValueError('Unexpected release')
    factor=constants(data,'trader')[131]['value']
    if type(factor) is not int or factor!=10000:raise ValueError('Unexpected loss factor')
    pe=pefile.PE(data=data);base=pe.OPTIONAL_HEADER.ImageBase
    ends={base+e.struct.BeginAddress:base+e.struct.EndAddress for e in pe.DIRECTORY_ENTRY_EXCEPTION}
    md=cs.Cs(cs.CS_ARCH_X86,cs.CS_MODE_64);md.detail=True
    sec=next(s for s in pe.sections if s.Name.rstrip(b'\0')==b'.text');raw=sec.get_data();lo=base+sec.VirtualAddress
    slots={t+8*r['index']:r['value'] for m,t in TABLES.items() for r in constants(data,m)
           if isinstance(r['value'],str) and r['value'].isidentifier()}
    slots[0x1429a02b0]=0
    defs={'loss':0x141e75b60,'subtract':0x1429195a0,'multiply':0x1429178d0,
          'floor_divide':0x142916a40,'call_vector2':0x142907260,'purpose_lookup':0x140baa280,
          'registry_save':0x140bb9150}
    ranges={}
    helpers={**{v:k for k,v in HELPERS.items()},**EXTRA,**EXTRA_HELPERS,**ADDED,
             0x142907260:'call_vector2',0x140baa280:'global_lookup:REGISTRY_PURPOSE'}
    for name,start in defs.items():
        blob=pe.get_data(start-base,ends[start]-start)
        instructions=list(md.disasm(blob,start))
        ranges[name]={'start':hex(start),'end':hex(ends[start]),'sha256':hashlib.sha256(blob).hexdigest(),
                      'calls':analyse(instructions,slots,helpers)}
    def decode(a):return next(md.disasm(raw[a-lo:a-lo+15],a,count=1),None)
    ins,unresolved=walk(0x1409079d3,decode,lambda a:lo<=a<lo+len(raw) and abs(a-0x1409079d3)<0x10000)
    reached={i.address for i in ins}
    return {'exe_sha256':SHA256,'original_executed':False,'ranges':ranges,
            'arithmetic':{'subtract_slot':'0x08','multiply_slot':'0x10','floor_divide_slot':'0xe8',
                          'number_methods_offset':'0x60','loss_factor':factor,
                          'domain':'integer raw amounts; nb_floor_divide, final PyNumber_Long'},
            'gui_failure':{'entry':'0x1409079d3','instructions':len(ins),'unresolved':unresolved,
                           'sha256':hashlib.sha256(b''.join(i.bytes for i in ins)).hexdigest(),
                           'reaches_catalog_refresh':0x1409088ea in reached,
                           'reaches_autopair_reschedule':0x140908d9a in reached},
            'manual_save_vector':{'call':'0x140bb9ace','argument_0':'self.path',
                                  'argument_1':'constructed document; contents not fully resolved',
                                  'argument_2':'global REGISTRY_PURPOSE'},
            'limitations':['Normal CPython integer semantics; globals may be rebound',
                            'GUI direct edges only; callees and SEH not traversed',
                            'Save argument provenance does not prove full rollback or absence thereof']}


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('exe',type=Path);p.add_argument('--output',required=True,type=Path)
    a=p.parse_args();a.output.write_text(json.dumps(report(a.exe),indent=2)+'\n')
