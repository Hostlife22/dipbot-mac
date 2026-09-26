"""Bounded caching of network identity only; state and prices are never cached."""

import threading
import time

from web3 import HTTPProvider

from dipbot.observability.telemetry import TIMINGS


class BscHTTPProvider(HTTPProvider):
    network_check_interval = 30.0

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._network_lock = threading.Lock()
        self._network_identity = None

    def invalidate_network(self):
        with self._network_lock:
            self._network_identity = None

    def make_request(self, method, params):
        if method == "eth_chainId":
            with self._network_lock:
                cached = self._network_identity
                if (
                    cached is not None
                    and cached[0] == str(self.endpoint_uri)
                    and 0 <= time.monotonic() - cached[1] < self.network_check_interval
                ):
                    return dict(cached[2])
        endpoint = str(self.endpoint_uri)
        started = time.monotonic()
        try:
            # Use an allowlist so caller-controlled strings cannot leak into reports.
            label = (
                method
                if method
                in {
                    "eth_chainId",
                    "eth_call",
                    "eth_getBlockByNumber",
                    "eth_getTransactionReceipt",
                    "eth_getTransactionByHash",
                    "eth_getTransactionCount",
                    "eth_estimateGas",
                    "eth_sendRawTransaction",
                    "eth_getLogs",
                    "eth_getBalance",
                    "eth_blockNumber",
                    "eth_gasPrice",
                }
                else "other"
            )
            with TIMINGS.measure("rpc." + label):
                response = super().make_request(method, params)
                if "error" in response:
                    TIMINGS.record("rpc.json_error", 0, failed=True)
        except Exception:
            self.invalidate_network()
            raise
        if "error" in response:
            self.invalidate_network()
        elif method == "eth_chainId":
            with self._network_lock:
                self._network_identity = (
                    (endpoint, started, dict(response)) if response.get("result") == "0x38" else None
                )
        return response
