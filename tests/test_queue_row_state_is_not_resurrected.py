"""Per-row state may not outlive, or come back after, its queue row.

A queue row carries state on both sides of the bridge: the per-track registry,
the expansion's predicted skips, the fetched track order, and the row's live
object; QML keeps the expanded list in a map of its own. Every removal path
prunes all of it.

Three writers could put it straight back. A track event, an owned-marks
prediction and a fetched track list all arrive over a queued connection, so
each can land after the row it belongs to has been cleared or cancelled, and
each created its qid's entry unconditionally. qids are never reused, so what
they re-created was never freed again: expand a 500-track playlist row and
press CLEAR before the fetch returns, and both sides of the bridge keep five
hundred dicts each for a row neither side still has, for the rest of the
session.

Nothing here is visible in the window. It is a leak that grows with use, in an
app people leave open for days.
"""

from __future__ import annotations

from threading import Lock
from types import SimpleNamespace

from waves.waves_ui.backend import WavesBridge

_LIVE_QID = 1
_GONE_QID = 2


class _Signal:
    def __init__(self):
        self.calls = []

    def emit(self, *a):
        self.calls.append(a)


class _Stub:
    """A bridge carcass with one live queue row (qid 1) and none for qid 2,
    which is the row that has just been withdrawn."""

    def __init__(self):
        self._queue = [{"qid": _LIVE_QID, "media_id": "m1", "status": "running", "type": "album"}]
        self._queue_index = {it["qid"]: it for it in self._queue}
        self._queue_lock = Lock()
        self._qdirty_changed: dict = {}
        self._outcome_lock = Lock()
        self._job_tracks: dict = {}
        self._job_owned: dict = {}
        self._job_fetched: dict = {}
        self._job_objs: dict = {}
        self._job_signals: dict = {}
        self._job_dls: dict = {}
        self._pct_last: dict = {}
        self._track_poll = SimpleNamespace(stop=lambda: None, isActive=lambda: True, start=lambda: None)
        self.queueTrackState = _Signal()
        self.queueTracksLoaded = _Signal()
        self._emit_queue = lambda: None
        self._own_pool = type("P", (), {"start": lambda self_, w: None})()
        self.settings = type("S", (), {"data": type("D", (), {"download_base_path": ""})()})()
        for name in (
            "_track_lifecycle",
            "_apply_owned_marks",
            "_merge_queue_tracks",
            "_prune_job_tracks",
            "_poll_track_progress",
            "_queue_item",
            "_queue_mark_changed",
        ):
            setattr(self, name, getattr(WavesBridge, name).__get__(self, _Stub))


def _event(track_id="9"):
    return {"id": track_id, "title": "t", "status": "running"}


def test_a_track_event_for_a_row_that_has_gone_records_nothing():
    b = _Stub()

    b._track_lifecycle(_GONE_QID, _event())

    assert _GONE_QID not in b._job_tracks
    assert b.queueTrackState.calls == []


def test_a_track_event_for_a_live_row_records_as_it_always_did():
    b = _Stub()

    b._track_lifecycle(_LIVE_QID, _event())

    assert b._job_tracks[_LIVE_QID]["9"]["status"] == "running"


def test_a_job_that_started_keeps_recording_through_its_last_events():
    """The registry is seeded when the job starts, and the run's own closing
    events must still land even if the row were swept in the same instant:
    the gate is about CREATING state, not about writing to state that is
    already there."""
    b = _Stub()
    b._job_tracks[_GONE_QID] = {}

    b._track_lifecycle(_GONE_QID, _event())

    assert b._job_tracks[_GONE_QID]["9"]["status"] == "running"


def test_a_prediction_that_lands_after_the_row_went_is_dropped():
    b = _Stub()

    b._apply_owned_marks(_GONE_QID, {"9": {"kind": "own", "tier": "LOSSLESS"}})

    assert b._job_owned == {}


def test_a_track_list_that_lands_after_the_row_went_is_dropped():
    """Expanding a long row is a network fetch; CLEAR can land first."""
    b = _Stub()

    b._merge_queue_tracks(_GONE_QID, [{"id": "9", "num": 1, "title": "t", "duration": "3:00"}])

    assert b._job_fetched == {}
    assert b.queueTracksLoaded.calls == [], "nothing is sent for a row QML no longer has"


def test_a_track_list_for_a_live_row_still_reaches_the_drawer():
    b = _Stub()

    b._merge_queue_tracks(_LIVE_QID, [{"id": "9", "num": 1, "title": "t", "duration": "3:00"}])

    assert b._job_fetched[_LIVE_QID]
    assert len(b.queueTracksLoaded.calls) == 1


def test_the_idle_sweep_settles_state_left_over_from_a_row_that_is_gone():
    """The documented safety net had no caller at all. It runs when the last
    collection download finishes, which is when the app is idle enough to
    afford a full pass."""
    b = _Stub()
    for store in (b._job_tracks, b._job_owned, b._job_fetched, b._job_objs):
        store[_LIVE_QID] = {"kept": True}
        store[_GONE_QID] = {"leaked": True}

    b._poll_track_progress()  # no jobs running: the sweep runs and the timer stops

    assert list(b._job_tracks) == [_LIVE_QID]
    assert list(b._job_owned) == [_LIVE_QID]
    assert list(b._job_fetched) == [_LIVE_QID]
    assert list(b._job_objs) == [_LIVE_QID]


def test_the_sweep_leaves_a_running_jobs_state_alone():
    b = _Stub()
    b._job_dls[_LIVE_QID] = SimpleNamespace(progress=SimpleNamespace(tasks=[]), row_task_ids=lambda: {})
    b._job_tracks[_GONE_QID] = {"still": "here"}

    b._poll_track_progress()

    assert _GONE_QID in b._job_tracks, "a poll with work in flight is not the moment for a full sweep"
