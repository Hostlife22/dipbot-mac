"""Bounded per-signal timings; no RPC, credentials, transaction data or retries."""

from __future__ import annotations

import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
from typing import Any, Callable, ParamSpec, TypeVar
from uuid import uuid4

_CAPTURE: ContextVar[dict[str, Any] | None] = ContextVar("market_capture", default=None)
_HEAD: ContextVar[dict[str, Any] | None] = ContextVar("observation_head", default=None)
_ACTIVE: ContextVar["CycleTrace | None"] = ContextVar("cycle_trace", default=None)
P = ParamSpec("P")
R = TypeVar("R")


@contextmanager
def head_context(number: int | None, block_hash: str | None, received_ns: int | None) -> Iterator[None]:
    """Carry the consumed hint across scheduling without changing its reception clock."""
    value = None
    if number is not None and block_hash is not None and received_ns is not None:
        value = {"number": number, "hash": block_hash.lower(), "received_ns": received_ns}
    token = _HEAD.set(value)
    try:
        yield
    finally:
        _HEAD.reset(token)


def observation_timing(function: Callable[P, R]) -> Callable[P, R]:
    @wraps(function)
    def wrapped(*args: P.args, **kwargs: P.kwargs) -> R:
        token = _CAPTURE.set(
            {"started_ns": time.perf_counter_ns(), "events": {}, "rpc": [], "dropped": 0, "head": _HEAD.get()}
        )
        try:
            return function(*args, **kwargs)
        finally:
            _CAPTURE.reset(token)

    return wrapped


def observation_mark(name: str) -> None:
    capture = _CAPTURE.get()
    if capture is not None and name in {
        "http_started",
        "raw_market_received",
        "price_ready",
        "strategy_completed",
    }:
        capture["events"][name] = time.perf_counter_ns()


def rpc_span(method: str, started_ns: int, *, failed: bool) -> None:
    """Only safe method labels; callers never pass params, URLs or response bodies."""
    capture = _CAPTURE.get()
    trace = _ACTIVE.get()
    if capture is None and trace is None:
        return
    origin = trace.started_ns if trace else capture["started_ns"]  # type: ignore[index]
    rows = trace.rpc if trace else capture["rpc"]  # type: ignore[index]
    if len(rows) >= 256:
        if trace:
            trace.rpc_dropped += 1
        else:
            capture["dropped"] += 1  # type: ignore[index]
        return
    rows.append(
        {
            "method": method,
            "start_ms": (started_ns - origin) / 1e6,
            "duration_ms": (time.perf_counter_ns() - started_ns) / 1e6,
            "failed": failed,
            "transaction": trace.transaction if trace else None,
        }
    )


