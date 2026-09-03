"""The final pre-release audit of 2026-08-31, the bridge's half.

The whole-file audit's fix round left the held-download path (a download
waiting for an unreachable download folder to come back) able to outlive every
press that ends a download, and able to lose the very state its hold was taken
to protect. Each test below states the user-visible sequence it prevents, and
each was checked against the unfixed code first: reverting its fix turns the
test red.

  1  STOP, CANCEL and the clears left a held download to start itself again
  2  a held REDOWNLOAD came back unforced and skipped every file
  3  an abandoned hold left its best-of-both plan for a later plain click
  4  a clear credited a failure against a row that had just started running
  5  the motion-background sweep deleted whatever else was in its folder
  6  a saturated pool reported as idle in every line of a diagnostics export
"""

from __future__ import annotations

from collections import deque
from threading import Event, Lock
from types import SimpleNamespace

from _dispatch_stub import arm_queue

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


def _queue_stub(statuses, *, running_qid=None):
    """A bridge stand-in holding one row per status, media ids m1..mN."""
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
        "_discard_pending_downloads",
        "_release_abandoned_hold",
        "clearQueue",
        "clearQueued",
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


# --------------------------------------------------------------------------- #
# 1. A press reaches the hold, not just the row.
#
# The gate stashes a replay BEFORE it returns False, so a press that lands
# while the worker is inside the reachability probe (seconds, against a
# sleeping share) was recorded as an abort nothing on the held path ever read.
# The album the user cancelled came back on its own when the share woke, into a
# queue with no row left to stop it a second time.
# --------------------------------------------------------------------------- #
def test_a_clear_takes_the_held_replay_with_the_row():
    s = _queue_stub(["queued"])
    s._pending_downloads = [("m1", lambda: None)]

    s.clearQueue()

    assert s._pending_downloads == [], "the cleared album was left held for the recovery watch to replay"


def test_a_cancel_takes_the_held_replay_with_the_row():
    s = _queue_stub(["queued"])
    s._pending_downloads = [("m1", lambda: None)]

    s.cancelQueueItem(1)

    assert s._pending_downloads == [], "the cancelled album was left held for the recovery watch to replay"


def test_a_clear_leaves_another_items_hold_alone():
    """Only what the press withdrew: a hold on an item the clear never touched
    is still waiting for its folder and must survive."""
    s = _queue_stub(["queued"])
    s._pending_downloads = [("m1", lambda: None), ("other", lambda: None)]

    s.clearQueued()

    assert [mid for mid, _fn in s._pending_downloads] == ["other"]


class _InlinePool:
    def start(self, worker) -> None:
        worker.run()


class _Engine:
    path_base = ""
    write_count = ok_count = fail_count = unavailable_count = 0
    list_unavailable = False

    def items(self, **kwargs):
        raise AssertionError("a stopped job must never reach the engine")

    def item(self, **kwargs):
        raise AssertionError("a stopped job must never reach the engine")

    def close_segment_pool(self) -> None:
        pass


