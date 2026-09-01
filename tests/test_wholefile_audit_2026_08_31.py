"""The pre-release whole-file audit of 2026-08-31, one guard per finding.

RELEASING.md's whole-file arm re-reads a fixed list of functions end to end
whether or not they changed, because the worst defects this app has shipped
were silent: no crash, no error, just wrong behaviour repeating forever. This
round found eight, and every one of them is fenced off here.

Each test states the user-visible sequence it prevents, and each was checked
against the unfixed code first: reverting its fix turns the test red.

  A  a copy at its release's ceiling read as not-owned once the setting rose
     (pinned in tests/test_ownership_ceiling.py, beside the rest of that
     function's matrix)
  B  an honest delivery counted as a degraded attempt, freezing the upgrade
  D  a playlist entry listed twice landed a second, numbered copy
  F  dismissing the folder gate left a rollup that could never finish
  G  a download held for automatic replay was credited as failed
  I  a bulk clear withdrew the row of a job already downloading
  J  STOP left held downloads for the recovery watch to start again
  K  a withdrawn row left its best-of-both plan for the next plain click
"""

from __future__ import annotations

import pathlib
import threading
from collections import defaultdict, deque
from threading import Event, Lock
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from _dispatch_stub import arm_dispatch, arm_queue

from waves.download import Download
from waves.waves_ui import backend
from waves.waves_ui.backend import WavesBridge, _JobSpec


class _Sig:
    def __init__(self):
        self.emits: list = []

    def emit(self, *a):
        self.emits.append(a)


def _bind(stub, name):
    return getattr(WavesBridge, name).__get__(stub, type(stub))


class _Stub:
    pass


# --------------------------------------------------------------------------- #
# B. The degraded counter is measured against what the run ASKED for.
#
# `degraded` gates a two-attempt budget, and once it is spent _copy_is_current
# settles the copy for good. Measured against the ceiling alone, every ordinary
# download at a setting below the ceiling burned one attempt: two of those and
# the copy was frozen, so RAISING the quality setting afterwards, the one thing
# the release notes promise picks the better master up, found the budget gone
# and fetched nothing. It also logged "TIDAL advertised a better master than it
# delivered" over downloads where TIDAL had done exactly as it was told.
# --------------------------------------------------------------------------- #
def _ownership_stub():
    s = _Stub()
    s.recorded: list = []

    class _Store:
        def record(self, *a, **kw):
            s.recorded.append(kw)
            return 1

    s._ownership = _Store()
    s.settings = SimpleNamespace(data=SimpleNamespace(symlink_to_track=False))
    s._own_cache = {}
    s._own_cache_lock = Lock()
    s._evict_own_cache_locked = lambda *a, **k: None
    s._announce_ownership = lambda *a, **k: None
    s._note_download_base_ok = lambda *a, **k: None
    s._record_ownership = _bind(s, "_record_ownership")
    return s


def _record(stub, *, tier: str, requested: int, ceiling: int) -> dict:
    stub._ownership_event = None
    stub._record_ownership(
        {
            "id": "101",
            "path": "/music/Artist/Album/01 Song.flac",
            "quality": {"tier": tier, "requested_rank": requested, "ceiling_rank": ceiling},
        }
    )
    return stub.recorded[-1]


def test_a_delivery_that_matched_the_ask_is_not_a_degraded_attempt():
    """The whole finding: at LOSSLESS on a HI_RES release, TIDAL delivering
    LOSSLESS is TIDAL obeying, not TIDAL falling short."""
    s = _ownership_stub()

    assert _record(s, tier="LOSSLESS", requested=2, ceiling=3)["degraded"] is False


def test_a_delivery_under_the_ask_is_still_a_degraded_attempt():
    """The issue #2 guard has to keep biting: asked for HI_RES, served HIGH."""
    s = _ownership_stub()

    assert _record(s, tier="HIGH", requested=3, ceiling=3)["degraded"] is True


def test_a_delivery_with_no_ask_recorded_is_never_degraded():
    """An Atmos fetch carries no requested_rank (-1). Reading -1 as a rank the
    delivery fell short of would have counted every one of them."""
    s = _ownership_stub()

    assert _record(s, tier="LOSSLESS", requested=-1, ceiling=3)["degraded"] is False


