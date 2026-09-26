"""RPC allowlist used by isolated read-only verification."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from web3.providers import BaseProvider
    from web3.types import RPCEndpoint

from collections import Counter

ALLOWED = {
    "eth_chainId",
    "eth_getBlockByNumber",
    "eth_getCode",
    "eth_call",
    "eth_getLogs",
    "eth_getBlockByHash",
}


def guard_provider(provider: BaseProvider) -> Counter[str]:
    calls: Counter[str] = Counter()
    original = provider.make_request

    def read_only(method: RPCEndpoint, params: Any) -> Any:
        if method not in ALLOWED:
            raise RuntimeError("Non-read RPC method blocked: " + method)
        calls[method] += 1
        return original(method, params)

    setattr(provider, "make_request", read_only)
    return calls
