"""Withdrawn queue rows settle their rollups, and stranded rollups self-heal
(issue #32).

The defect: every path that credits a discography/folder rollup lives inside a
download worker, and every path that WITHDRAWS a row (CLEAR ALL, the Queued
section's CLEAR, a row's cancel or remove) only dropped the row and its spec.
A queued member withdrawn that way could never enter the group's ``done`` set,
so ``finished`` never came true, the group was never deleted, and every later
tick re-emitted ``running`` under the artist id. The QML holder map is reset
by value only, and after a clear the drawer's STOP (the one control that
sweeps groups) is hidden with the queue empty, so the button was stuck for
the session: "the progress bar remains and the discovery cannot be downloaded
again" until a restart.

Fixes pinned here, layer by layer: the withdrawal slots credit never-started
rows to their rollups; _reap_stranded_groups deletes any group with no live
member row (two consecutive sightings, so a group mid-birth is never eaten);
the bumps drop their emits when a STOP moved the scan generation under them;
a stale batch enqueue is refused by its generation; and _download refuses an
exact duplicate of a row already queued or running.

Same hermetic pattern as the queue's other tests: the real unbound methods
bound onto a minimal stub, no Qt app or network session.
"""

from __future__ import annotations

from collections import deque
from threading import Event, Lock
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from _dispatch_stub import arm_dispatch, arm_queue

from waves.waves_ui import backend
from waves.waves_ui.backend import WavesBridge


class _Sig:
    def __init__(self):
        self.emits: list = []

    def emit(self, *a):
        self.emits.append(a)


def _bind(stub, name):
    return getattr(WavesBridge, name).__get__(stub, type(stub))


class _Stub:
    pass


def _queue_stub(statuses):
    """A queue of albums in the given statuses, plus one live discography
    rollup spanning all of them, with the real withdrawal slots bound."""
    s = _Stub()
    s._queue = [
        {"qid": n, "media_id": f"m{n}", "status": st, "type": "album", "name": f"r{n}"}
        for n, st in enumerate(statuses, 1)
    ]
    s._queue_lock = Lock()
    s._queue_index = {it["qid"]: it for it in s._queue}
    s._queue_emit_suspended = False
    s._job_specs = {it["qid"]: object() for it in s._queue}
    s._job_aborts = {}
    s._pending_qids = deque(it["qid"] for it in s._queue)
    s._event_run = Event()
    s._paused = False
    s.pausedChanged = _Sig()
    s._scan_gen = 0
    s._scans_in_flight = 0
    s._scan_count_lock = Lock()
    s.scanningChanged = _Sig()
    s.downloadState = _Sig()
    s.downloadProgress = _Sig()
    s.folderRemaining = _Sig()
    s.statuses = []
    s._set_status = s.statuses.append
    s._job_objs = {}
    s._artist_groups = {
        "art1": {"keys": {it["media_id"] for it in s._queue}, "done": set(), "failed": set(), "prog": {}}
    }
    s._artist_lock = Lock()
    s._folder_groups = {}
    s._folder_lock = Lock()
    s._stranded_once = set()
    arm_queue(s)
    s._emit_queue = lambda: None
    for n in (
        "_reindex_queue",
        "_queue_item",
        "_remove_rows_where",
        "_remove_row",
        "clearQueue",
        "clearQueued",
        "clearFailed",
        "cancelQueueItem",
        "removeQueueItem",
        "stopAll",
        "_bump_download_groups",
        "_bump_artist_group",
        "_bump_folder_group",
        "_reap_stranded_groups",
    ):
        setattr(s, n, _bind(s, n))
    return s


