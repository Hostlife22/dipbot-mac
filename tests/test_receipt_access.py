from types import SimpleNamespace
import pytest
from hexbytes import HexBytes
from dipbot.chain import Chain


def test_receipt_access_rejects_read_restricted_endpoint_before_live():
    chain=object.__new__(Chain);chain.check=lambda **kw:100
    tx=HexBytes('0x'+'12'*32)
    def denied(*a):raise RuntimeError('provider URL and private token must not leak')
    chain.w3=SimpleNamespace(eth=SimpleNamespace(
        get_block=lambda number:{'number':number,'transactions':[tx]},get_transaction_receipt=denied))
    with pytest.raises(ValueError,match='транзакция не отправлена') as exc:
        chain.check_receipt_access()
    assert 'private token' not in str(exc.value)


def test_receipt_access_checks_hash_and_block():
    chain=object.__new__(Chain);chain.check=lambda **kw:100
    tx=HexBytes('0x'+'12'*32)
    result={'transactionHash':tx,'blockNumber':97,'status':1}
    chain.w3=SimpleNamespace(eth=SimpleNamespace(
        get_block=lambda number:{'number':number,'transactions':[tx]},
        get_transaction_receipt=lambda h:result))
    chain.check_receipt_access()
    result['blockNumber']=96
    with pytest.raises(ValueError):chain.check_receipt_access()
