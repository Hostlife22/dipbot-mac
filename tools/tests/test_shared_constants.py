import struct
import unittest
from tools.shared_constants_audit import find_stream


def record(name, payload):
    return name+b'\0'+struct.pack('<I', len(payload))+payload


class SharedStreamTests(unittest.TestCase):
    def test_skip_unrelated_payload_without_decoding_it(self):
        blob=b'\0'*8+record(b'opaque',b'\0not-a-stream\0')+record(b'',b'\x03\0li\0.')
        start, payload=find_stream(blob,b'')
        self.assertEqual(blob[start:],payload)
        self.assertEqual(payload,b'\x03\0li\0.')

    def test_truncated_and_out_of_bounds_records(self):
        for blob in (b'',b'\0'*8+b'x',b'\0'*8+b'\0\xff\xff\xff\x7f',
                     b'\0'*8+record(b'',b'x')):
            with self.subTest(blob=blob):
                with self.assertRaises(ValueError): find_stream(blob,b'')

    def test_missing_stream(self):
        with self.assertRaises(ValueError):
            find_stream(b'\0'*8+record(b'other',b'\0\0.'),b'')