def test_the_recorded_ask_is_the_one_the_degraded_verdict_used():
    """The verdict and the stored requested_rank must come from one read, or a
    later reader draws a different conclusion from the same row."""
    s = _ownership_stub()
    row = _record(s, tier="LOSSLESS", requested=2, ceiling=3)

    assert row["requested_rank"] == 2
    assert row["ceiling_rank"] == 3


# --------------------------------------------------------------------------- #
# D. A playlist entry listed twice lands ONE file.
#
# Both twins pass every earlier skip check while the folder is still empty, and
# the file only appears at the move. By the time the second twin claims a name,
# the first has landed it. The claim's disk arm is not owner-aware, so the twin
# stepped aside onto "Song_01.flac": a byte-identical copy carrying the same
# item id, listed twice in the .m3u8 and never cleaned up (the app never
# deletes user files).
# --------------------------------------------------------------------------- #
def _make_download(tmp_path: pathlib.Path, *, skip_existing: bool = True) -> Download:
    dl = Download(
        tidal_obj=MagicMock(),
        skip_existing=skip_existing,
        path_base=str(tmp_path),
        fn_logger=MagicMock(),
        progress=MagicMock(),
    )
    dl.settings = MagicMock()
    dl.settings.data.filename_illegal_replacement = ""
    dl.settings.data.filename_illegal_map = None
    dl.settings.data.extract_flac = False
    dl.settings.data.downsample_enabled = False
    dl.settings.data.video_convert_mp4 = False
    dl.settings.data.path_binary_ffmpeg = ""
    dl.event_abort = threading.Event()
    dl.event_run = threading.Event()
    dl.event_run.set()

    return dl


def test_the_claim_alone_still_steps_the_twin_aside_on_disk(tmp_path):
    """The control case, and the reason the guard above it has to exist: with a
    file actually on disk the claim answers "occupied" for the item's OWN copy.
    The existing claim tests never create the file, so this arm was untested."""
    dl = _make_download(tmp_path)
    dst = tmp_path / "Song.flac"
    dst.write_bytes(b"landed by the first twin")

    picked, _claim = dl._claim_destination(dst, "42")

    assert picked != dst, "if this ever passes, the claim became owner-aware and the guard can be revisited"
    assert picked.name == "Song_01.flac"


class _ReachedTheClaim(Exception):
    """Raised by the claim spy: the guard did NOT late-skip this pass."""


def _twin_run(dl: Download, dst: pathlib.Path, media) -> tuple[bool, pathlib.Path]:
    """Drive _perform_actual_download to the late-skip guard, with the network
    download stubbed to 'succeeded'.

    Returns the (ok, path) of a late skip. Raises _ReachedTheClaim when the
    guard let the pass through to _claim_destination, which is exactly the
    distinction every test below turns on: skipping onto the twin's file, or
    going on to write a second one.
    """

    def _fake_download(self, *, media, stream_manifest, path_file, event_stop=None, **kw):
        return True, path_file

    def _plan(self, *a, **k):
        return defaultdict(float)

    def _spy_claim(self, *a, **k):
        raise _ReachedTheClaim

    with (
        patch.object(Download, "_download", _fake_download),
        patch.object(Download, "_finalize_plan", _plan),
        patch.object(Download, "_note_stage", lambda *a, **k: None),
        patch.object(Download, "_claim_destination", _spy_claim),
    ):
        return dl._perform_actual_download(
            media=media,
            path_media_dst=dst,
            stream_manifest=None,
            do_flac_extract=False,
            is_parent_album=False,
            media_stream=None,
        )


def _track(item_id: str):
    return SimpleNamespace(id=item_id, name="Song", artist=SimpleNamespace(name="Artist"), artists=[], duration=200)