# --------------------------------------------------------------------------- #
# Withdrawal crediting: the exact reported shape, CLEAR without a STOP first.
# --------------------------------------------------------------------------- #
def test_clear_all_settles_the_discography_rollup():
    s = _queue_stub(["running", "queued", "queued"])
    s.clearQueue()
    assert [it["status"] for it in s._queue] == ["running"]
    # The spared running row finishes; the group must now settle, not strand.
    s._bump_download_groups("m1", 100.0, "done")
    assert s._artist_groups == {}, "the group must settle once every member is accounted for"
    # Withdrawn members count as failed, so the artist button lands on RETRY
    # (clickable), never on a "running" nothing can ever clear again.
    assert ("art1", "failed") in s.downloadState.emits
    assert s.downloadState.emits[-1] != ("art1", "running")


def test_per_row_cancel_of_queued_albums_settles_it_too():
    s = _queue_stub(["running", "queued", "queued"])
    s.cancelQueueItem(2)
    s.cancelQueueItem(3)
    s._bump_download_groups("m1", 100.0, "done")
    assert s._artist_groups == {}
    assert ("art1", "failed") in s.downloadState.emits


def test_clear_queued_section_settles_it_too():
    s = _queue_stub(["running", "queued", "queued"])
    s.clearQueued()
    s._bump_download_groups("m1", 100.0, "done")
    assert s._artist_groups == {}
    assert ("art1", "failed") in s.downloadState.emits


def test_remove_row_settles_it_too():
    s = _queue_stub(["running", "queued"])
    s.removeQueueItem(2)
    s._bump_download_groups("m1", 100.0, "done")
    assert s._artist_groups == {}
    assert ("art1", "failed") in s.downloadState.emits


def test_cancel_of_a_running_row_leaves_the_credit_to_its_worker():
    """A running row's worker credits the group when its abort lands (the
    body's cancel branch); the slot crediting it too would be a double count
    of a member the worker still owns."""
    s = _queue_stub(["running", "queued"])
    s._job_aborts[1] = Event()
    s.cancelQueueItem(1)
    grp = s._artist_groups["art1"]
    assert "m1" not in grp["done"], "the slot must not settle a member its worker still owns"
    # The worker's own cancel bump then settles it, with m2 still queued.
    s._bump_download_groups("m1", None, "failed")
    assert "m1" in grp["done"]


def test_all_members_withdrawn_resets_the_button_to_idle():
    """No member ever ran: nothing was downloaded, so the reap resets the
    button to idle on the sweep that follows the settling bumps."""
    s = _queue_stub(["queued", "queued"])
    s.clearQueued()
    # Both bumps ran inside clearQueued; the group settled as failed.
    assert s._artist_groups == {}
    assert ("art1", "failed") in s.downloadState.emits


def test_stop_then_clear_stays_clean():
    s = _queue_stub(["running", "queued", "queued"])
    s.stopAll()
    assert s._artist_groups == {}
    assert ("art1", "") in s.downloadState.emits
    s.clearQueue()
    assert s._artist_groups == {}


# --------------------------------------------------------------------------- #
# The reaper: the net under every stranding path nobody has found yet.
# --------------------------------------------------------------------------- #
def test_reaper_takes_two_sightings_then_resets_the_button():
    s = _queue_stub([])
    s._artist_groups = {"art1": {"keys": {"mX"}, "done": set(), "failed": set(), "prog": {}}}
    s._reap_stranded_groups()
    assert "art1" in s._artist_groups, "one sighting must not reap (the group may be mid-birth)"
    s._reap_stranded_groups()
    assert s._artist_groups == {}
    assert s.downloadState.emits[-1] == ("art1", "")


def test_reaper_spares_a_group_with_a_live_row():
    s = _queue_stub(["queued"])
    s._reap_stranded_groups()
    s._reap_stranded_groups()
    assert "art1" in s._artist_groups