class _JobStub:
    """Enough bridge for one job body whose folder does not answer, with the
    press landing while the probe runs (the window the gate stashes in)."""

    def __init__(self, *, press: str, merge_plan=None) -> None:
        self._press = press
        self._logged_in = True
        self._job_aborts: dict = {}
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
        self.dl = _Engine()
        self.dl_pool = _InlinePool()
        self.downloadState = _Sig()
        self.downloadProgress = _Sig()
        self.statuses: list = []
        self._track_poll = SimpleNamespace(isActive=lambda: True, start=lambda *a: None)
        self._merge_plan_arg = merge_plan
        from _dispatch_stub import arm_dispatch

        arm_dispatch(self)
        self._jobFinished = _Sig()
        self._emit_queue = lambda: None
        for n in (
            "_reindex_queue",
            "_queue_item",
            "_remove_rows_where",
            "_remove_row",
            "_discard_pending_downloads",
            "_release_abandoned_hold",
        ):
            setattr(self, n, _bind(self, n))

    def _set_queue_status(self, qid, status, reason: str = "") -> None:
        self._queue[0]["status"] = status
        self.statuses.append(status)

    def _set_queue_progress(self, qid, pct) -> None:
        pass

    def _set_status(self, msg) -> None:
        pass

    def _job_library_skip(self, qid: int) -> bool:
        return False

    def _row_ask(self, qid):
        return None  # a held retry asks at what its row asked; no row ask here

    def _job_quality(self, qid):
        return None

    def _build_download(self, signals, **kwargs):
        return self.dl

    def _release_job_signals(self, qid) -> None:
        self._job_signals.pop(qid, None)

    def _gate_reachability(self, retry, media_id) -> bool:
        """The folder does not answer, so the gate stashes the replay and the
        job body withdraws the row. The press lands mid-probe: "clear" takes
        the row away as CLEAR ALL and CANCEL do, "stop" marks it cancelled and
        leaves it in the Stopped section as STOP does. Both set the abort."""
        if self._press == "clear":
            self._queue.clear()
            self._queue_index.clear()
            self._job_aborts[1].set()
        elif self._press == "stop":
            self._queue[0]["status"] = "cancelled"
            self._job_aborts[1].set()
        self._pending_downloads.append((media_id, retry))
        return False


def _drive(stub: _JobStub, *, merge_plan=None) -> _JobStub:
    """Run one job body against ``stub``, start to finish, on this thread."""
    from unittest.mock import patch

    from waves.waves_ui import backend
    from waves.waves_ui.backend import _JobSpec

    media = SimpleNamespace(
        id="m1", name="Album", artist=SimpleNamespace(name="Artist"), artists=[], audio_quality=None, duration=200
    )
    spec = _JobSpec(media, "album", "Album", "{title}", True, "m1", merge_plan)
    with patch.object(backend, "_ProgressSignals", lambda *a, **k: object()):
        WavesBridge._start_job(stub, 1, spec)
    return stub


def _run_held(*, press: str, merge_plan=None) -> _JobStub:
    return _drive(_JobStub(press=press, merge_plan=merge_plan), merge_plan=merge_plan)


def test_a_press_during_the_probe_drops_the_replay_the_gate_stashed():
    stub = _run_held(press="clear")

    assert (
        stub._pending_downloads == []
    ), "the cancelled album stayed in the stash and downloads itself when the share answers"


def test_a_stop_during_the_probe_keeps_its_stopped_row():
    """STOP keeps every stopped row in its own section with RETRY (issue #27).
    The hold's withdrawal would have deleted the row the press just marked,
    leaving the album no record and no control at all."""
    stub = _run_held(press="stop")

    assert stub._queue and stub._queue[0]["status"] == "cancelled", "the press lost its Stopped row"
    assert stub._pending_downloads == []


def test_a_hold_that_nobody_pressed_still_keeps_its_replay_and_plan():
    """The hold itself must go on working: this is the path that exists so a
    sleeping share resumes by itself."""
    plan = ["a plan the scan stashed"]
    stub = _run_held(press="", merge_plan=plan)

    assert [mid for mid, _fn in stub._pending_downloads] == ["m1"]
    assert stub._merge_plans.get("m1") == plan, "the replay lost the plan it is going to run"
    assert stub._queue == [], "a genuine hold still withdraws its row"


def test_a_press_during_the_probe_releases_the_plan_it_would_have_replayed():
    plan = ["a plan the scan stashed"]
    stub = _run_held(press="clear", merge_plan=plan)

    assert "m1" not in stub._merge_plans, "an abandoned hold left its plan for the next plain click"


