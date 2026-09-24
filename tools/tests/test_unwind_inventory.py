import struct
import unittest
from tools.unwind_inventory import unwind_chain


class UnwindTest(unittest.TestCase):
    def parse(self, blob):
        return unwind_chain(lambda rva, size: blob[rva:rva+size], 0)

    def test_odd_code_count_pads_before_handler(self):
        blob = bytes([1 | (3 << 3), 0, 1, 0]) + b'\0'*4 + struct.pack('<I', 0x1234)
        self.assertEqual(self.parse(blob)[0]['handler_rva'], '0x1234')
        self.assertEqual(self.parse(blob)[0]['opaque_data_rva'], '0xc')

    def test_chain_resolves_parent_handler(self):
        blob = bytes([1 | (4 << 3), 0, 0, 0]) + struct.pack('<III', 0x100, 0x200, 16)
        blob += bytes([1 | (1 << 3), 0, 0, 0]) + struct.pack('<I', 0x300)
        rows = self.parse(blob)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[-1]['handler_rva'], '0x300')

    def test_cycle_and_truncation_rejected(self):
        for blob in (b'\1', bytes([33, 0, 0, 0])+struct.pack('<III', 1, 2, 0),
                     bytes([9, 0, 0, 0])):
            with self.assertRaises(ValueError): self.parse(blob)

    def test_conflicting_flags_rejected(self):
        with self.assertRaises(ValueError): self.parse(bytes([41, 0, 0, 0]))

    def test_no_handler_does_not_read_adjacent_bytes(self):
        self.assertEqual(self.parse(bytes([1, 0, 0, 0])),
                         [{'unwind_rva': '0x0', 'version': 1, 'flags': 0}])