def test_reaper_spares_a_group_whose_members_are_held_for_recovery():
    """A share dropping mid-discography withdraws each member's row and holds
    the download for an automatic replay when the folder comes back. That is
    live work with no row to see it by: reaping it took the artist rollup away
    while the replays were still coming, so the run that followed showed no
    progress, no completion and no failure at all."""
    s = _queue_stub([])
    s._artist_groups = {"art1": {"keys": {"mX"}, "done": set(), "failed": set(), "prog": {}}}
    s._pending_downloads = [("mX", lambda: None)]

    s._reap_stranded_groups()
    s._reap_stranded_groups()

    assert "art1" in s._artist_groups
    assert s.downloadState.emits == [], "and the artist button is left as it is"


def test_reaper_still_takes_a_group_once_the_held_work_is_gone():
    s = _queue_stub([])
    s._artist_groups = {"art1": {"keys": {"mX"}, "done": set(), "failed": set(), "prog": {}}}
    s._pending_downloads = [("mOther", lambda: None)]

    s._reap_stranded_groups()
    s._reap_stranded_groups()

    assert s._artist_groups == {}


def test_reaper_defers_while_a_scan_is_in_flight():
    s = _queue_stub([])
    s._artist_groups = {"art1": {"keys": {"mX"}, "done": set(), "failed": set(), "prog": {}}}
    s._scans_in_flight = 1
    s._reap_stranded_groups()
    s._reap_stranded_groups()
    assert "art1" in s._artist_groups
    # And the strikes reset: the scan may be about to enqueue this group's rows.
    s._scans_in_flight = 0
    s._reap_stranded_groups()
    assert "art1" in s._artist_groups, "the first post-scan sighting is strike one, not two"


def test_reaper_covers_folder_groups_too():
    s = _queue_stub([])
    s._artist_groups = {}
    s._folder_groups = {
        "fold1": {"keys": {"p1"}, "done": set(), "failed": set(), "prog": {}, "weights": {"p1": 1}, "total": 1}
    }
    s._reap_stranded_groups()
    s._reap_stranded_groups()
    assert s._folder_groups == {}
    assert s.downloadState.emits[-1] == ("fold1", "")


# --------------------------------------------------------------------------- #
# The bump's stale-generation guard: a bump racing STOP keeps its arithmetic
# but must not re-light a swept button.
# --------------------------------------------------------------------------- #
class _StopDuringLock:
    """A lock stand-in whose release simulates STOP landing while the bump
    held it: the generation moves between the arithmetic and the emits."""

    def __init__(self, stub):
        self._stub = stub
        self._lock = Lock()

    def __enter__(self):
        self._lock.acquire()

    def __exit__(self, *exc):
        self._stub._scan_gen += 1
        self._lock.release()


def test_a_bump_that_raced_stop_drops_its_emits():
    s = _queue_stub(["running"])
    s._artist_lock = _StopDuringLock(s)
    s._bump_download_groups("m1", 50.0, None)
    assert s.downloadState.emits == [], "a stale bump must not re-light a swept button"
    assert s.downloadProgress.emits == []
    assert s._artist_groups["art1"]["prog"]["m1"] == 50.0, "the arithmetic itself still lands"


def test_a_bump_with_the_generation_still_current_emits_normally():
    s = _queue_stub(["running"])
    s._bump_download_groups("m1", 50.0, None)
    assert ("art1", "running") in s.downloadState.emits


# --------------------------------------------------------------------------- #
# Stale batch refusal: a scan's enqueue posted before STOP, delivered after.
# --------------------------------------------------------------------------- #
def _enqueue_stub():
    s = _Stub()
    s._scan_gen = 1
    # The scan marks every album key exempt from the edition scan before it
    # emits the batch; a refused batch has to give those marks back.
    s._merge_scanned = {"a1", "a2"}
    # ...and stashes each one's merge plan, which a refused batch gives back
    # too, or the next plain click on that album downloads the assembly.
    s._merge_plans = {}
    s._queue_emit_suspended = False
    s._emit_queue = lambda: None
    s.downloadState = _Sig()
    s.calls = []
    s.downloadAlbum = s.calls.append
    s.downloadTrack = s.calls.append
    s.downloadVideo = s.calls.append
    for n in ("_enqueue_albums", "_enqueue_tracks", "_enqueue_videos", "_queue_batch"):
        setattr(s, n, _bind(s, n))
    return s


