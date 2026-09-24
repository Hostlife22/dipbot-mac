"""Run with the FLOSS Python environment; no reference executable required."""
import unittest
import envi.archs.amd64
from tools.envi_lookup_step import step


class EnviSignExtensionTest(unittest.TestCase):
    def emulator(self, code):
        emu = envi.archs.amd64.Amd64Module().getEmulator()
        emu.addMemoryMap(0x1000,7,'synthetic',bytes.fromhex(code)+b'\x90'*16)
        emu.setProgramCounter(0x1000)
        return emu

    def test_negative_imm32_extends_and_sets_no_flags(self):
        emu=self.emulator('48c7c0ffffffff')
        flags=emu.getRegisterByName('eflags')
        self.assertTrue(step(emu))
        self.assertEqual(emu.getRegisterByName('rax'),0xffffffffffffffff)
        self.assertEqual(emu.getRegisterByName('eflags'),flags)
        self.assertEqual(emu.getProgramCounter(),0x1007)

    def test_sign_branch_uses_real_test_instruction(self):
        emu=self.emulator('48c7c0ffffffff4885c078029090')
        step(emu);step(emu);step(emu)
        self.assertEqual(emu.getProgramCounter(),0x100e)

    def test_32bit_mov_is_not_sign_extended(self):
        emu=self.emulator('b8ffffffff')
        self.assertFalse(step(emu))
        self.assertEqual(emu.getRegisterByName('rax'),0xffffffff)


if __name__=='__main__':unittest.main()
