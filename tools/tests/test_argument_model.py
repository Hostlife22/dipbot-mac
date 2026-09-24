import ctypes
import struct
import sys
import unittest
import capstone as cs
from tools.receiver_flow import analyse
from tools.floss_names_model import compact_ascii


class ArgumentModelTest(unittest.TestCase):
    def test_method_vector_uses_r9_and_keeps_attribute_in_r8(self):
        for helper,count in [('method_vector2',2),('method_vector3',3)]:
            # Incoming argv[0]=self, argv[1]=first arg, last values deliberately unknown.
            code=bytearray.fromhex('49 8b 10 49 8b 40 08 48 83 ec 48 48 89 44 24 20 4c 8d 4c 24 20')
            code+=b'\x4c\x8b\x05'+struct.pack('<i',0x3000-(0x1000+len(code)+7))
            code+=b'\xe8'+struct.pack('<i',0x4000-(0x1000+len(code)+5))+b'\xc3'
            md=cs.Cs(cs.CS_ARCH_X86,cs.CS_MODE_64);md.detail=True
            rows=analyse(list(md.disasm(bytes(code),0x1000)),{0x3000:'execute_quote_to_bnb'},{0x4000:helper})
            self.assertEqual(rows[0]['expression'],'arg[0].execute_quote_to_bnb')
            self.assertEqual(rows[0]['arguments'],['arg[1]']+[None]*(count-1))

    def test_keyword_vector_reads_values_in_name_order(self):
        code=bytearray.fromhex('49 8b 00 48 83 ec 38 48 89 44 24 20 4c 8d 44 24 20')
        code+=b'\x4c\x8b\x0d'+struct.pack('<i',0x3000-(0x1000+len(code)+7))
        code+=b'\xe8'+struct.pack('<i',0x4000-(0x1000+len(code)+5))+b'\xc3'
        md=cs.Cs(cs.CS_ARCH_X86,cs.CS_MODE_64);md.detail=True
        rows=analyse(list(md.disasm(bytes(code),0x1000)),{0x3000:['first','missing']},{0x4000:'call_keyword_vector'})
        self.assertEqual(rows[0]['keyword_values'],{'first':'arg[0]','missing':None})

    def test_two_argument_vector_preserves_order_and_unknown(self):
        code=bytearray.fromhex('49 8b 00 48 83 ec 38 48 89 44 24 20 4c 8d 44 24 20')
        code+=b'\xe8'+struct.pack('<i',0x4000-(0x1000+len(code)+5))+b'\xc3'
        md=cs.Cs(cs.CS_ARCH_X86,cs.CS_MODE_64);md.detail=True
        rows=analyse(list(md.disasm(bytes(code),0x1000)),{},{0x4000:'call_vector2'})
        self.assertEqual(rows[0]['arguments'],['arg[0]',None])

    def test_special_method_receiver_and_literal_none_tuple(self):
        # arg[0] -> type-based __exit__ lookup -> call with the constant tuple.
        code=bytearray.fromhex('49 8b 10')
        code+=b'\x4c\x8b\x05'+struct.pack('<i',0x3000-(0x1000+len(code)+7))
        code+=b'\xe8'+struct.pack('<i',0x4000-(0x1000+len(code)+5))
        code+=bytes.fromhex('48 89 c2')
        code+=b'\x4c\x8b\x05'+struct.pack('<i',0x3008-(0x1000+len(code)+7))
        code+=b'\xe8'+struct.pack('<i',0x4010-(0x1000+len(code)+5))+b'\xc3'
        md=cs.Cs(cs.CS_ARCH_X86,cs.CS_MODE_64);md.detail=True
        rows=analyse(list(md.disasm(bytes(code),0x1000)),
                     {0x3000:'__exit__',0x3008:[None,None,None]},
                     {0x4000:'get_special_attribute',0x4010:'call_tuple3'})
        self.assertEqual(rows[1]['callable'],'arg[0].__exit__')
        self.assertEqual(rows[1]['literal_arguments'],[None,None,None])
        self.assertNotIn('keyword_names',rows[1])

    def test_keyword_values_from_constant_tuple_items(self):
        for offset,expected in [(24,{'parents':True,'exist_ok':True}),(16,None)]:
            code=bytearray()
            for prefix,slot in [(b'\x4c\x8b\x05',0x3000),(b'\x4c\x8b\x0d',0x3008)]:
                code+=prefix+struct.pack('<i',slot-(0x1000+len(code)+7))
            code+=bytes([0x49,0x83,0xc0,offset])
            code+=b'\xe8'+struct.pack('<i',0x4000-(0x1000+len(code)+5))+b'\xc3'
            md=cs.Cs(cs.CS_ARCH_X86,cs.CS_MODE_64);md.detail=True
            rows=analyse(list(md.disasm(bytes(code),0x1000)),
                         {0x3000:[True,True],0x3008:['parents','exist_ok']},
                         {0x4000:'call_keyword_vector'})
            self.assertEqual(rows[0].get('keyword_literal_values'),expected)

    @unittest.skipUnless(sys.version_info[:2]==(3,12),'ABI cross-check uses local CPython 3.12')
    def test_ascii_layout_against_live_local_cpython(self):
        # Checks layout only; the local Mac object is never put into the Windows model.
        value=''.join(['fresh_ascii_',str(id(self))])
        actual=ctypes.string_at(id(value),40+len(value)+1)
        model=compact_ascii(value,123)
        self.assertEqual(model[16:24],actual[16:24])
        self.assertEqual(model[40:],actual[40:])
        self.assertEqual(model[32]&0x7c,actual[32]&0x7c)

    def test_ascii_model_rejects_non_ascii_instead_of_wrong_layout(self):
        with self.assertRaises(UnicodeEncodeError):compact_ascii('токен',123)


if __name__=='__main__':unittest.main()