def test_a_stale_album_batch_queues_nothing_and_resets_its_buttons():
    s = _enqueue_stub()
    s._enqueue_albums(0, ["a1", "a2"])
    assert s.calls == []
    assert s.downloadState.emits == [("a1", ""), ("a2", "")]


def test_a_stale_album_batch_hands_back_the_edition_scan_exemptions():
    """The mark is consumed by the next click on that album, so one left
    behind by a batch that queued nothing silently downgraded that click to a
    plain download, skipping the edition scan the preference asks for."""
    s = _enqueue_stub()

    s._enqueue_albums(0, ["a1", "a2"])

    assert s._merge_scanned == set()


def test_a_current_batch_keeps_the_exemptions_it_was_given():
    s = _enqueue_stub()

    s._enqueue_albums(1, ["a1", "a2"])

    assert s._merge_scanned == {"a1", "a2"}, "the albums it queued consume them on their own way through"


def test_a_current_batch_queues_every_key():
    s = _enqueue_stub()
    s._enqueue_albums(1, ["a1", "a2"])
    assert s.calls == ["a1", "a2"]


def test_a_batch_that_raises_still_delivers_what_it_queued():
    """The closing delivery sat on the line AFTER the try/finally, so a loop
    body that raised skipped it while the flag was still cleared: the rows
    were in the queue and marked dirty, and the drawer showed none of them
    until some later, unrelated change flushed the marks."""
    s = _enqueue_stub()
    delivered = []
    s._emit_queue = lambda: delivered.append(True)

    def boom(key):
        s.calls.append(key)
        if key == "a2":
            raise RuntimeError("a key that would not queue")

    s.downloadAlbum = boom
    with pytest.raises(RuntimeError):
        s._enqueue_albums(1, ["a1", "a2", "a3"])

    assert s.calls == ["a1", "a2"]
    assert delivered == [True], "what did make it into the queue is on screen"
    assert s._queue_emit_suspended is False, "and the gate is open for what follows"


def test_stale_track_and_video_batches_are_refused_the_same_way():
    s = _enqueue_stub()
    s._enqueue_tracks(0, ["t1"])
    s._enqueue_videos(0, ["v1"])
    assert s.calls == []
    assert ("t1", "") in s.downloadState.emits
    assert ("v1", "") in s.downloadState.emits


# --------------------------------------------------------------------------- #
# Duplicate-row refusal in _download.
# --------------------------------------------------------------------------- #
def _download_stub(existing_status="queued", existing_quality="LOSSLESS"):
    s = _Stub()
    s._logged_in = True
    s._download_gate = lambda: "ok"
    s._ffmpeg_gate_holds = lambda media_id, retry: False
    s._queued_quality_value = lambda: "LOSSLESS"
    s._queue = [
        {
            "qid": 1,
            "media_id": "m1",
            "type": "album",
            "status": existing_status,
            "template": "T",
            "askQuality": existing_quality,
        }
    ]
    s._queue_lock = Lock()
    s.downloadState = _Sig()
    s.enqueued = []
    s._enqueue = lambda *a: s.enqueued.append(a) or 99
    s._job_objs = {}
    s._job_specs = {}
    s._job_tracks = {}
    s._merge_plans = {}
    s._pending_qids = deque()
    s._pump_queue = lambda: None
    s._download = _bind(s, "_download")
    return s


def _album_obj():
    return SimpleNamespace(artists=[SimpleNamespace(name="Artist")], artist=SimpleNamespace(name="Artist"))


