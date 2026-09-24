import struct
import unittest
import capstone as cs
from tools.global_lookup_audit import signature


class LookupSignatureTest(unittest.TestCase):
    def sig(self, blob, start=0x1000):
        md=cs.Cs(cs.CS_ARCH_X86,cs.CS_MODE_64);md.detail=True
        return signature(list(md.disasm(blob,start)),start,start+len(blob))

    def test_rip_slots_and_internal_branches_can_move(self):
        self.assertEqual(self.sig(bytes.fromhex('488b0d010000007400c3')),
                         self.sig(bytes.fromhex('488b0d020000007400c3'),0x2000))

    def test_different_external_callee_rejected(self):
        self.assertNotEqual(self.sig(bytes.fromhex('e801000000c3')),
                            self.sig(bytes.fromhex('e802000000c3')))

    def test_same_external_callee_after_relocation_matches(self):
        self.assertEqual(self.sig(b'\xe8'+struct.pack('<i',0x3000-0x1005)+b'\xc3'),
                         self.sig(b'\xe8'+struct.pack('<i',0x3000-0x2005)+b'\xc3',0x2000))

    def test_memory_field_and_opcode_changes_rejected(self):
        self.assertNotEqual(self.sig(bytes.fromhex('488b4120c3')),self.sig(bytes.fromhex('488b4128c3')))
        self.assertNotEqual(self.sig(bytes.fromhex('488b0d01000000c3')),self.sig(bytes.fromhex('488d0d01000000c3')))
