"""Opt-in monotonic timings; no query text or credentials in logs."""
from contextlib import contextmanager
import os
import sys
import time

def report(label, started):
    if os.getenv('MC_PROFILE') == '1':
        print(f'[MC_PROF] {label}: {(time.perf_counter()-started)*1000:.1f} ms', file=sys.stderr, flush=True)

@contextmanager
def timed(label):
    started = time.perf_counter()
    try:
        yield
    finally:
        report(label, started)