def test_a_stopped_row_keeps_the_state_its_retry_reads_back():
    """STOP keeps the row, and a stopped row has RETRY on it. The plan, the
    REDOWNLOAD force and the claim override are what that RETRY reads back, so
    releasing them for a row that is still on screen leaves the retry to come
    back as a plain, unforced download: it writes the identity edition over
    the tracks the merge borrowed, or skips every file the user had just
    confirmed replacing, and says it finished."""
    plan = ["a plan the scan stashed"]
    stub = _JobStub(press="stop", merge_plan=plan)
    stub._redownload_overrides.add("m1")
    stub._library_claim_overrides.add("m1")
    stub._merge_plans["m1"] = plan
    _drive(stub, merge_plan=plan)

    assert stub._queue and stub._queue[0]["status"] == "cancelled", "the press lost its Stopped row"
    assert stub._merge_plans.get("m1") == plan, "the retry on that row comes back a plain album"
    assert "m1" in stub._redownload_overrides, "the retry on that row comes back unforced"
    assert "m1" in stub._library_claim_overrides


def _folder_death_stub(*, dead: bool = True):
    """A bridge stand-in for the mid-download folder-death path: the share was
    fine at the gate and dies while the bytes are landing."""
    s = _queue_stub(["running"])
    s._probe_download_base = lambda timeout_s=4.0: (("timeout", "") if dead else ("ok", ""))
    s._last_probe_remounted = False
    s._recovery_dialog_shown = False
    s._recovery_dialog_deadline = 0.0
    s._WARMUP_DIALOG_DELAY_SEC = WavesBridge._WARMUP_DIALOG_DELAY_SEC
    # The press settles the row where it stands, the way every other abort
    # reading in the job body does; the row itself stays for the Stopped
    # section, so this records the status rather than delivering it.
    s._set_queue_status = lambda qid, status, reason="": s._queue_index[qid].update(status=status, reason=reason)
    s._recoveryWatchWanted = _Sig()
    s.downloadFolderRecovered = _Sig()
    s._stash_pending_download = _bind(s, "_stash_pending_download")
    s._download_failed_with_folder = _bind(s, "_download_failed_with_folder")
    return s


def test_a_press_while_the_folder_death_is_probed_ends_the_download():
    """The gate is not the only place that stashes. When the share dies MID
    download the same hold is taken, behind another multi-second probe (plus a
    remount), and the caller read the abort before that probe began: without a
    second reading, STOP left a replay it had already drained for and lost the
    Stopped row it had just marked."""
    s = _folder_death_stub()
    abort = Event()
    abort.set()

    claimed = s._download_failed_with_folder(lambda: None, "m1", 1, "Album", abort)

    assert claimed is True, "the caller would mark a stopped download failed"
    assert s._pending_downloads == [], "the stopped album was left to start itself again"
    assert s._queue and s._queue[0]["status"] == "cancelled", "the press lost its Stopped row"


def test_a_folder_death_with_no_press_still_holds_the_download():
    """The hold itself must go on working: this is the path that exists so a
    share dying mid-download resumes by itself instead of failing red."""
    s = _folder_death_stub()

    claimed = s._download_failed_with_folder(lambda: None, "m1", 1, "Album", Event())

    assert claimed is True
    assert [mid for mid, _fn in s._pending_downloads] == ["m1"], "the download was not held for recovery"
    assert s._queue == [], "the held download's row was left behind"


def test_a_track_failure_over_a_healthy_folder_is_still_the_tracks_own():
    s = _folder_death_stub(dead=False)

    assert s._download_failed_with_folder(lambda: None, "m1", 1, "Album", Event()) is False
    assert s._pending_downloads == []


# --------------------------------------------------------------------------- #
# 2. A hold keeps the whole of its per-row state, not only the merge plan.
#
# The replay re-reads the REDOWNLOAD force and the library-claim override when
# it builds its job, so releasing them at the withdrawal left a held REDOWNLOAD
# coming back as an ordinary download: with every file already on disk, it
# skipped all of them and reported done, silently, over the copies the user had
# just confirmed replacing.
# --------------------------------------------------------------------------- #
def test_a_held_withdrawal_keeps_the_redownload_force_and_the_claim_override():
    s = _queue_stub(["queued"])
    s._redownload_overrides.add("m1")
    s._library_claim_overrides.add("m1")
    s._merge_plans["m1"] = ["a plan the scan stashed"]
    # The gate stashed this download's replay and is now withdrawing its row.
    s._pending_downloads = [("m1", lambda: None)]

    s._remove_row(1)

    assert "m1" in s._redownload_overrides, "the replay comes back unforced and skips every file"
    assert "m1" in s._library_claim_overrides, "the replay comes back claimable and skips the album"
    assert "m1" in s._merge_plans


