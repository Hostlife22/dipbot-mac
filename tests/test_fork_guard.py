import pytest
from tools.fork_roundtrip import validate_read_request


@pytest.mark.parametrize('method',['eth_sendRawTransaction','eth_sendTransaction','personal_sendTransaction','anvil_setBalance','wallet_sendCalls','debug_setHead'])
def test_proxy_rejects_writes_even_hidden_in_batch(method):
    with pytest.raises(ValueError):
        validate_read_request([{'method':'eth_getCode'}, {'method':method}])


def test_proxy_allows_bounded_state_reads():
    rows=[{'method':'eth_getStorageAt'},{'method':'eth_getBalance'}]
    assert validate_read_request(rows)==rows
    with pytest.raises(ValueError):validate_read_request(rows*51)
