"""A QThreadPool-shaped saturation gauge for plain ThreadPoolExecutors.

The diagnostics perf sampler reads ``activeThreadCount`` / ``maxThreadCount``
off every pool it is given, and the project convention is that a new pool
registers so its saturation shows up in a verbose report. Executors that are
built and torn down per job (the scanner's walk/read pools, the download
engine's segment and per-item fan-outs, the search enrich burst, the
bridge's per-call fan-outs for search popularity and merged-album tracks)
cannot be handed over directly: they are ThreadPoolExecutors (no such
methods) and a registered reference would go stale. A gauge counts work items in flight
instead, which is what saturation actually means there, and reads zero
between jobs. One module-level gauge per pool kind, registered once at
startup in backend.py beside the QThreadPools.

Grew out of the scanner's private gauge (waves/library_index.py), promoted
here so the engine can use it without importing the scanner, and the scanner
without importing the engine. Deliberately Qt-free: the download engine
imports this and must stay importable without PySide6.

Counts only integers. Nothing about the user's library, downloads or their
names passes through here.
"""

from __future__ import annotations

import contextlib
from threading import Lock


class PoolGauge:
    def __init__(self, maximum: int) -> None:
        self._busy = 0
        self._max = maximum
        self._lock = Lock()
        #: High-water mark of items in flight. The sampler reads the
        #: INSTANTANEOUS count on a timer, which on a fast pool it can sample
        #: straight past; this remembers the busiest the pool ever got, which is
        #: the number worth having once a job is over and the question is
        #: whether it was ever saturated. Never reset: it describes the run.
        self.peak = 0

    @contextlib.contextmanager
    def working(self):
        """Hold for the duration of one work item. The lock costs about a
        microsecond against work items that are a network round trip or a tag
        read, and a plain ``+= 1`` from eight threads is not atomic."""
        with self._lock:
            self._busy += 1
            if self._busy > self.peak:
                self.peak = self._busy
        try:
            yield
        finally:
            with self._lock:
                self._busy -= 1

    def activeThreadCount(self) -> int:  # (Qt's spelling: the sampler calls this)
        return self._busy

    def maxThreadCount(self) -> int:
        return self._max

    def limit(self, maximum: int) -> None:
        """Record the cap the CURRENT job actually runs under, so a throttled
        run reports 2/2 (saturated, as intended) rather than 2/8 (looking
        mysteriously underdriven). The pools gauged here are serialized per
        kind, so the shared gauge follows the one job in flight."""
        self._max = maximum
