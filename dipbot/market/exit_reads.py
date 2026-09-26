"""Bounded retries for read-only exit preparation, never transaction submission."""

import time

from requests.exceptions import ConnectionError, HTTPError, Timeout
from web3.exceptions import BlockNotFound, Web3RPCError


class ExitReadCancelled(RuntimeError):
    pass


def transient(exc):
    # BlockNotFound is a Web3RPCError subclass; classify it before generic codes.
    if isinstance(exc, BlockNotFound):
        return True
    if isinstance(exc, HTTPError):
        return getattr(exc.response, "status_code", None) in (429, 500, 502, 503, 504)
    if isinstance(exc, Web3RPCError):
        response = getattr(exc, "rpc_response", None) or {}
        return response.get("error", {}).get("code") in (-32005, -32016)
    return isinstance(exc, (ConnectionError, Timeout, TimeoutError, BlockNotFound))


def retry_read(read, *, cancelled, wait, notify, delays=(0.5, 1.0, 2.0)):
    """One initial attempt plus three retries. Callback must perform reads only."""
    try:
        for attempt in range(len(delays) + 1):
            if cancelled():
                raise ExitReadCancelled("STOP: ожидание котировки выхода прервано")
            try:
                return read(attempt)
            except Exception as exc:
                if not transient(exc) or attempt == len(delays):
                    raise
                delay = delays[attempt]
                notify(
                    {
                        "error": type(exc).__name__,
                        "attempt": attempt + 1,
                        "limit": len(delays),
                        "retry_at": time.monotonic() + delay,
                    }
                )
                if wait(delay) or cancelled():
                    raise ExitReadCancelled("STOP: ожидание котировки выхода прервано") from None
    finally:
        notify(None)