def test_the_second_entry_skips_onto_the_file_the_first_one_landed(tmp_path):
    """The fix: the twin returns the landed path and writes nothing."""
    dl = _make_download(tmp_path)
    dst = tmp_path / "Song.flac"
    dst.write_bytes(b"landed by the first twin")
    media = _track("42")

    with patch("waves.download.read_item_id", lambda p: "42"):
        ok, landed = _twin_run(dl, dst, media)

    assert ok is True
    assert landed == dst, "the twin must land on the first one's file, not a numbered copy of it"
    assert not (tmp_path / "Song_01.flac").exists()


def test_a_genuinely_different_track_of_the_same_name_is_not_skipped(tmp_path):
    """The guard must not swallow a real collision: a DIFFERENT item at that
    name is a distinct track, and it still gets its own numbered file."""
    dl = _make_download(tmp_path)
    dst = tmp_path / "Song.flac"
    dst.write_bytes(b"a different track that happens to share the name")
    media = _track("42")

    with patch("waves.download.read_item_id", lambda p: "999"):
        try:
            _twin_run(dl, dst, media)
        except _ReachedTheClaim:
            pass
        else:
            raise AssertionError("a colliding stranger must not be treated as this item's own copy")


def test_a_forced_redownload_does_not_late_skip_its_own_file(tmp_path):
    """REDOWNLOAD turns skipping off for that thread, and the guard rides that
    switch: the point of the force is to overwrite the copy in place."""
    dl = _make_download(tmp_path, skip_existing=False)
    dst = tmp_path / "Song.flac"
    dst.write_bytes(b"the copy being replaced")
    media = _track("42")

    with patch("waves.download.read_item_id", lambda p: "42"):
        try:
            _twin_run(dl, dst, media)
        except _ReachedTheClaim:
            pass
        else:
            raise AssertionError("a forced redownload must reach the move, not skip itself")


def test_an_untagged_stranger_on_the_name_is_not_read_as_this_item(tmp_path):
    """The regression the guard caused on its first attempt, and the reason it
    matches ids POSITIVELY instead of reusing the pre-write skip check: that
    check answers "identity unknown, treat as this item" for a file whose id it
    cannot read, which is right before the bytes are fetched and wrong here. An
    untagged file on the name is a stranger to step around, not a twin, and
    reading it as this item made a distinct track skip instead of uniquifying,
    taking its lyrics and cover with it."""
    dl = _make_download(tmp_path)
    dst = tmp_path / "Song.flac"
    dst.write_bytes(b"a different track, written by something that tags nothing")
    media = _track("42")

    with patch("waves.download.read_item_id", lambda p: None):
        try:
            _twin_run(dl, dst, media)
        except _ReachedTheClaim:
            pass
        else:
            raise AssertionError("an untagged stranger was treated as this item's own copy")


def test_an_empty_destination_is_never_read_as_this_item(tmp_path):
    """The other half of the same trap: a file that is not there reads as
    "identity unknown" too, and answering yes to that late-skips EVERY download
    in the run so nothing is ever written.

    Guarded twice over on purpose, and this test stays green if either guard is
    removed: the existence check refuses an absent destination outright, and the
    positive id match refuses it again because an unreadable file yields no id.
    The pair is cheap and the failure it prevents is total."""
    dl = _make_download(tmp_path)
    dst = tmp_path / "Song.flac"  # deliberately NOT created
    media = _track("42")

    with patch("waves.download.read_item_id", lambda p: None):
        try:
            _twin_run(dl, dst, media)
        except _ReachedTheClaim:
            pass
        else:
            raise AssertionError("an absent destination was treated as an already-landed copy")


# --------------------------------------------------------------------------- #
# Queue teardown: F, G, I, J, K.
# --------------------------------------------------------------------------- #
def _queue_stub(statuses, *, running_qid=None):
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
    s._running_qid = running_qid
    s._emit_queue = lambda: None
    for n in (
        "_reindex_queue",
        "_queue_item",
        "_remove_rows_where",
        "_remove_row",
        "_abort_if_in_flight",
        "clearQueue",
        "clearQueued",
        "clearFailed",
        "clearStopped",
        "cancelQueueItem",
        "removeQueueItem",
        "stopAll",
        "dismissDownloadFolderNudge",
        "_bump_download_groups",
        "_bump_artist_group",
        "_bump_folder_group",
        "_reap_stranded_groups",
    ):
        setattr(s, n, _bind(s, n))
    return s


