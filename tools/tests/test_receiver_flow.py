"""Synthetic machine-code regressions; run with the isolated Capstone environment."""
import struct
import unittest
import capstone
from tools.receiver_flow import analyse

BASE=0x1000
SLOT=0x3000
HELPER=0x4000


def decode(prefix):
    code=bytearray.fromhex(prefix)
    code+=b'\x4c\x8b\x05'+struct.pack('<i',SLOT-(BASE+len(code)+7))
    code+=b'\xe8'+struct.pack('<i',HELPER-(BASE+len(code)+5))+b'\xc3'
    md=capstone.Cs(capstone.CS_ARCH_X86,capstone.CS_MODE_64);md.detail=True
    return analyse(list(md.disasm(bytes(code),BASE)),{SLOT:'stop'},{HELPER:'method_call0'})[-1]


class ReceiverFlowTest(unittest.TestCase):
    def test_argument_receiver(self):
        self.assertEqual(decode('49 8b 18 48 89 da')['expression'],'arg[0].stop')

    def test_join_drops_disagreeing_receivers(self):
        # if (eax) rdx=arg[0]; else rdx=arg[1]
        self.assertNotIn('expression',decode('85 c0 74 05 49 8b 10 eb 04 49 8b 50 08'))

    def test_join_preserves_identical_receivers(self):
        self.assertEqual(decode('85 c0 74 05 49 8b 10 eb 03 49 8b 10')['expression'],'arg[0].stop')

    def test_partial_register_write_invalidates_receiver(self):
        self.assertNotIn('expression',decode('49 8b 10 b2 01'))

    def test_stack_spill_preserves_provenance(self):
        self.assertEqual(decode('48 83 ec 38 49 8b 00 48 89 44 24 20 48 8b 54 24 20')['expression'],'arg[0].stop')

    def test_unknown_call_clobbers_volatile_receiver(self):
        self.assertNotIn('expression',decode('49 8b 10 e8 00 70 00 00'))


if __name__=='__main__':unittest.main()
