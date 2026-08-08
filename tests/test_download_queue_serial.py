"""The download queue runs ONE item at a time, strictly in queue order.

WHAT THIS FENCES OFF
--------------------
dl_pool used to run downloads_concurrent_max queue items side by side, which
read as the queue jumping around (livetest report): a 21-track album ground
along (its tracks carry the 3-5s anti-hammer delay and shared the
10-connection HTTP pool with every concurrent sibling) while single tracks
queued after it zipped past. The queue's promise is order; parallelism lives
inside a collection (the engine's per-collection track executor, still sized
by downloads_concurrent_max, which it reads live from settings on each run).

So two things must hold:
1. dl_pool is created with exactly one thread, regardless of the setting.
2. Saving settings must NOT resize dl_pool back up (the old live-reapply).
"""

from __future__ import annotations

import inspect
import re
from pathlib import Path

from tidaler.waves_ui import backend as backend_mod

BACKEND_SRC = Path(inspect.getsourcefile(backend_mod))


def _source() -> str:
    return BACKEND_SRC.read_text(encoding="utf-8")


def test_dl_pool_is_serial():
    src = _source()
    m = re.search(r"self\.dl_pool = QtCore\.QThreadPool\(\).*?setMaxThreadCount\(([^)]*)\)", src, re.S)
    assert m, "dl_pool must still be created with an explicit thread cap"
    assert m.group(1).strip() == "1", (
        f"dl_pool sized to {m.group(1).strip()!r}: the queue must stay serial "
        "(one item at a time, in order); track-level parallelism belongs to the "
        "engine's per-collection executor, not here"
    )


def test_settings_save_never_resizes_the_pool():
    src = _source()
    assert src.count("dl_pool.setMaxThreadCount") == 1, (
        "a second setMaxThreadCount call (the old settings-save live-reapply) "
        "would widen the queue back out from under the serial design"
    )


def test_the_knob_still_reaches_the_track_executor():
    """downloads_concurrent_max must keep meaning something: the engine's
    per-collection executor is sized by it, read live at download time."""
    from tidaler import download as engine_dl

    src = Path(inspect.getsourcefile(engine_dl)).read_text(encoding="utf-8")
    assert "max_workers=self.settings.data.downloads_concurrent_max" in src


def test_a_one_thread_pool_actually_runs_submissions_in_order():
    """Behavioral pin, not a source grep: the serial design leans on
    QThreadPool draining equal-priority runnables in submission order, so
    prove it with a real pool rather than assuming it."""
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