# --- I. A bulk clear aborts the job it withdraws ---------------------------- #
#
# _pump_queue hands a row to the pool while it still reads "queued": the status
# only flips to "running" AFTER the folder reachability probe, which on a
# sleeping share is seconds of probe, remount and probe again. Both bulk clears
# select on that status, so the row of a job already downloading was withdrawn
# with nothing left to stop it. The album ran to completion with no row, no
# progress and no control, and with the queue reading idle the drawer hides
# STOP entirely.
def test_clear_all_aborts_the_job_whose_row_it_withdraws():
    s = _queue_stub(["queued", "queued"], running_qid=1)
    abort = Event()
    s._job_aborts[1] = abort

    s.clearQueue()

    assert abort.is_set(), "the album kept downloading with no row left to show or stop it"


def test_clear_queued_section_aborts_it_too():
    s = _queue_stub(["queued", "queued"], running_qid=1)
    abort = Event()
    s._job_aborts[1] = abort

    s.clearQueued()

    assert abort.is_set()


def test_removing_one_row_aborts_it_too():
    s = _queue_stub(["queued", "queued"], running_qid=1)
    abort = Event()
    s._job_aborts[1] = abort

    s.removeQueueItem(1)

    assert abort.is_set()


def test_a_clear_that_spares_the_running_row_aborts_nothing():
    """The other half of the contract: CLEAR ALL spares a row that has reached
    "running" and it must keep downloading, so nothing may set its abort."""
    s = _queue_stub(["running", "queued"], running_qid=1)
    abort = Event()
    s._job_aborts[1] = abort

    s.clearQueue()

    assert not abort.is_set(), "a row still writing bytes was killed by a bulk clear"
    assert [it["qid"] for it in s._queue] == [1]


def test_clearing_rows_behind_the_running_one_leaves_it_alone():
    """And a clear that touches only other rows must not abort the job either."""
    s = _queue_stub(["queued", "failed"], running_qid=1)
    abort = Event()
    s._job_aborts[1] = abort

    s.clearFailed()

    assert not abort.is_set()


# --- J. STOP ends held downloads too ---------------------------------------- #
#
# A download held for an unreachable folder is neither running nor queued: the
# gate withdrew its row, so STOP's sweep cannot see it and nothing holds its
# abort. Left in the stash it was not stopped, only postponed: the recovery
# watch kept polling and started the albums again by itself minutes later, into
# rollups the same press had already deleted.
def test_stop_drops_downloads_held_for_the_folder_to_come_back():
    s = _queue_stub(["running"])
    s._pending_downloads = [("m9", lambda: None)]
    stopped: list = []
    s._recovery_poll = SimpleNamespace(stop=lambda: stopped.append(True), start=lambda: None, isActive=lambda: True)

    s.stopAll()

    assert s._pending_downloads == [], "STOP left downloads for the recovery watch to start again"
    assert stopped == [True], "the recovery watch kept polling after STOP"
    # A regression the first cut of this fix introduced: a held download can
    # carry a lit button, the row sweep cannot see it (a hold has no row), and
    # a button left lit refuses every click for the rest of the session.
    assert ("m9", "") in s.downloadState.emits, "the held download's button was left lit and dead"


# --- K. A withdrawn row gives up its merge plan ----------------------------- #
#
# downloadAlbum PEEKS the stashed best-of-both plan (it must survive a RETRY),
# so a plan left behind by a withdrawn row was consumed by the next PLAIN click
# on that album: with the preference since turned off, the click still built a
# cross-edition copy, and the "Best of both:" line that would have said so is
# only written on the explicit path.
def test_clearing_a_failed_row_releases_its_merge_plan():
    s = _queue_stub(["failed"])
    s._merge_plans["m1"] = ["a plan the scan stashed"]

    s.clearFailed()

    assert "m1" not in s._merge_plans, "the next plain click on this album would silently merge editions"


def test_clearing_a_stopped_row_releases_it_too():
    s = _queue_stub(["cancelled"])
    s._merge_plans["m1"] = ["a plan the scan stashed"]

    s.clearStopped()

    assert "m1" not in s._merge_plans


