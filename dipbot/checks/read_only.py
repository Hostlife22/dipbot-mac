"""RPC allowlist used by isolated read-only verification."""
from collections import Counter

ALLOWED = {'eth_chainId','eth_getBlockByNumber','eth_getCode','eth_call','eth_getLogs','eth_getBlockByHash'}


def guard_provider(provider):
    calls = Counter()
    original = provider.make_request
    def read_only(method, params):
        if method not in ALLOWED:
            raise RuntimeError('Non-read RPC method blocked: '+method)
        calls[method] += 1
        return original(method, params)
    provider.make_request = read_only
    return calls
