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
from threading import Lock
from types import SimpleNamespace

from waves.waves_ui.backend import WavesBridge


class _RecordingSignal:
    """Minimal stand-in for a Qt signal: records every emit."""

    def __init__(self):
        self.emits: list = []

    def emit(self, *args):
        self.emits.append(args if len(args) != 1 else args[0])


def arm_queue(stub) -> None:
    """The queue's dirty marks (what QML has not been told yet) and the
    per-row stores the remove path prunes: any stand-in that binds the real
    _enqueue / _remove_rows_where / _flush_queue_changes family needs them.

    Defaults are only filled in where the stand-in has not set its own, so a
    test that wants a populated queue, a live abort or a running job says so
    and this leaves it alone."""
    stub._qdirty_added = getattr(stub, "_qdirty_added", [])
    stub._qdirty_changed = getattr(stub, "_qdirty_changed", {})
    stub._qdirty_removed = getattr(stub, "_qdirty_removed", [])
    stub._qdirty_full = getattr(stub, "_qdirty_full", False)
    stub._qflush_posted = getattr(stub, "_qflush_posted", False)
    # name -> factory for the state the real queue methods read. Each line says
    # which path needs it, because a missing one fails as an AttributeError
    # deep inside a bound method rather than at the stand-in.
    defaults = (
        # The per-row stores the remove path prunes.
        ("_job_specs", dict),
        ("_job_objs", dict),
        ("_job_tracks", dict),
        ("_job_owned", dict),
        ("_job_fetched", dict),
        ("_queue_lock", Lock),
        # A withdrawn row gives up its REDOWNLOAD force on the way out, and the
        # library-claim override it registered alongside that force: both marks
        # live only as long as a live row holds them.
        ("_redownload_overrides", set),
        ("_library_claim_overrides", set),
        # And the best-of-both plan the scan stashed for the row, the third
        # piece of the same per-row state: a plan left behind is picked up by
        # the next plain click on that album.
        ("_merge_plans", dict),
        # The job in flight, so a clear that drops its row can abort it: a row
        # is handed to the pool while it still reads "queued", so the bulk
        # clears can select one that is already downloading.
        ("_running_qid", lambda: None),
        # The watch that replays downloads held for an unreachable folder, and
        # which STOP now stops (the held downloads themselves are armed with
        # the rest of the rollup state, below).
        ("_recovery_poll", lambda: SimpleNamespace(stop=lambda: None, start=lambda: None, isActive=lambda: False)),
    )
    for name, make in defaults:
        if not hasattr(stub, name):
            setattr(stub, name, make())
    # Real bridge methods the queue family calls on itself. _queue_batch is the
    # context manager a batched enqueue (a discography, a folder, RETRY ALL)
    # holds the queue's delivery open through.
    # _discard_pending_downloads and _release_abandoned_hold are what a press
    # reaches a HELD download through: every withdrawal slot calls them so a
    # download waiting for the folder to come back is stopped rather than
    # merely postponed, and the state its hold kept alive goes with it.
    for name in (
        "_queue_mark_changed",
        "_abort_if_in_flight",
        "_queue_batch",
        "_discard_pending_downloads",
        "_release_abandoned_hold",
    ):
        if not hasattr(stub, name):
            setattr(stub, name, getattr(WavesBridge, name).__get__(stub, type(stub)))
    _arm_rollups(stub)


def _arm_rollups(stub) -> None:
    """The rollup fields and methods the withdrawal slots now touch (issue
    #32: a cleared or cancelled queued row credits its discography/folder
    rollups, and every slot sweeps for stranded groups afterwards). Stubs
    with their own richer versions keep them."""
    from threading import Lock as _Lock

    for field, default in (
        ("_artist_groups", dict),
        ("_folder_groups", dict),
        ("_stranded_once", set),
        ("_scan_gen", int),
        ("_scans_in_flight", int),
        # A download held for recovery has no queue row, and the reaper counts
        # it as live work all the same (a held member must not strand a group).
        ("_pending_downloads", list),
    ):
        if not hasattr(stub, field):
            setattr(stub, field, default())
    for lock in ("_artist_lock", "_folder_lock", "_pending_lock"):
        if not hasattr(stub, lock):
            setattr(stub, lock, _Lock())
    for sig in ("downloadState", "downloadProgress", "folderRemaining"):
        if not hasattr(stub, sig):
            setattr(stub, sig, _RecordingSignal())
    for name in ("_bump_download_groups", "_bump_artist_group", "_bump_folder_group", "_reap_stranded_groups"):
        if not hasattr(stub, name):
            setattr(stub, name, getattr(WavesBridge, name).__get__(stub, type(stub)))
    # _download's duplicate-row refusal reads the pinned quality of a fresh row.
    if not hasattr(stub, "_queued_quality_value"):
        stub._queued_quality_value = lambda: "LOSSLESS"


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