def test_a_plan_survives_a_row_withdrawn_because_the_download_is_only_HELD():
    """A regression the first cut of this fix introduced. The folder gate
    withdraws the row precisely BECAUSE it stashed a replay, so that withdrawal
    is a hold, not a give-up. Popping the plan there meant the share came back,
    the merge replayed from the closure, and a later RETRY of it re-downloaded
    the album PLAIN, writing the identity edition's own lower-quality tracks
    over the ones the merge had borrowed."""
    s = _queue_stub(["queued"])
    s._merge_plans["m1"] = ["a plan the scan stashed"]
    s._pending_downloads = [("m1", lambda: None)]

    s._remove_row(1)

    assert s._merge_plans["m1"] == ["a plan the scan stashed"], "a held merge came back as a plain album"


def test_a_plan_is_still_released_when_the_row_is_really_abandoned():
    """The other side: nothing held, so the withdrawal is a give-up and the
    plan goes with it. Without this the fix above would just disable K."""
    s = _queue_stub(["queued"])
    s._merge_plans["m1"] = ["a plan the scan stashed"]
    s._pending_downloads = []

    s._remove_row(1)

    assert "m1" not in s._merge_plans


def test_a_plan_is_kept_while_a_live_row_still_holds_it():
    """RETRY re-queues the item BEFORE the old row is dropped, which is the
    whole reason the plan is peeked and not popped: a retried merge must stay a
    merge. The release rides the same liveness test as the REDOWNLOAD force."""
    s = _queue_stub(["failed", "queued"])
    s._queue[1]["media_id"] = "m1"  # the retry's new row for the same album
    s._merge_plans["m1"] = ["a plan the scan stashed"]

    s.clearFailed()

    assert s._merge_plans["m1"] == ["a plan the scan stashed"], "a retried merge was degraded to a plain download"


# --- F. Dismissing the folder gate settles the rollups ---------------------- #
#
# Held downloads have no row, so no worker will ever credit them. Dropping them
# without crediting left a discography short of its own key set for good: the
# artist button stayed "running" and refused every tap, with an idle queue and
# no STOP on screen to end it.
def test_dismissing_the_folder_nudge_settles_the_rollup():
    s = _queue_stub(["done"])
    # A discography of three: one landed, and two are held behind the folder
    # that went away, with their rows already withdrawn by the gate.
    s._artist_groups["art1"]["keys"] |= {"m2", "m3"}
    s._bump_download_groups("m1", 100.0, "done")
    assert "art1" in s._artist_groups, "the group must still be open with two members outstanding"
    s._pending_downloads = [("m2", lambda: None), ("m3", lambda: None)]

    s.dismissDownloadFolderNudge()

    assert s._artist_groups == {}, "the artist button would stay running and refuse every tap"
    assert ("art1", "failed") in s.downloadState.emits


def test_dismissing_with_nothing_held_settles_nothing():
    """The slot is also the dialog's click-away, so it fires with an empty
    stash all the time. It must not sweep live rollups when it does."""
    s = _queue_stub(["queued"])

    s.dismissDownloadFolderNudge()

    assert "art1" in s._artist_groups


# --- G. Held work is not credited as failed --------------------------------- #
#
# Every False from the reachability gate has stashed the download for automatic
# replay (one of them tells the user "the download starts by itself"). Crediting
# it as failed deleted the rollup before the replay could report into it, so a
# discography whose folder slept between albums finished RED with every one of
# its albums present on disk.
class _InlinePool:
    def start(self, worker) -> None:
        worker.run()


