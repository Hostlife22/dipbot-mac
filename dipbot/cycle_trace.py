"""Bounded per-signal timings; no RPC, credentials, transaction data or retries."""
from contextlib import contextmanager
import time


class CycleTrace:
    def __init__(self, action, mode, header=None):
        self.started = time.perf_counter()
        self.stages = []
        self.truncated = False
        self.data = {'action':action, 'mode':mode, 'signal_block':None,
                     'block_to_signal_ms':None}
        if header:
            self.data['signal_block'] = header.get('number')
            timestamp = header.get('timestamp')
            if type(timestamp) in (int, float) and 0 <= time.time()-timestamp < 3600:
                # Wall-clock estimate from integer block timestamp, not exact propagation time.
                self.data['block_to_signal_ms'] = (time.time()-timestamp)*1000
        self.mark('signal')

    def mark(self, stage, *, kind=None, block=None):
        if stage not in {'signal','quote','gas_estimated','transaction_built','signed',
                         'intent_persisted','broadcast_ack','receipt_validated','completed','failed',
                         'preflight_started','activity_checked','activity_skipped','entry_screened',
                         'paper_delay_finished','fill_price_read','fill_quote_received','execution_applied'}:
            return
        if len(self.stages) >= 64:
            self.truncated = True
            return
        row = {'stage':stage, 'ms':(time.perf_counter()-self.started)*1000}
        if kind in ('BUY','SELL','APPROVE','OTHER'):
            row['kind'] = kind
        if type(block) is int and block >= 0:
            row['block'] = block
        self.stages.append(row)


def mark(subject, stage, *, label=None, block=None):
    trace = getattr(subject, 'cycle_trace', None)
    if isinstance(trace, CycleTrace):
        kind = label if label in ('BUY','SELL') else ('APPROVE' if label and label.startswith('APPROVE') else 'OTHER')
        trace.mark(stage, kind=kind if label is not None else None, block=block)


@contextmanager
def signal_cycle(worker, action, header):
    trace = CycleTrace(action, worker.mode, header)
    previous = getattr(worker, 'cycle_trace', None)
    live = worker.live
    previous_live = getattr(live, 'cycle_trace', None)
    worker.cycle_trace = trace
    if live is not None:
        live.cycle_trace = trace
    error_type = None
    try:
        yield trace
    except BaseException as exc:
        error_type = type(exc).__name__
        raise
    finally:
        trace.mark('failed' if error_type else 'completed')
        worker.cycle_trace = previous
        if live is not None:
            live.cycle_trace = previous_live
        # Diagnostics must never change execution or hide its original exception.
        try:
            worker.record_market('cycle_latency', **trace.data, stages=trace.stages,
                                 error_type=error_type, truncated=trace.truncated)
        except Exception:
            pass
