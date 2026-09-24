import struct
import unittest
import capstone as cs
from tools.branch_closure import walk
from tools.receiver_flow import analyse


class BranchClosureTest(unittest.TestCase):
    def instructions(self,data):
        md=cs.Cs(cs.CS_ARCH_X86,cs.CS_MODE_64);md.detail=True
        return lambda addr:next(md.disasm(data[addr-0x1000:],addr,count=1),None)

    def test_follows_jump_skips_unreachable_bytes(self):
        code,missing=walk(0x1000,self.instructions(bytes.fromhex('eb 02 cc cc c3')),lambda a:0x1000<=a<0x1005)
        self.assertEqual([i.address for i in code],[0x1000,0x1004]);self.assertEqual(missing,[])

    def test_does_not_follow_call_target(self):
        code,missing=walk(0x1000,self.instructions(bytes.fromhex('e8 00 10 00 00 c3')),lambda a:0x1000<=a<0x1006)
        self.assertEqual(len(code),2);self.assertEqual(missing,[])

    def test_out_of_scope_target_reported(self):
        _,missing=walk(0x1000,self.instructions(bytes.fromhex('eb 7f')),lambda a:0x1000<=a<0x1002)
        self.assertEqual(missing,['0x1081'])

    def test_traps_do_not_fall_through_into_adjacent_function(self):
        for trap in ('cc','0f 0b','f4'):
            data=bytes.fromhex(trap+' e8 00 00 00 00 c3')
            code,missing=walk(0x1000,self.instructions(data),lambda a:0x1000<=a<0x1000+len(data))
            self.assertEqual(len(code),1)
            self.assertEqual(missing,['trap@0x1000'])
            md=cs.Cs(cs.CS_ARCH_X86,cs.CS_MODE_64);md.detail=True
            ins=list(md.disasm(data,0x1000))
            target=next(i for i in ins if i.mnemonic=='call').operands[0].imm
            self.assertEqual(analyse(ins,{}, {target:'call0'}),[])

    def test_cached_global_result_reaches_call(self):
        data=b'\xe8'+struct.pack('<i',0x2000-0x1005)+b'\x48\x89\xc2'
        data+=b'\xe8'+struct.pack('<i',0x3000-0x100d)+b'\xc3'
        md=cs.Cs(cs.CS_ARCH_X86,cs.CS_MODE_64);md.detail=True
        rows=analyse(list(md.disasm(data,0x1000)),{}, {0x2000:'global_lookup:reload_runtime_settings',0x3000:'call0'})
        self.assertEqual(rows[-1]['callable'],'lookup:reload_runtime_settings')


if __name__=='__main__':unittest.main()