def test_an_ordinary_withdrawal_still_releases_all_three():
    """The release itself has to keep working: a row withdrawn with nothing
    held must not leave a session-wide force behind for the next click."""
    s = _queue_stub(["queued"])
    s._redownload_overrides.add("m1")
    s._library_claim_overrides.add("m1")
    s._merge_plans["m1"] = ["a plan the scan stashed"]

    s._remove_row(1)

    assert "m1" not in s._redownload_overrides
    assert "m1" not in s._library_claim_overrides
    assert "m1" not in s._merge_plans


# --------------------------------------------------------------------------- #
# 3. An abandoned hold releases what it was keeping.
#
# The hold is the only reason that state survives the withdrawal, and a held
# download has no row, so when the hold is abandoned instead of replayed
# nothing else can ever release it: no later withdrawal walks this item again.
# The plan was the visible one, surviving with no row, no RETRY and no replay
# until a much later plain click on the album picked it up and quietly built a
# cross-edition copy, with "best of both" since turned off.
# --------------------------------------------------------------------------- #
def test_stop_releases_the_plan_and_force_of_the_holds_it_drains():
    s = _queue_stub(["running"])
    s._pending_downloads = [("m9", lambda: None)]
    s._merge_plans["m9"] = ["a plan the scan stashed"]
    s._redownload_overrides.add("m9")
    s._library_claim_overrides.add("m9")
    s._recovery_poll = SimpleNamespace(stop=lambda: None, start=lambda: None, isActive=lambda: True)

    s.stopAll()

    assert "m9" not in s._merge_plans, "the abandoned hold left its plan for the next plain click"
    assert "m9" not in s._redownload_overrides
    assert "m9" not in s._library_claim_overrides


def test_dismissing_the_folder_nudge_releases_the_plan_of_the_hold_it_drops():
    s = _queue_stub(["done"])
    s._pending_downloads = [("m9", lambda: None)]
    s._merge_plans["m9"] = ["a plan the scan stashed"]
    s._redownload_overrides.add("m9")

    s.dismissDownloadFolderNudge()

    assert "m9" not in s._merge_plans, "the abandoned hold left its plan for the next plain click"
    assert "m9" not in s._redownload_overrides


def test_a_hold_abandoned_while_a_live_row_still_wants_it_keeps_its_force():
    """A retry re-queues the item before its hold is dropped: the live row
    still holds the force, exactly the rule the withdrawal itself uses."""
    s = _queue_stub(["queued"])  # m1 is live
    s._pending_downloads = [("m1", lambda: None)]
    s._redownload_overrides.add("m1")

    s.dismissDownloadFolderNudge()

    assert "m1" in s._redownload_overrides


