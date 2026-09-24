"""Conservative register/local-stack provenance across selected Nuitka CFGs.

arg[n] denotes an incoming R8 argument-vector element, NOT a proven Python type.
No branch feasibility, indirect CFG edges, heap mutations or exception unwinding.
"""
import argparse
from collections import deque
import hashlib
import json
from pathlib import Path
from tools.audit_native import SHA256, constants
from tools.dispatch_graph import RANGES as BASE_RANGES
from tools.full_static_audit import TABLES
from tools.context_workspace import HELPERS

RANGES={**BASE_RANGES,
        'remove_live_pair':(0x140907240,0x1409093a9),
        'wallet_registry_load':(0x142084980,0x1420867bb),
        'wallet_registry_save':(0x142086ef0,0x1420886a2),
        'config_load':(0x140f2c190,0x140f2d28a),
        'config_save':(0x140f2eaf0,0x140f2fee6),
        'apply_autopair_selection':(0x14090c110,0x14090cabe),
        'confirm_manual_selection':(0x140911150,0x1409118ed),
        'dynamic_registry_save':(0x140bb9150,0x140bb9cd9),
        'load_config_values':(0x140f2bf40,0x140f2c011),
        'reload_saved_settings':(0x140f2d290,0x140f2dafd),
        'wallet_read_document':(0x1420867c0,0x142086eee)}


def meet(left, right):
    return {key:value for key,value in left.items() if right.get(key)==value}


