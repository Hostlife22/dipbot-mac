"""Bounded asynchronous public-market recordings, never credentials or signed data."""
import json
import os
from pathlib import Path
import queue
import threading
import time
import uuid

FIELDS = {
    'price': {'price', 'block', 'block_hash', 'block_timestamp', 'source'},
    'observation': {'price', 'block', 'block_hash', 'quote_usd', 'quote_usd_observed_at'},
    'signal': {'action', 'price', 'base', 'entry'},
    'execution': {'side', 'price', 'reason'},
    'read_error': {'type'},
}


class MarketTape:
    def __init__(self, directory, metadata, *, max_bytes=10*1024**2, max_total_bytes=200*1024**2, capacity=1024):
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        remaining = max_total_bytes-sum(p.stat().st_size for p in directory.glob('market-*.jsonl'))
        if remaining < 4096:
            raise OSError('Market archive full')
        self.max_bytes = min(max_bytes, remaining)
        self.path = directory / ('market-'+time.strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:8]+'.jsonl')
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        self.stream = os.fdopen(fd, 'w', buffering=1)
        self.queue = queue.Queue(maxsize=capacity)
        self.stop_event = threading.Event()
        self.started = time.monotonic()
        self.sequence = self.dropped = self.written = 0
        self.error_type = ''
        allowed = {'mode', 'pool', 'signal_policy', 'settings', 'starts_with_position', 'sizing', 'requested_amount', 'exit_policy'}
        metadata = dict(metadata)
        nested = {
            'exit_policy': {'tp_sl_basis','trailing_pct','max_hold_seconds','cooldown_seconds'},
            'sizing': {'unit','reserve_bnb'},
            'pool': {'address','router','token','quote','token_decimals','quote_decimals','token_is_0','fee'},
            'settings': {'amount','dip','take_profit','stop_loss','slippage','dynamic','max_gap','max_roundtrip_loss','min_swaps'},
            'signal_policy': {'mode','window_seconds','rebound_pct','max_block_age'},
        }
        for name, keys in nested.items():
            if isinstance(metadata.get(name), dict):
                metadata[name] = {k:v for k,v in metadata[name].items() if k in keys}
        self.stream.write(json.dumps({'event':'header', 'version':1, 'created_at':int(time.time()),
                                      **{k:v for k,v in metadata.items() if k in allowed}}, default=str)+'\n')
        self.bytes_written = self.stream.tell()
        self.thread = threading.Thread(target=self.run, name='market-recorder', daemon=True)
        self.thread.start()

    def record(self, kind, **data):
        if kind not in FIELDS or self.stop_event.is_set():
            return
        self.sequence += 1
        record = {'event':kind, 'sequence':self.sequence, 't':time.monotonic()-self.started,
                  **{k:v for k,v in data.items() if k in FIELDS[kind]}}
        try:
            self.queue.put_nowait(record)
        except queue.Full:
            self.dropped += 1

    def run(self):
        try:
            while not self.stop_event.is_set() or not self.queue.empty():
                try:
                    record = self.queue.get(timeout=.1)
                except queue.Empty:
                    continue
                line = json.dumps(record, default=str)+'\n'
                size = len(line.encode('utf-8'))
                if self.bytes_written + size > self.max_bytes - 512:
                    self.dropped += 1
                    continue
                self.stream.write(line)
                self.bytes_written += size
                self.written += 1
            self.stream.write(json.dumps({'event':'end', 'written':self.written, 'dropped':self.dropped,
                                          'last_sequence':self.sequence})+'\n')
        except OSError as exc:
            self.error_type = type(exc).__name__
        finally:
            self.stream.close()

    def close(self):
        self.stop_event.set()
        self.thread.join(timeout=.5)
        return not self.thread.is_alive()
