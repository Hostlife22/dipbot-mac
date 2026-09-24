import pytest
from tools.audit_native import Decoder, inspect


def test_previous_container_scope_and_string_offsets():
    decoder = Decoder(b'aouter\0T\x02ainner\0pp')
    assert decoder.read() == 'outer'
    assert decoder.read() == ['inner', 'inner']
    assert decoder.read() == ['inner', 'inner']
    assert decoder.pos == len(decoder.data)


def test_dictionary_keys_and_values_have_separate_previous_scope():
    assert Decoder(b'D\x02aa\0ab\0i\x03p').read() == {'dict': [('a', 3), ('b', 3)]}


def test_truncated_or_unknown_constant_fails_closed():
    for raw in (b'f\0', b'?', b'T\xff\xff', b'v\x04abc'):
        with pytest.raises(ValueError):
            Decoder(raw).read()


def test_native_offsets_rejected_for_different_binary(tmp_path):
    path = tmp_path / 'other.exe'
    path.write_bytes(b'MZ not the audited executable')
    with pytest.raises(ValueError, match='Different release'):
        inspect(path)