def test_an_identical_queued_row_refuses_a_duplicate():
    s = _download_stub()
    s._download(_album_obj(), "album", "R", "T", True, "m1")
    assert s.enqueued == [], "the same item at the same quality must not queue twice"
    assert ("m1", "queued") in s.downloadState.emits, "the click is still acknowledged"


def test_a_running_twin_refuses_a_duplicate_too():
    s = _download_stub(existing_status="running")
    s._download(_album_obj(), "album", "R", "T", True, "m1")
    assert s.enqueued == []


def test_a_different_pinned_quality_is_not_a_duplicate():
    """The upgrade flow: the same album queued again after a quality change is
    a genuine new ask and keeps its own row."""
    s = _download_stub(existing_quality="HIGH")
    s._download(_album_obj(), "album", "R", "T", True, "m1")
    assert len(s.enqueued) == 1


def test_a_terminal_row_never_blocks_a_fresh_ask():
    s = _download_stub(existing_status="cancelled")
    s._download(_album_obj(), "album", "R", "T", True, "m1")
    assert len(s.enqueued) == 1


# --------------------------------------------------------------------------- #
# Issue #31's session-long REDOWNLOAD mark: cleared by the job it forced
# finishing, kept on failure so a retry stays forced.
# --------------------------------------------------------------------------- #
class _InlinePool:
    def start(self, worker) -> None:
        worker.run()


class _BodyDownload:
    """Stands in for the built download inside the job body."""

    path_base = ""
    unavailable_count = 0
    write_count = 1
    ok_count = 1
    fail_count = 0
    skip_count = 0
    list_unavailable = False

    def __init__(self, fail=False):
        self._fail = fail

    def item(self, **kwargs):
        if self._fail:
            raise RuntimeError("stream refused")
        return True, "/tmp/song.flac"

    def items(self, **kwargs):
        if self._fail:
            raise RuntimeError("stream refused")
        return None

    def _landed_paths(self, *a, **k):
        return []

    def _playlist_for_collection(self, media, file_template, result_paths) -> None:
        pass


def _body_stub(fail=False):
    s = _Stub()
    s._logged_in = True
    s._download_gate = lambda: "ok"
    s._ffmpeg_gate_holds = lambda media_id, retry: False
    s._job_library_skip = lambda qid: False
    s._gate_reachability = lambda retry, media_id: True
    # The job's abort rides along now: the helper probes for seconds before it
    # takes its hold, and a press landing in there has to reach it.
    s._download_failed_with_folder = lambda retry, media_id, qid, name, abort=None: False
    s._job_quality = lambda qid: None
    s._build_download = lambda signals, **kw: s.dl
    s._enqueue = lambda *a: 41
    # Three arguments, as the real slot has taken since 1333a46: the failure
    # branch calls it with a reason, and a two-argument stub raised there,
    # inside a Worker that swallows and logs. Nothing after that line ran, so
    # this stub could not see the failure handler at all and the assertions
    # below passed on the never-entered success branch.
    s.statuses = []
    s._set_queue_status = lambda qid, status, reason="": s.statuses.append((qid, status, reason))
    s._set_queue_progress = lambda qid, pct: None
    s._set_status = lambda msg: None
    s._bump_download_groups = lambda media_id, pct, status: None
    s._release_job_signals = lambda qid: s._job_signals.pop(qid, None)
    s._job_aborts = {}
    s._job_signals = {}
    s._job_tracks = {}
    # The finally clause pops it; unseeded, that raised too and hid the same
    # ground.
    s._job_dls = {}
    s._merge_plans = {}
    s._redownload_overrides = {"m1"}
    s._library_claim_overrides = set()
    s._queue = []
    s._queue_lock = Lock()
    s.settings = SimpleNamespace(
        data=SimpleNamespace(download_base_path="/tmp/waves-out", download_delay=False, downloads_concurrent_max=2)
    )
    s.dl_pool = _InlinePool()
    s.downloadState = _Sig()
    s.downloadProgress = _Sig()
    s.dl = _BodyDownload(fail=fail)
    s._track_poll = SimpleNamespace(isActive=lambda: True, start=lambda *a: None)
    arm_dispatch(s)
    s._reap_stranded_groups = lambda: None
    s._download = _bind(s, "_download")
    return s


