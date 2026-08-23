"""Arm a partial WavesBridge stand-in with the queue dispatcher's state.

_download used to build a job per call and hand it straight to dl_pool; it
now records a _JobSpec and _pump_queue builds the job when the pool is free,
one at a time (the backlog resilience work for issue #30). Stubs that bind
the real _download therefore need the dispatcher's fields, and their inline
pools keep their synchronous behavior through a _jobFinished stand-in that
calls the real _on_job_finished directly: the Worker's finally emits it, the
next queued spec starts, and a multi-download test still sees every job run
in order within the _download call it drove.
"""

from __future__ import annotations

from collections import deque
from types import SimpleNamespace

from tidaler.waves_ui.backend import WavesBridge


def arm_queue(stub) -> None:
    """The queue's dirty marks (what QML has not been told yet) and the
    per-row stores the remove path prunes: any stand-in that binds the real
    _enqueue / _remove_rows_where / _flush_queue_changes family needs them."""
    stub._qdirty_added = getattr(stub, "_qdirty_added", [])
    stub._qdirty_changed = getattr(stub, "_qdirty_changed", {})
    stub._qdirty_removed = getattr(stub, "_qdirty_removed", [])
    stub._qdirty_full = getattr(stub, "_qdirty_full", False)
    stub._qflush_posted = getattr(stub, "_qflush_posted", False)
    for store in ("_job_specs", "_job_objs", "_job_tracks", "_job_owned", "_job_fetched"):
        if not hasattr(stub, store):
            setattr(stub, store, {})
    if not hasattr(stub, "_queue_lock"):
        from threading import Lock

        stub._queue_lock = Lock()
    if not hasattr(stub, "_queue_mark_changed"):
        stub._queue_mark_changed = WavesBridge._queue_mark_changed.__get__(stub, type(stub))


def arm_dispatch(stub) -> None:
    arm_queue(stub)
    stub._job_specs = {}
    stub._job_objs = {}
    stub._pending_qids = deque()
    stub._running_qid = None
    stub._paused = getattr(stub, "_paused", False)
    stub._pct_last = getattr(stub, "_pct_last", {})
    if not hasattr(stub, "_track_poll"):
        stub._track_poll = SimpleNamespace(isActive=lambda: True, start=lambda *a: None)
    if not hasattr(stub, "_queue_item"):
        # A stand-in whose _enqueue returns a bare qid keeps no rows; the
        # pump re-validates the row, so answer "still queued" as a real row
        # would (the pre-dispatcher behavior: the job always started).
        stub._queue_item = lambda qid: {"qid": qid, "status": "queued"}
    for name in ("_pump_queue", "_start_job", "_on_job_finished"):
        setattr(stub, name, getattr(WavesBridge, name).__get__(stub, type(stub)))
    stub._jobFinished = SimpleNamespace(emit=stub._on_job_finished)
