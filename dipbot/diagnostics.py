"""Local crash evidence without exception messages, locals, credentials or wallet data."""
import faulthandler
import json
import resource
import os
from pathlib import Path
import sys
import threading
import time
import traceback

from .storage import Store
from .telemetry import TIMINGS


def resource_snapshot():
    """No process arguments, paths, locals or thread names in the checkpoint."""
    usage = resource.getrusage(resource.RUSAGE_SELF)
    return {'peak_rss_bytes': int(usage.ru_maxrss * (1 if sys.platform == 'darwin' else 1024)),
            'python_threads': threading.active_count(),
            'cpu_user_seconds': usage.ru_utime, 'cpu_system_seconds': usage.ru_stime,
            'monotonic_seconds': time.monotonic()}


class Diagnostics:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.session = Store(self.directory / 'session.json')
        self.previous_unclean = bool(self.session.data and not self.session.data.get('clean_exit'))
        self.session.data = {'started': int(time.time()), 'pid': os.getpid(), 'clean_exit': False,
                             'previous_unclean': self.previous_unclean}
        self.session.save()
        path = self.directory / 'fault.log'
        if path.exists() and path.stat().st_size > 2_000_000:
            os.replace(path, self.directory / 'fault.previous.log')
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        self.stream = os.fdopen(fd, 'a', buffering=1)
        self.lock = threading.Lock()
        self.had_exception = False
        self.old_hook = sys.excepthook
        self.old_thread_hook = threading.excepthook
        faulthandler.enable(self.stream, all_threads=True)
        sys.excepthook = self.exception
        threading.excepthook = self.thread_exception
        self.write({'event': 'start', 'previous_unclean': self.previous_unclean})
        self.stop = threading.Event()
        self.checkpoint_thread = threading.Thread(target=self.checkpoints, daemon=True,
                                                   name='diagnostic-checkpoint')
        self.checkpoint_thread.start()

    def write(self, event):
        try:
            with self.lock:
                self.stream.write(json.dumps({'time': int(time.time()), **event}) + '\n')
                self.stream.flush()
        except (OSError, ValueError):
            pass  # Diagnostics must never change trading or recovery state.

    def exception(self, kind, value, tb):
        self.had_exception = True
        # No source lines or exception text: either can contain secrets.
        frames = [{'file': Path(f.filename).name, 'line': f.lineno, 'function': f.name}
                  for f in traceback.extract_tb(tb)]
        self.write({'event': 'unhandled_exception', 'type': kind.__name__, 'frames': frames})

    def thread_exception(self, args):
        self.exception(args.exc_type, args.exc_value, args.exc_traceback)

    def checkpoint(self):
        try:
            report = Store(self.directory / 'timings.json')
            report.data = {'units': 'milliseconds', 'recorded_at': int(time.time()),
                           'series': TIMINGS.snapshot(), 'resources': resource_snapshot()}
            report.save()
        except (OSError, ValueError):
            pass  # A diagnostic failure must not halt trading.

    def checkpoints(self):
        while not self.stop.wait(30):
            self.checkpoint()

    def close(self, clean=True):
        self.stop.set()
        self.checkpoint_thread.join(timeout=1)
        try:
            if not self.checkpoint_thread.is_alive():
                self.checkpoint()
            self.session.data['clean_exit'] = clean and not self.had_exception
            self.session.data['ended'] = int(time.time())
            self.session.save()
        except OSError:
            pass
        finally:
            faulthandler.disable()
            sys.excepthook = self.old_hook
            threading.excepthook = self.old_thread_hook
            self.stream.close()
