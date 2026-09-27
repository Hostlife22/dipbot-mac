"""Bounded asynchronous public-market recordings, never credentials or signed data."""

from __future__ import annotations

import json
import os
import queue
import threading
import time
import uuid
from pathlib import Path
from typing import Any

FIELDS = {
    "cycle_latency": {
        "schema",
        "cycle_id",
        "origin",
        "head",
        "operation_outcome",
        "observation",
        "unavailable",
        "rpc",
        "rpc_dropped",
        "action",
        "mode",
        "signal_block",
        "block_to_signal_ms",
        "stages",
        "error_type",
        "truncated",
    },
    "quote": {
        "purpose",
        "side",
        "amount_in_raw",
        "amount_out_raw",
        "reverse_out_raw",
        "block",
        "block_hash",
        "pool",
    },
    "activity": {"count", "from_block", "to_block", "pool"},
    "stream_gap": {"previous_block", "new_block", "discontinuity"},
    "backfill": {"pool", "from_block", "to_block", "truncated", "events", "count", "error_type"},
    "price": {"price", "block", "block_hash", "block_timestamp", "source", "pool_state"},
    "observation": {"price", "block", "block_hash", "quote_usd", "quote_usd_observed_at"},
    "signal": {"action", "price", "base", "entry"},
    "execution": {"side", "price", "reason"},
    "read_error": {"type"},
}


class ArchiveFull(OSError):
    pass


