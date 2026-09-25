import json
import subprocess
import sys

import pytest

from dipbot.telemetry import Timings
from dipbot.diagnostics import Diagnostics


def test_bounded_percentiles_and_lifetime_errors():
    metrics = Timings(capacity=100, max_series=2)
    for i in range(200):
        metrics.record('quote', i / 1000, failed=i < 10)
    row = metrics.snapshot()['quote']
    assert row == {'count': 200, 'errors': 10, 'window': 100,
                   'p50_ms': 149., 'p95_ms': 194., 'p99_ms': 198.}
    metrics.record('second', 0)
    metrics.record('overflow', 0)
    metrics.record('quote', float('nan'))
    assert len(metrics.snapshot()) == 2
    assert metrics.snapshot()['quote']['count'] == 200


def test_measure_preserves_exception_and_records_failure():
    metrics = Timings()
    exc = ValueError('synthetic secret')
    with pytest.raises(ValueError) as caught:
        with metrics.measure('send'):
            raise exc
    assert caught.value is exc
    assert metrics.snapshot()['send']['errors'] == 1


def test_diagnostics_redacts_exception_and_tracks_exit(tmp_path):
    d = Diagnostics(tmp_path)
    try:
        try:
            raise ValueError('SECRET_RPC_URL_AND_PRIVATE_KEY')
        except ValueError:
            d.exception(*sys.exc_info())
    finally:
        d.close()
    text = (tmp_path / 'fault.log').read_text()
    assert 'SECRET_RPC_URL_AND_PRIVATE_KEY' not in text
    assert 'ValueError' in text and 'frames' in text
    assert not json.loads((tmp_path / 'session.json').read_text())['clean_exit']
    d = Diagnostics(tmp_path)
    assert d.previous_unclean
    d.close()
    assert json.loads((tmp_path / 'session.json').read_text())['clean_exit']


def test_process_termination_leaves_unclean_marker(tmp_path):
    code = '''
import os, sys
from dipbot.diagnostics import Diagnostics
Diagnostics(sys.argv[1])
os._exit(7)
'''
    proc = subprocess.run([sys.executable, '-c', code, str(tmp_path)], timeout=20)
    assert proc.returncode == 7
    d = Diagnostics(tmp_path)
    try:
        assert d.previous_unclean
    finally:
        d.close()
