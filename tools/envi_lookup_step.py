"""Narrow ISA correction for Envi's MOV rax, sign-extended imm32 decoding.

Only the exact observed seven-byte instruction is corrected. No original code,
function return, external call, branch condition or memory content is patched.
"""
def step(emu):
    pc = emu.getProgramCounter()
    if emu.readMemory(pc, 7) == bytes.fromhex('48c7c0ffffffff'):
        emu.setRegisterByName('rax', 0xffffffffffffffff)
        emu.setProgramCounter(pc+7)
        return True
    emu.stepi()
    return False