# --------------------------------------------------------------------------- #
# 4. What a clear credits is read from the removal, under its lock.
#
# _pump_queue hands a row to the pool while it still reads "queued": the flip
# to "running" happens on the worker thread, after the folder probe. A clear
# that lists the queued rows in one lock and removes them in another can be
# overtaken in between, and then credited a FAILURE to the rollup of a row it
# did not remove, one that went on to download and land: the artist button
# turned red over an album that arrived.
# --------------------------------------------------------------------------- #
def test_a_clear_credits_only_the_rows_it_actually_removed():
    s = _queue_stub(["queued", "queued"])
    real_remove = _bind(s, "_remove_rows_where")

    def worker_gets_there_first(pred, withdrawn_out=None):
        """m2's job passed the folder probe on the worker thread and flipped
        its row to running, in the window between a caller listing the queued
        rows and the removal pass deciding which of them actually go."""
        s._queue[1]["status"] = "running"
        return real_remove(pred, withdrawn_out)

    s._remove_rows_where = worker_gets_there_first
    s.clearQueued = _bind(s, "clearQueued")

    s.clearQueued()

    failed = s._artist_groups.get("art1", {}).get("failed", set())
    assert "m2" not in failed, "a row that had just started running was credited as failed"
    assert s._queue[-1]["media_id"] == "m2", "the running row was withdrawn after all"
    # The credit itself still has to happen for the row that really did go.
    assert "m1" in failed, "a row withdrawn before it started was left uncredited"


# --------------------------------------------------------------------------- #
# 5. The motion-background sweep deletes only what Waves wrote.
#
# The sweep runs inside the user's own config folder. "Everything that is not
# the current copy" is not a description of what Waves wrote, so anything else
# that ended up in that folder, by any route, was deleted with the stale
# copies. The app never deletes a file it did not write.
# --------------------------------------------------------------------------- #
def test_the_motion_cache_sweep_is_anchored_to_waves_own_names():
    """The sweep lives in a closure inside motionVideoUrl, staged 15 s after
    boot: what is pinned here is that it decides by the shared anchored names
    rather than by "everything that is not the current copy", which is what
    made it delete a bystander."""
    import inspect

    from waves.waves_ui import backend as bk

    source = inspect.getsource(bk.WavesBridge.motionVideoUrl)
    assert "_MOTION_CACHE_NAMES" in source, "the sweep decides by name shape again, not by what Waves wrote"


def test_the_motion_cache_names_match_only_what_waves_writes():
    from waves.waves_ui import backend as bk

    def swept(name: str) -> bool:
        return any(p.match(name) for p in bk._MOTION_CACHE_NAMES)

    # Waves' own: the copy named by the asset's byte size, and the uuid tmp
    # sibling it is staged through.
    assert swept("wave_loop_111.mp4")
    assert swept("wave_loop_111.mp4.deadbeef.tmp")
    # Anything else in that folder belongs to whoever put it there.
    assert not swept("notes.txt")
    assert not swept("my wave_loop_111.mp4")
    assert not swept("wave_loop_111.mp4.txt")
    assert not swept("wave_loop.mp4")


# --------------------------------------------------------------------------- #
# 6. A pool's high-water mark reaches the perf line.
#
# The sampler reads on a timer and a per-job fan-out can saturate and drain
# entirely between two ticks, so the instantaneous count alone reported a pool
# that spent seconds pinned at its ceiling as 0/N in every line of an export:
# the exact blindness the mark was added to cure.
# --------------------------------------------------------------------------- #
def test_the_perf_line_carries_a_gauges_peak():
    from waves.poolgauge import PoolGauge
    from waves.waves_ui import diagnostics

    gauge = PoolGauge(10)
    # Two work items at once, both finished: the pool reads idle now and the
    # sampler's next tick would see nothing, which is the whole problem.
    with gauge.working(), gauge.working():
        pass
    assert gauge.activeThreadCount() == 0 and gauge.peak == 2

    lines: list = []
    sampler = diagnostics._PerfSampler.__new__(diagnostics._PerfSampler)
    sampler._pools = [("dlseg", gauge)]
    sampler._probe_wall = 0.0
    sampler._probe_busy = 0.0
    sampler._probe_max_stall = 0.0
    sampler._rss_mb = lambda: None
    real_debug = diagnostics.logger.debug
    diagnostics.logger.debug = lambda msg, *a, **kw: lines.append(msg % a if a else msg)
    try:
        sampler._sample()
    finally:
        diagnostics.logger.debug = real_debug

    assert lines, "the sampler emitted nothing"
    assert "dlseg=0/10^2" in lines[0], f"the pool's busiest moment never reached the log: {lines[0]}"
