"""Bounded historical Swap retrieval, independent of current-price trading."""
import threading
from .chain import Chain
from .activity import read_swaps


class GapRecovery:
    def __init__(self, endpoint, pool, start, end, *, factory=None):
        self.pool = pool
        self.start_block, self.end_block = max(start, end-31, 0), end
        self.truncated = self.start_block > start
        self.factory = factory or (lambda: Chain(endpoint, request_timeout=2))
        self.stop_event = threading.Event()
        self.result = None
        self.thread = None

    def start(self):
        self.thread = threading.Thread(target=self.run, name='swap-gap-recovery', daemon=True)
        self.thread.start()
        return self

    def run(self):
        if self.stop_event.is_set():return
        result = {'pool':self.pool.address, 'from_block':self.start_block,
                  'to_block':self.end_block, 'truncated':self.truncated}
        try:
            chain = self.factory()
            chain.restrict_to_reads()
            chain.check()
            if self.stop_event.is_set():return
            header = chain.w3.eth.get_block(self.end_block)
            if header['number'] != self.end_block:
                raise ValueError('Wrong backfill block')
            events = read_swaps(chain, self.pool, self.start_block, self.end_block, header, limit=512)
            result.update(events=events, count=len(events), error_type=None)
        except Exception as exc:
            result.update(error_type=type(exc).__name__, events=[], count=None)
        if not self.stop_event.is_set():
            self.result = result

    def stop(self):
        self.stop_event.set()
        if self.thread is not None:
            self.thread.join(timeout=.1)