def _track_obj():
    return SimpleNamespace(artists=[SimpleNamespace(name="Artist")], artist=SimpleNamespace(name="Artist"))


def test_a_finished_forced_job_clears_its_redownload_mark():
    s = _body_stub(fail=False)
    with patch.object(backend, "_ProgressSignals", lambda *a, **k: object()):
        s._download(_track_obj(), "track", "Song", "T", False, "m1")
    assert s._redownload_overrides == set(), "the mark was one job's force, not a standing policy"


def test_a_failed_forced_job_keeps_its_mark_so_the_retry_stays_forced():
    s = _body_stub(fail=True)
    with patch.object(backend, "_ProgressSignals", lambda *a, **k: object()):
        s._download(_track_obj(), "track", "Song", "T", False, "m1")
    # Proof the failure handler was reached at all: without it this assertion
    # is answered by the success branch never being entered, so the guard
    # would stay green even if the mark were cleared right here.
    assert (41, "failed") in [(q, st) for q, st, _r in s.statuses]
    assert s._redownload_overrides == {"m1"}


def test_the_finished_job_really_walked_the_success_branch():
    """The other half of the same proof: the clearing test must be reading a
    job that actually finished."""
    s = _body_stub(fail=False)
    with patch.object(backend, "_ProgressSignals", lambda *a, **k: object()):
        s._download(_track_obj(), "track", "Song", "T", False, "m1")
    assert (41, "done") in [(q, st) for q, st, _r in s.statuses]


# --------------------------------------------------------------------------- #
# A withdrawn row takes its REDOWNLOAD force with it. The mark is dropped by
# the job that consumes it and kept on failure and cancel so a retry stays
# forced, but every WITHDRAWAL left it behind: the row went and the force
# stayed, so the next click on that item this session, from anywhere, silently
# re-fetched and overwrote copies it should have skipped.
# --------------------------------------------------------------------------- #
def _forced_queue_stub(statuses, media_ids=None):
    s = _queue_stub(statuses)
    if media_ids:
        for row, mid in zip(s._queue, media_ids, strict=False):
            row["media_id"] = mid
        s._queue_index = {it["qid"]: it for it in s._queue}
    s._redownload_overrides = {it["media_id"] for it in s._queue}
    return s


@pytest.mark.parametrize(
    "withdraw",
    [
        lambda s: s.clearQueue(),
        lambda s: s.clearQueued(),
        lambda s: s.cancelQueueItem(1),
        lambda s: s.removeQueueItem(1),
    ],
)
def test_every_withdrawal_gives_the_redownload_force_back(withdraw):
    s = _forced_queue_stub(["queued"])

    withdraw(s)

    assert s._queue == [], "the row was withdrawn"
    assert s._redownload_overrides == set(), "and its force went with it"


def test_a_withdrawal_leaves_another_rows_force_alone():
    s = _forced_queue_stub(["queued", "queued"], media_ids=["m1", "m2"])

    s.cancelQueueItem(1)

    assert s._redownload_overrides == {"m2"}, "only the withdrawn row's force is released"


def test_a_row_still_running_keeps_the_force_a_withdrawn_twin_asked_for():
    """Two rows for one item (an upgrade queued over a running download): the
    force belongs to the work, and one of the two rows leaving is not the end
    of it."""
    s = _forced_queue_stub(["running", "queued"], media_ids=["m1", "m1"])

    s.clearQueued()

    assert s._redownload_overrides == {"m1"}


def test_a_settled_row_dismissed_from_the_drawer_releases_it_too():
    s = _forced_queue_stub(["failed"])

    s.clearFailed()

    assert s._redownload_overrides == set()