class _GateStub:
    """One job body, with the gate blocking and the rollup under observation."""

    def __init__(self) -> None:
        self._logged_in = True
        self._job_aborts: dict[int, Event] = {}
        self._job_signals: dict = {}
        self._job_dls: dict = {}
        self._job_tracks: dict = {}
        self._merge_plans: dict = {}
        self._redownload_overrides: set = set()
        self._library_claim_overrides: set = set()
        self._queue = [{"qid": 1, "media_id": "m1", "status": "queued", "type": "album", "name": "Album"}]
        self._queue_index = {1: self._queue[0]}
        self._queue_lock = Lock()
        self.settings = SimpleNamespace(
            data=SimpleNamespace(download_base_path="/tmp/waves-out", download_delay=False, downloads_concurrent_max=2)
        )
        self.dl = SimpleNamespace(path_base="")
        self.dl_pool = _InlinePool()
        self.downloadState = _Sig()
        self.downloadProgress = _Sig()
        self.bumps: list = []
        self._track_poll = SimpleNamespace(isActive=lambda: True, start=lambda *a: None)
        arm_dispatch(self)
        self._emit_queue = lambda: None
        for n in ("_remove_rows_where", "_remove_row", "_reindex_queue"):
            setattr(self, n, _bind(self, n))

    def _set_queue_status(self, qid, status, reason: str = "") -> None:
        self._queue[0]["status"] = status

    def _set_queue_progress(self, qid, pct) -> None:
        pass

    def _set_status(self, msg) -> None:
        pass

    def _job_library_skip(self, qid: int) -> bool:
        return False

    def _job_quality(self, qid):
        return None

    def _build_download(self, signals, **kwargs):
        return self.dl

    def _release_job_signals(self, qid) -> None:
        self._job_signals.pop(qid, None)

    def _bump_download_groups(self, media_id, pct, state) -> None:
        self.bumps.append((media_id, pct, state))

    def _gate_reachability(self, retry, media_id) -> bool:
        """The folder is asleep: the gate stashes the replay and blocks."""
        return False


def _media():
    return SimpleNamespace(
        id="m1", name="Album", artist=SimpleNamespace(name="Artist"), artists=[], audio_quality=None, duration=200
    )


def test_a_download_held_at_the_gate_is_not_credited_as_failed():
    stub = _GateStub()
    spec = _JobSpec(_media(), "album", "Album", "{title}", True, "m1", None)

    with patch.object(backend, "_ProgressSignals", lambda *a, **k: object()):
        WavesBridge._start_job(stub, 1, spec)

    assert stub.bumps == [], "the rollup was deleted before the automatic replay could report into it"


def test_a_merge_held_at_the_gate_keeps_its_plan_even_if_a_clear_beat_the_stash():
    """The last regression this chain introduced. A bulk clear runs on the GUI
    thread and can take the row away while the worker is still inside the
    probe, which is BEFORE the stash exists, so the withdrawal's held-work
    check cannot see this download and releases its plan. The replay survives
    (the closure carries the plan by value), but a later RETRY reads this dict
    and would save a plain album over the tracks the merge had borrowed."""
    stub = _GateStub()
    plan = ["a plan the scan stashed"]
    spec = _JobSpec(_media(), "album", "Album", "{title}", True, "m1", plan)
    # The clear already landed: the row is gone and the plan went with it.
    stub._queue.clear()
    stub._queue_index.clear()
    stub._merge_plans.pop("m1", None)

    with patch.object(backend, "_ProgressSignals", lambda *a, **k: object()):
        WavesBridge._start_job(stub, 1, spec)

    assert stub._merge_plans.get("m1") == plan, "a retried merge would have come back a plain album"


def test_a_plain_album_held_at_the_gate_invents_no_plan():
    """The guard is only for a job that really carries one: a plain download
    must not acquire a merge plan by passing through the gate."""
    stub = _GateStub()
    spec = _JobSpec(_media(), "album", "Album", "{title}", True, "m1", None)

    with patch.object(backend, "_ProgressSignals", lambda *a, **k: object()):
        WavesBridge._start_job(stub, 1, spec)

    assert stub._merge_plans == {}


def test_a_download_held_at_the_gate_still_withdraws_its_row():
    """The rest of the gate-block contract is unchanged: the queue reads as if
    the download never started, so only the CREDIT was wrong."""
    stub = _GateStub()
    spec = _JobSpec(_media(), "album", "Album", "{title}", True, "m1", None)

    with patch.object(backend, "_ProgressSignals", lambda *a, **k: object()):
        WavesBridge._start_job(stub, 1, spec)

    assert stub._queue == []
    assert ("m1", "") in stub.downloadState.emits