def analyse(instructions, slots, helpers):
    import capstone as cs
    if not instructions:return []
    code={ins.address:ins for ins in instructions}
    entry=instructions[0].address
    states={entry:{'r8':'argv','rcx':'tstate','rsp':'stack:0'}}
    queue=deque([entry])
    def register(ins,reg):
        name=ins.reg_name(reg)
        aliases={'eax':'rax','ecx':'rcx','edx':'rdx','ebx':'rbx','esi':'rsi','edi':'rdi','ebp':'rbp','esp':'rsp'}
        for stem,full in [('a','rax'),('b','rbx'),('c','rcx'),('d','rdx')]:
            aliases.update({stem+'x':full,stem+'l':full,stem+'h':full})
        aliases.update({'si':'rsi','sil':'rsi','di':'rdi','dil':'rdi','bp':'rbp','bpl':'rbp','sp':'rsp','spl':'rsp'})
        return aliases.get(name,name[:-1] if name.startswith('r') and name[-1:] in ('d','w','b') else name)
    def stack_offset(ins,op,state):
        if op.type!=cs.CS_OP_MEM or op.mem.index:return None
        base=state.get(register(ins,op.mem.base),'')
        if base.startswith('stack:'):return int(base[6:])+op.mem.disp
        return None
    def read(ins,op,state):
        if op.type==cs.CS_OP_REG:return state.get(register(ins,op.reg))
        if op.type!=cs.CS_OP_MEM:return None
        mem=op.mem
        if mem.index:return None
        offset=stack_offset(ins,op,state)
        if offset is not None:return state.get('mem:'+str(offset))
        if mem.base==cs.x86.X86_REG_RIP:
            value=slots.get(ins.address+ins.size+mem.disp)
            if value is None:return None
            return 'name:'+value if isinstance(value,str) else 'literal:'+json.dumps(value,sort_keys=True)
        base=state.get(register(ins,mem.base))
        if base and base.startswith('dict_slot:') and mem.disp==0 and op.size==8:
            return 'lookup:'+base[10:]
        if base=='argv' and mem.disp>=0 and mem.disp%8==0 and op.size==8:
            return 'arg[%d]'%(mem.disp//8)
        return None
    def transfer(ins,state):
        new=state.copy();ops=ins.operands;row=None;result=None
        if ins.mnemonic=='call':
            target=ops[0].imm if ops[0].type==cs.CS_OP_IMM else None
            helper=helpers.get(target)
            receiver=state.get('rdx');name=state.get('r8')
            if helper:
                row={'site':hex(ins.address),'helper':helper}
                if receiver:row['receiver']=receiver
                if helper.startswith('global_lookup:'):
                    result='lookup:'+helper.split(':',1)[1]
                    row['lookup']=result
                elif helper=='dict_slot_lookup':
                    if receiver and receiver.startswith('name:'):
                        result='dict_slot:'+receiver[5:]
                        row['lookup_name']=receiver[5:]
                elif helper=='copy_constant_dict':
                    if receiver and receiver.startswith('literal:'):
                        result=receiver;row['constant']=receiver
                elif helper in ('call0','call1','call_vector2','call_vector3','call_tuple_kwargs','call_keyword_vector','call_pos_keyword_vectors','call_tuple3'):
                    if receiver and receiver.startswith(('arg[','lookup:')):
                        row['callable']=receiver
                        result=receiver+'()'
                    if helper in ('call_vector2','call_vector3'):
                        count=2 if helper=='call_vector2' else 3
                        vector=state.get('r8','')
                        row['arguments']=[state.get('mem:'+str(int(vector[6:])+8*i)) for i in range(count)] if vector.startswith('stack:') else None
                    if helper=='call_tuple3':
                        row['tuple']=state.get('r8')
                        if row['tuple'] and row['tuple'].startswith('literal:'):
                            values=json.loads(row['tuple'][8:])
                            if isinstance(values,list) and len(values)==3:row['literal_arguments']=values
                    if helper=='call_tuple_kwargs':
                        row['tuple']=state.get('r8');row['kwargs']=state.get('r9')
                    if helper in ('call_keyword_vector','call_pos_keyword_vectors'):
                        sp=state.get('rsp','')
                        names=state.get('r9') if helper=='call_keyword_vector' else state.get('mem:'+str(int(sp[6:])+32)) if sp.startswith('stack:') else None
                        row['keyword_names']=names
                        if helper=='call_pos_keyword_vectors':
                            positional=state.get('r8','')
                            row['positional_values']=[state.get('mem:'+positional[6:])] if positional.startswith('stack:') else None
                        vector=state.get('r8' if helper=='call_keyword_vector' else 'r9','')
                        if names and names.startswith('literal:') and vector.startswith('tuple_items:'):
                            keys=json.loads(names[8:]);values=json.loads(vector[12:])
                            if isinstance(keys,list) and all(isinstance(k,str) for k in keys) and len(keys)==len(values):
                                row['keyword_literal_values']=dict(zip(keys,values))
                        if names and names.startswith('literal:') and vector.startswith('stack:'):
                            keys=json.loads(names[8:])
                            if isinstance(keys,list) and all(isinstance(k,str) for k in keys):
                                row['keyword_values']={k:state.get('mem:'+str(int(vector[6:])+8*i)) for i,k in enumerate(keys)}
                elif name and name.startswith('name:'):
                    row['attribute']=name[5:]
                    if receiver and receiver.startswith(('arg[','lookup:')):
                        expression=receiver+'.'+name[5:]
                        row['expression']=expression
                        if helper in ('get_attribute','getattr_default','get_special_attribute'):result=expression
                        elif helper in ('method_call0','method_call1','method_vector2','method_vector3'):result=expression+'()'
                if helper in ('method_vector2','method_vector3'):
                    count=2 if helper=='method_vector2' else 3
                    vector=state.get('r9','')
                    row['arguments']=[state.get('mem:'+str(int(vector[6:])+8*i)) for i in range(count)] if vector.startswith('stack:') else None
                if helper in ('call1','method_call1','set_attribute'):
                    argument=state.get('r8' if helper=='call1' else 'r9')
                    if argument:row['argument']=argument
            for reg in ('rax','rcx','rdx','r8','r9','r10','r11'):new.pop(reg,None)
            # Escaped local addresses may be modified by any unmodelled callee.
            escaped=any(state.get(reg,'').startswith('stack:') for reg in ('rcx','rdx','r8','r9'))
            sp=state.get('rsp','')
            for key in list(new):
                if key.startswith('mem:') and (escaped or
                    (sp.startswith('stack:') and int(sp[6:])<=int(key[4:])<int(sp[6:])+32)):
                    new.pop(key)
            if result and len(result)<180:new['rax']=result
            return new,row
        value=read(ins,ops[1],state) if ins.mnemonic=='mov' and len(ops)==2 else None
        offset=stack_offset(ins,ops[0],state) if ops else None
        if offset is not None and ins.mnemonic not in ('cmp','test'):
            new.pop('mem:'+str(offset),None)
            if value and ops[0].size==8:new['mem:'+str(offset)]=value
        if ins.mnemonic=='lea' and len(ops)==2:
            source=stack_offset(ins,ops[1],state)
            if source is not None:value='stack:'+str(source)
        if ins.mnemonic in ('sub','add') and len(ops)==2 and ops[0].type==cs.CS_OP_REG and ops[1].type==cs.CS_OP_IMM:
            base=state.get(register(ins,ops[0].reg),'')
            if ins.mnemonic=='add' and ops[1].imm==24 and base.startswith('literal:'):
                container=json.loads(base[8:])
                if isinstance(container,list):value='tuple_items:'+json.dumps(container)
            if base.startswith('stack:'):
                value='stack:'+str(int(base[6:])+ops[1].imm*(1 if ins.mnemonic=='add' else -1))
        _,writes=ins.regs_access()
        for reg in writes:new.pop(register(ins,reg),None)
        if value and ops[0].type==cs.CS_OP_REG and ops[0].size==8:
            new[register(ins,ops[0].reg)]=value
        if ins.mnemonic in ('push','pop') and state.get('rsp','').startswith('stack:'):
            new['rsp']='stack:'+str(int(state['rsp'][6:])+(-8 if ins.mnemonic=='push' else 8))
        return new,None
    while queue:
        addr=queue.popleft();ins=code[addr];out,_=transfer(ins,states[addr])
        successors=[]
        if ins.group(cs.CS_GRP_JUMP):
            if ins.operands[0].type==cs.CS_OP_IMM:successors.append(ins.operands[0].imm)
            if ins.mnemonic!='jmp':successors.append(addr+ins.size)
        elif not ins.group(cs.CS_GRP_RET) and ins.mnemonic not in ('int3','ud2','hlt'):successors.append(addr+ins.size)
        for dest in successors:
            if dest not in code:continue
            merged=meet(states[dest],out) if dest in states else out.copy()
            if dest not in states or merged!=states[dest]:
                states[dest]=merged;queue.append(dest)
    rows=[]
    for addr,state in sorted(states.items()):
        _,row=transfer(code[addr],state)
        if row:rows.append(row)
    return rows


def report(exe):
    import capstone as cs
    import pefile
    data=exe.read_bytes()
    if hashlib.sha256(data).hexdigest()!=SHA256:raise ValueError('Unexpected release')
    pe=pefile.PE(data=data)
    engine=cs.Cs(cs.CS_ARCH_X86,cs.CS_MODE_64);engine.detail=True
    slots={table+8*r['index']:r['value'] for module,table in TABLES.items()
           for r in constants(data,module) if isinstance(r['value'],str) and r['value'].isidentifier()}
    ranges={};seen=set()
    for name,(start,end) in RANGES.items():
        if start in seen:continue
        seen.add(start)
        blob=pe.get_data(start-pe.OPTIONAL_HEADER.ImageBase,end-start)
        instructions=list(engine.disasm(blob,start))
        outside=[{'site':hex(i.address),'target':hex(i.operands[0].imm)} for i in instructions
                 if i.group(cs.CS_GRP_JUMP) and i.operands[0].type==cs.CS_OP_IMM
                 and not start<=i.operands[0].imm<end]
        ranges[name]={'start':hex(start),'end':hex(end),'sha256':hashlib.sha256(blob).hexdigest(),
                      'outside_direct_branches':outside,
                      'calls':analyse(instructions,slots,{v:k for k,v in HELPERS.items()})}
    return {'exe_sha256':SHA256,'original_executed':False,'assumption':'R8 is Nuitka argument vector; arg[n] has no inferred runtime type',
            'limitations':['direct CFG within explicit ranges only','register and local stack provenance; escaped stack values discarded; no heap alias model','getattr_default expression is conditional on attribute existence','expressions identify access paths, not concrete method implementations'],
            'ranges':ranges}


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('exe',type=Path)
    p.add_argument('--output',required=True,type=Path);args=p.parse_args()
    args.output.write_text(json.dumps(report(args.exe),ensure_ascii=False,indent=2)+'\n')