class MarketTape:
    # Reserve segment capacity across recorders in this process. Closed files
    # still count toward the directory quota; no history is silently deleted.
    _quota_lock = threading.Lock()
    _leases: dict[Path, int] = {}

    def __init__(
        self,
        directory: Path,
        metadata: dict[str, Any],
        *,
        max_bytes: Any = 10 * 1024**2,
        max_total_bytes: Any = 200 * 1024**2,
        capacity: int = 1024,
    ) -> None:
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.directory = directory.resolve()
        self.segment_limit = max_bytes
        self.total_limit = max_total_bytes
        self.session_id = uuid.uuid4().hex
        self.paths: list[Path] = []
        self.segment = 0
        self.segment_written = 0
        self.last_written_sequence = 0
        self.full = False
        self.queue: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=capacity)
        self.stop_event = threading.Event()
        self.started = time.monotonic()
        self.sequence = self.dropped = self.written = 0
        self.error_type = ""
        self.completed = False
        allowed = {
            "mode",
            "pool",
            "signal_policy",
            "settings",
            "starts_with_position",
            "sizing",
            "requested_amount",
            "exit_policy",
            "entry_cost_policy",
            "paper_policy",
        }
        metadata = dict(metadata)
        nested = {
            "paper_policy": {"latency_seconds", "fee_quote", "gas_units"},
            "entry_cost_policy": {"maximum_pct", "roundtrip_gas"},
            "exit_policy": {
                "continue_after_risk_exit",
                "tp_sl_basis",
                "trailing_pct",
                "max_hold_seconds",
                "cooldown_seconds",
            },
            "sizing": {"unit", "reserve_bnb"},
            "pool": {
                "address",
                "router",
                "token",
                "quote",
                "token_decimals",
                "quote_decimals",
                "token_is_0",
                "fee",
            },
            "settings": {
                "amount",
                "dip",
                "take_profit",
                "stop_loss",
                "slippage",
                "dynamic",
                "max_gap",
                "max_roundtrip_loss",
                "min_swaps",
            },
            "signal_policy": {
                "mode",
                "window_seconds",
                "rebound_pct",
                "max_block_age",
                "volatility_multiplier",
            },
        }
        for name, keys in nested.items():
            if isinstance(metadata.get(name), dict):
                metadata[name] = {k: v for k, v in metadata[name].items() if k in keys}
        self.metadata = {k: v for k, v in metadata.items() if k in allowed}
        self.stream, self.current_path, self.max_bytes, self.bytes_written = self.open_segment(0, None)
        self.path = self.current_path  # Stable entry point for loading the entire session.
        self.thread = threading.Thread(target=self.run, name="market-recorder", daemon=True)
        self.thread.start()

    def open_segment(self, index: int, previous: Any) -> Any:
        path = self.directory / (
            "market-" + time.strftime("%Y%m%d-%H%M%S") + "-" + self.session_id + "-" + str(index) + ".jsonl"
        )
        header = (
            json.dumps(
                {
                    "event": "header",
                    "version": 2,
                    "created_at": int(time.time()),
                    **self.metadata,
                    "session_id": self.session_id,
                    "segment": index,
                    "previous_file": previous,
                },
                default=str,
            )
            + "\n"
        )
        size = len(header.encode("utf-8"))
        with self._quota_lock:
            used = sum(
                max(p.stat().st_size, self._leases.get(p, 0)) for p in self.directory.glob("market-*.jsonl")
            )
            capacity = min(self.segment_limit, self.total_limit - used)
            if capacity < size + 512 + 128:
                raise ArchiveFull("Market archive full or segment too small")
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            self._leases[path] = capacity
        stream = os.fdopen(fd, "w", buffering=1)
        try:
            stream.write(header)
        except BaseException:
            stream.close()
            with self._quota_lock:
                self._leases.pop(path, None)
            raise
        self.paths.append(path)
        return stream, path, capacity, size

    def finish_segment(self, next_file: Any = None) -> None:
        try:
            self.stream.write(
                json.dumps(
                    {
                        "event": "end",
                        "written": self.segment_written,
                        "dropped": self.dropped,
                        "last_sequence": self.last_written_sequence,
                        "session_id": self.session_id,
                        "segment": self.segment,
                        "next_file": next_file,
                        "session_complete": next_file is None,
                    }
                )
                + "\n"
            )
        finally:
            self.stream.close()
            with self._quota_lock:
                self._leases.pop(self.current_path, None)

    def rotate(self) -> Any:
        try:
            new = self.open_segment(self.segment + 1, self.current_path.name)
        except ArchiveFull:
            self.full = True
            return False
        try:
            self.finish_segment(new[1].name)
        except BaseException:
            new[0].close()
            with self._quota_lock:
                self._leases.pop(new[1], None)
            raise
        self.stream, self.current_path, self.max_bytes, self.bytes_written = new
        self.segment += 1
        self.segment_written = 0
        return True

    def record(self, kind: str, **data: Any) -> None:
        if kind not in FIELDS or self.stop_event.is_set():
            return
        self.sequence += 1
        record = {
            "event": kind,
            "sequence": self.sequence,
            "t": time.monotonic() - self.started,
            **{k: v for k, v in data.items() if k in FIELDS[kind]},
        }
        try:
            self.queue.put_nowait(record)
        except queue.Full:
            self.dropped += 1

    def run(self) -> None:
        try:
            while not self.stop_event.is_set() or not self.queue.empty():
                try:
                    record = self.queue.get(timeout=0.1)
                except queue.Empty:
                    continue
                line = json.dumps(record, default=str) + "\n"
                size = len(line.encode("utf-8"))
                if self.full:
                    self.dropped += 1
                    continue
                if self.bytes_written + size > self.max_bytes - 512:
                    # Oversized events must not create an endless series of empty parts.
                    if not self.segment_written or not self.rotate():
                        self.dropped += 1
                        continue
                    if self.bytes_written + size > self.max_bytes - 512:
                        self.dropped += 1
                        continue
                self.stream.write(line)
                self.bytes_written += size
                self.written += 1
                self.segment_written += 1
                self.last_written_sequence = record["sequence"]
            self.finish_segment()
            self.completed = True
        except Exception as exc:
            self.error_type = type(exc).__name__
        finally:
            self.stream.close()
            with self._quota_lock:
                self._leases.pop(self.current_path, None)

    def close(self) -> bool:
        self.stop_event.set()
        self.thread.join(timeout=0.5)
        return not self.thread.is_alive()