class CycleTrace:
    def __init__(self, action: str, mode: str, header: Mapping[str, Any] | None = None) -> None:
        capture = _CAPTURE.get()
        self.signal_ns = time.perf_counter_ns()
        self.started_ns = capture["started_ns"] if capture else self.signal_ns
        self.started = self.started_ns / 1e9
        self.transaction = 0
        self.rpc: list[dict[str, Any]] = list(capture["rpc"]) if capture else []
        self.rpc_dropped = capture["dropped"] if capture else 0
        self.stages: list[dict[str, Any]] = []
        self.truncated = False
        self.data: dict[str, Any] = {
            "schema": 3,
            "cycle_id": uuid4().hex,
            "origin": "observation_start" if capture else "signal",
            "observation": {k: (v - self.started_ns) / 1e6 for k, v in capture["events"].items()}
            if capture
            else {},
            "unavailable": {
                "raw_market_received": "see observation; cache hit has no new pool read",
                "finality": "not observed",
                "head_received": "not correlated",
                "broadcast": "N/A" if mode != "LIVE" else "see stages",
            },
            "action": action,
            "mode": mode,
            "signal_block": None,
            "block_to_signal_ms": None,
        }
        if header:
            self.data["signal_block"] = header.get("number")
            timestamp = header.get("timestamp")
            if isinstance(timestamp, (int, float)) and 0 <= time.time() - timestamp < 3600:
                # Wall-clock estimate from integer block timestamp, not exact propagation time.
                self.data["block_to_signal_ms"] = (time.time() - timestamp) * 1000
        hint = capture.get("head") if capture else None
        if hint and header:
            value = header.get("hash")
            block_hash = (
                value.lower()
                if isinstance(value, str)
                else "0x" + bytes(value).hex()
                if value is not None
                else None
            )
            matched = header.get("number") == hint["number"] and block_hash == hint["hash"]
            elapsed = (hint["received_ns"] - self.started_ns) / 1e6
            self.data["head"] = {"number": hint["number"], "hash": hint["hash"], "matched": matched}
            if matched and hint["received_ns"] <= self.started_ns:
                self.data["observation"]["head_received"] = elapsed
                self.data["unavailable"].pop("head_received")
            else:
                self.data["unavailable"]["head_received"] = "different block/hash or invalid clock ordering"
        self.mark("signal")

    def mark(self, stage: str, *, kind: str | None = None, block: int | None = None) -> None:
        if stage not in {
            "signal",
            "transaction_started",
            "nonce_ready",
            "journal_started",
            "broadcast_started",
            "broadcast_known",
            "receipt_observed",
            "quote",
            "gas_estimated",
            "transaction_built",
            "signed",
            "intent_persisted",
            "broadcast_ack",
            "receipt_validated",
            "completed",
            "failed",
            "preflight_started",
            "activity_checked",
            "activity_skipped",
            "entry_screened",
            "paper_delay_finished",
            "fill_price_read",
            "fill_quote_received",
            "execution_applied",
        }:
            return
        if len(self.stages) >= (256 if self.data["action"] == "SWEEP" else 64):
            self.truncated = True
            return
        if stage == "transaction_started":
            self.transaction += 1
        row: dict[str, Any] = {"stage": stage, "ms": (time.perf_counter_ns() - self.started_ns) / 1e6}
        if kind is not None and self.transaction and stage != "quote":
            row["transaction"] = self.transaction
        if kind in ("BUY", "SELL", "APPROVE", "OTHER", "CONVERT_BUY", "CONVERT_SELL", "WRAP", "UNWRAP"):
            row["kind"] = kind
        if type(block) is int and block >= 0:
            row["block"] = block
        self.stages.append(row)


def mark(subject: object, stage: str, *, label: str | None = None, block: int | None = None) -> None:
    trace = getattr(subject, "cycle_trace", None)
    if isinstance(trace, CycleTrace):
        kind = (
            label
            if label in ("BUY", "SELL")
            else ("APPROVE" if label and label.startswith("APPROVE") else "OTHER")
        )
        kind = {
            "CONVERTER BUY": "CONVERT_BUY",
            "CONVERTER SELL": "CONVERT_SELL",
            "BNB → WBNB": "WRAP",
            "WBNB → BNB": "UNWRAP",
        }.get(label or "", kind)
        trace.mark(stage, kind=kind if label is not None else None, block=block)


@contextmanager
def signal_cycle(worker: Any, action: str, header: Mapping[str, Any] | None) -> Iterator[CycleTrace]:
    trace = CycleTrace(action, worker.mode, header)
    previous = getattr(worker, "cycle_trace", None)
    live = worker.live
    previous_live = getattr(live, "cycle_trace", None)
    worker.cycle_trace = trace
    if live is not None:
        live.cycle_trace = trace
    error_type = None
    token = _ACTIVE.set(trace)
    try:
        yield trace
    except BaseException as exc:
        error_type = type(exc).__name__
        raise
    finally:
        trace.mark("failed" if error_type else "completed")
        _ACTIVE.reset(token)
        worker.cycle_trace = previous
        if live is not None:
            live.cycle_trace = previous_live
        # Diagnostics must never change execution or hide its original exception.
        try:
            worker.record_market(
                "cycle_latency",
                **trace.data,
                stages=trace.stages,
                rpc=trace.rpc,
                rpc_dropped=trace.rpc_dropped,
                error_type=error_type,
                truncated=trace.truncated,
            )
        except Exception:
            pass
