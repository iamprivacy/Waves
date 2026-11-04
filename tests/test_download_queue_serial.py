"""The download queue STREAMS one item at a time, in queue order, and lets
the finished ones finish behind it.

WHAT THIS FENCES OFF
--------------------
dl_pool once ran downloads_concurrent_max queue items side by side, which
read as the queue jumping around (livetest report): a 21-track album ground
along while single tracks queued after it zipped past. Then it ran exactly
one, which made a single track's tag-and-copy (minutes onto a slow library
volume) block the next row entirely while the network sat idle.

The promise now: streams start in queue order, one job at a time
(_pump_queue holds the next row until the streaming job has started its last
stream), and up to downloads_finishing_max jobs may be finishing behind it.
So:

1. dl_pool is created with 1 + downloads_finishing_max threads, and a
   settings save re-applies that (a running job keeps its thread).
2. The engine's per-collection executor is sized from the stream count plus
   the finishing count, read live, and the slot ledger caps the streams.
3. QThreadPool drains equal-priority runnables in submission order, which the
   hand-over still leans on.
"""

from __future__ import annotations

import inspect
import re
from pathlib import Path
from types import SimpleNamespace

from waves.waves_ui import backend as backend_mod

BACKEND_SRC = Path(inspect.getsourcefile(backend_mod))


def _source() -> str:
    return BACKEND_SRC.read_text(encoding="utf-8")


def test_dl_pool_holds_the_streaming_job_plus_the_finishing_ones():
    src = _source()
    m = re.search(r"self\.dl_pool = QtCore\.QThreadPool\(\).*?setMaxThreadCount\((.*?)\)\n", src, re.S)
    assert m, "dl_pool must still be created with an explicit thread cap"
    assert m.group(1).strip() == "1 + _finishing_seats_of(self)", (
        f"dl_pool sized to {m.group(1).strip()!r}: one streaming job plus the finishing count, "
        "never downloads_concurrent_max (the queue's order promise lives in _pump_queue)"
    )


def test_a_settings_save_reapplies_the_finishing_count_to_the_pool():
    src = _source()
    assert (
        src.count("dl_pool.setMaxThreadCount(1 + _finishing_seats_of(self))") == 2
    ), "the pool is sized at startup and again on save, both from the finishing count"


def test_the_finishing_count_reads_live_and_tolerates_a_missing_field():
    seats = backend_mod._finishing_seats_of
    assert seats(SimpleNamespace(settings=SimpleNamespace(data=SimpleNamespace(downloads_finishing_max=5)))) == 5
    assert seats(SimpleNamespace(settings=SimpleNamespace(data=SimpleNamespace(downloads_finishing_max=0)))) == 0
    assert seats(SimpleNamespace(settings=SimpleNamespace(data=SimpleNamespace()))) == 3, "an older config"
    assert seats(SimpleNamespace()) == 3, "a stub without settings"
    assert seats(SimpleNamespace(settings=SimpleNamespace(data=SimpleNamespace(downloads_finishing_max=-2)))) == 0


def test_the_knob_still_reaches_the_track_executor():
    """downloads_concurrent_max must keep meaning something: the engine's
    per-collection executor is sized from it (plus the finishing seats), read
    live at download time, and the slot ledger caps the streams by it."""
    from waves import download as engine_dl

    src = Path(inspect.getsourcefile(engine_dl)).read_text(encoding="utf-8")
    assert "max_workers=workers" in src
    dl = engine_dl.Download.__new__(engine_dl.Download)
    dl.settings = SimpleNamespace(data=SimpleNamespace(downloads_concurrent_max=4, downloads_finishing_max=2))
    assert dl._item_workers() == 6
    streams, seats = dl._slot_limits()
    assert (streams(), seats()) == (4, 2)


def test_a_one_thread_pool_actually_runs_submissions_in_order():
    """Behavioral pin, not a source grep: the hand-over leans on QThreadPool
    draining equal-priority runnables in submission order, so prove it with
    a real pool rather than assuming it."""
    from threading import Event

    from PySide6.QtCore import QRunnable, QThreadPool

    order: list[int] = []
    done = Event()

    class _Job(QRunnable):
        def __init__(self, i: int):
            super().__init__()
            self.i = i

        def run(self) -> None:
            order.append(self.i)
            if self.i == 19:
                done.set()

    pool = QThreadPool()
    pool.setMaxThreadCount(1)
    for i in range(20):
        pool.start(_Job(i))
    assert done.wait(10), "the pool never drained"
    pool.waitForDone(10000)
    assert order == list(range(20)), "queue items ran out of submission order"
