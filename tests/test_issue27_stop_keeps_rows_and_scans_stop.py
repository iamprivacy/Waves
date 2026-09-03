"""Issue #27: STOP ends a discography scan in flight, keeps the rows it stops,
and the sweep's edition handling follows 'Most-complete edition only'.

WHAT THIS FENCES OFF
--------------------
Three things the report describes, each against the real bridge method run
over a stub carcass (the same pattern as test_discography_video_source.py):

1. STOP during a discography scan. stopAll clears the queue, but a scan still
   gathering on the scan pool holds no row, no abort and no artist group yet,
   so nothing stopAll touched reached it: the scan finished after the press
   and queued the whole discography behind it, with the artist button stuck
   at "running". The scan now checks the generation stopAll bumps at every
   hop that costs a request, and a stale scan drops what it gathered.

2. STOP keeps the rows. stopAll used to empty the queue, so a press over one
   wrong item cost every other row and left no record of what was in flight.
   Now every queued and running row stays, marked cancelled, in a Stopped
   section of its own with the same RETRY, RETRY ALL and CLEAR as Failed.

3. The sweep with 'Most-complete edition only' OFF downloads every edition
   whole. 'Best of both' used to run on the sweep regardless, so a Standard
   beside its Deluxe left as one merged album, and a group whose merge
   declined was collapsed anyway: the switch was dead while on the page.
   Single-album clicks keep merging on their own (downloadAlbum's scan is
   not touched here; tests/test_edition_merge_gate.py covers it).
"""

from __future__ import annotations

from collections import deque
from threading import Event, Lock
from types import SimpleNamespace

from _dispatch_stub import arm_queue
from test_discography_video_source import _Artist as _VideoArtist
from test_discography_video_source import _DiscoStub, _Signal

from waves.waves_ui.backend import _RETRYABLE, WavesBridge, _stop_check_for

# ---------------------------------------------------------------------------
# 1. STOP during a discography scan
# ---------------------------------------------------------------------------


class _HoldingPool:
    """A scan pool that parks the worker until the test releases it, the way a
    second artist's scan waits behind the first on the real single-thread pool."""

    def __init__(self):
        self.held: list = []

    def start(self, worker):
        self.held.append(worker)

    def run_all(self):
        while self.held:
            self.held.pop(0).fn()


class _StopMidScanStub(_DiscoStub):
    """STOP lands in the middle of the scan, at the hop named by ``press_at``:
    ``releases`` (the release listing), ``guest`` (a guest release's track
    fetch) or ``never`` (the control case)."""

    def __init__(self, press_at: str, guest: list | None = None):
        super().__init__(_VideoArtist([]), video_download=False)
        self.press_at = press_at
        self.guest = guest or []
        self.stop_calls = 0
        # The hops that cost a request and ran AFTER the press. The final
        # gate before queueing keeps "nothing queued" true on its own; the
        # per-hop checks are what keep this list empty.
        self.hops_after_stop: list = []

    def _hop(self, name):
        if self.stop_calls:
            self.hops_after_stop.append(name)

    def _press_stop(self):
        # What stopAll does to a scan: bump the generation. The rest of
        # stopAll is covered by the stopAll tests below.
        self._scan_gen += 1
        self.stop_calls += 1

    def _artist_releases(self, artist):
        if self.press_at == "releases":
            self._press_stop()
        return [SimpleNamespace(id="al1"), SimpleNamespace(id="al2")], list(self.guest), True

    def _dedup_tracks(self, tracks):
        return list(tracks)

    def _library_claim_media(self, t, album=None):
        return False


class _GuestRelease:
    def __init__(self, stub, tid):
        self._stub = stub
        self._tid = tid

    def tracks(self):
        self._stub._hop(f"guest:{self._tid}")
        if self._stub.press_at == "guest" and not self._stub.stop_calls:
            self._stub._press_stop()
        return [SimpleNamespace(id=self._tid, artists=[SimpleNamespace(id="art1")], artist=SimpleNamespace(id="art1"))]


def _nothing_queued(stub) -> None:
    assert stub._albumsQueued.emits == [], "a stopped scan queued albums"
    assert stub._tracksQueued.emits == [], "a stopped scan queued guest tracks"
    assert stub._artist_groups == {}, "a stopped scan registered an artist group"
    assert stub._merge_scanned == set(), "a stopped scan left albums marked exempt"


def test_stop_during_the_release_listing_queues_nothing_and_resets_the_button():
    stub = _StopMidScanStub("releases")
    stub.guest = [_GuestRelease(stub, "t1")]
    stub.downloadArtist("art1")
    _nothing_queued(stub)
    # The check after the listing: no guest release is fetched.
    assert stub.hops_after_stop == [], "a hop ran after the press"
    # "running" at click time, then handed back: a stuck button is the
    # report's second symptom.
    assert stub.downloadState.emits == [("art1", "running"), ("art1", "")]


def test_stop_during_a_guest_track_fetch_queues_nothing():
    stub = _StopMidScanStub("guest")
    stub.guest = [_GuestRelease(stub, "t1"), _GuestRelease(stub, "t2")]
    stub.downloadArtist("art1")
    _nothing_queued(stub)
    # The per-release check: the second guest's tracks are never fetched.
    assert stub.hops_after_stop == [], "a hop ran after the press"
    assert stub.downloadState.emits[-1] == ("art1", "")
    # The check sits OUTSIDE the per-release failure guard: a STOP is not a
    # fetch failure, so the "try again" status never appears.
    assert not any("try again" in s for s in stub.statuses)


def test_stop_before_a_parked_scan_starts_queues_nothing():
    # The scan was ordered, then STOP pressed while it still waited behind
    # another scan on the serialised pool.
    stub = _StopMidScanStub("never")
    pool = _HoldingPool()
    stub._scan_pool = pool
    stub.downloadArtist("art1")
    assert pool.held, "the scan was parked on the pool"
    stub._press_stop()
    pool.run_all()
    _nothing_queued(stub)
    assert stub.downloadState.emits == [("art1", "running"), ("art1", "")]


def test_an_unstopped_scan_still_queues_everything():
    # The control case: the same stub with no press queues both albums and
    # registers the group, so the checks above are not a scan that never ran.
    stub = _StopMidScanStub("never")
    stub.downloadArtist("art1")
    assert stub._albumsQueued.emits == [(0, ["al1", "al2"])]
    assert stub._artist_groups["art1"]["keys"] == {"al1", "al2"}


def test_a_parked_scan_says_it_is_scanning_until_it_ends():
    # With an empty queue the drawer's STOP is gated on active rows, and a
    # scan holds none, so the one control that ends a long discography scan
    # was hidden for exactly as long as it was needed. `scanning` is what
    # the button reads instead: up from the moment the scan is ordered,
    # down when the worker ends, whichever way it ends.
    stub = _StopMidScanStub("never")
    pool = _HoldingPool()
    stub._scan_pool = pool
    assert WavesBridge._get_scanning(stub) is False
    stub.downloadArtist("art1")
    assert WavesBridge._get_scanning(stub) is True, "ordered, not yet running: already scanning"
    assert stub.scanningChanged.emits == [()]
    pool.run_all()
    assert WavesBridge._get_scanning(stub) is False
    assert stub.scanningChanged.emits == [(), ()]


def test_a_scan_that_stop_ends_hands_scanning_back_too():
    stub = _StopMidScanStub("never")
    pool = _HoldingPool()
    stub._scan_pool = pool
    stub.downloadArtist("art1")
    stub._press_stop()
    pool.run_all()
    assert WavesBridge._get_scanning(stub) is False


def test_two_scans_in_flight_announce_once_each_way():
    # The signal fires on the edges only (first up, last down): a second
    # scan parked behind the first changes nothing the button can see.
    stub = _StopMidScanStub("never")
    pool = _HoldingPool()
    stub._scan_pool = pool
    stub.downloadArtist("art1")
    stub.downloadArtist("art1")
    assert stub._scans_in_flight == 2
    assert stub.scanningChanged.emits == [()]
    pool.run_all()
    assert stub._scans_in_flight == 0
    assert stub.scanningChanged.emits == [(), ()]


def test_the_drawer_stop_button_reads_scanning():
    # The binding itself: STOP shows for active rows OR a scan in flight.
    import re
    from pathlib import Path

    src = (Path(__file__).resolve().parent.parent / "waves" / "waves_ui" / "qml" / "Main.qml").read_text()
    stop = re.search(r'visible: ([^\n]*)\n\s*danger: true; label: "STOP"', src)
    assert stop, "the drawer's STOP button"
    assert "waves.scanning" in stop.group(1) and "activeQueueCount > 0" in stop.group(1)


def test_a_stop_check_is_stale_only_after_a_bump():
    bridge = SimpleNamespace(_scan_gen=3)
    check = _stop_check_for(bridge)
    check()  # same generation: silent
    bridge._scan_gen += 1
    import pytest

    from waves.waves_ui.backend import _ScanStopped

    with pytest.raises(_ScanStopped):
        check()


# The edition scans take the check too: STOP during the per-edition track
# fetch ('Scanning editions…' is the long part of a big discography).


class _EditionAlbum:
    def __init__(self, stub, aid, title, tracks):
        self._stub = stub
        self.id = aid
        self.name = title
        self.full_name = title
        self.artist = SimpleNamespace(name="Artist", id="art1")
        self.artists = [self.artist]
        self.audio_modes = ["STEREO"]
        self._tracks = tracks

    def tracks(self):
        self._stub._hop(f"edition:{self.id}")
        self._stub._press_stop()
        return [
            SimpleNamespace(id=f"{self.id}-{n}", name=t, full_name=t, duration=200, isrc=None, explicit=False)
            for n, t in enumerate(self._tracks)
        ]


class _StopInEditionScanStub(_StopMidScanStub):
    """'Most-complete edition only' on, so the sweep scans editions; STOP lands
    inside the first track fetch of that scan."""

    _collapse_editions = WavesBridge._collapse_editions
    _merge_editions = WavesBridge._merge_editions
    _merge_recs_factory = WavesBridge._merge_recs_factory

    def __init__(self, merge: bool):
        super().__init__("editions")
        self.merge = merge
        self._waves_prefs = {"edition_conflict": "merge" if merge else "completeness", "explicit_mode": "explicit"}

    def _waves_pref_bool(self, key):
        return key == "collapse_editions"

    def _merge_pref_on(self):
        return self.merge

    def _merge_rank_fn(self):
        return lambda album: 1

    def _artist_releases(self, artist):
        std = _EditionAlbum(self, "std", "Album", ["a", "b"])
        dlx = _EditionAlbum(self, "dlx", "Album (Deluxe)", ["a", "b", "c"])
        return [std, dlx], [], True


def test_stop_during_the_collapse_scan_queues_nothing():
    stub = _StopInEditionScanStub(merge=False)
    stub.downloadArtist("art1")
    assert stub.stop_calls >= 1, "the edition scan fetched at least one track list"
    _nothing_queued(stub)
    # The per-edition check: the second edition's tracks are never fetched.
    assert stub.hops_after_stop == [], "a hop ran after the press"
    assert stub.downloadState.emits[-1] == ("art1", "")


def test_stop_during_the_best_of_both_scan_queues_nothing():
    stub = _StopInEditionScanStub(merge=True)
    stub.downloadArtist("art1")
    assert stub.stop_calls >= 1
    _nothing_queued(stub)
    assert stub.downloadState.emits[-1] == ("art1", "")


# ---------------------------------------------------------------------------
# 2. STOP keeps the rows
# ---------------------------------------------------------------------------


class _QueueStub:
    stopAll = WavesBridge.stopAll
    retryAllFailed = WavesBridge.retryAllFailed
    retryAllStopped = WavesBridge.retryAllStopped
    _retry_all_with_status = WavesBridge._retry_all_with_status
    clearFailed = WavesBridge.clearFailed
    clearStopped = WavesBridge.clearStopped
    clearFinished = WavesBridge.clearFinished
    clearQueue = WavesBridge.clearQueue
    _set_queue_status = WavesBridge._set_queue_status
    _queue_item = WavesBridge._queue_item
    _reindex_queue = WavesBridge._reindex_queue
    _remove_rows_where = WavesBridge._remove_rows_where
    _remove_row = WavesBridge._remove_row
    _row_object = WavesBridge._row_object

    def __init__(self, statuses):
        self._queue = [
            {"qid": n, "media_id": f"m{n}", "status": st, "type": "album", "name": f"r{n}"}
            for n, st in enumerate(statuses, 1)
        ]
        self._queue_lock = Lock()
        self._queue_index = {it["qid"]: it for it in self._queue}
        self._job_aborts = {it["qid"]: Event() for it in self._queue if it["status"] in ("queued", "running")}
        self._event_run = Event()
        self._paused = True
        self.pausedChanged = _Signal()
        self._artist_groups = {"art1": {"keys": {"m1"}, "done": set(), "failed": set(), "prog": {}}}
        self._artist_lock = Lock()
        self._folder_groups: dict = {}
        self._folder_lock = Lock()
        self._scan_gen = 0
        self._scans_in_flight = 0
        self._scan_count_lock = Lock()
        self.scanningChanged = _Signal()
        self.downloadState = _Signal()
        self.statuses: list = []
        self.emits = 0
        self.retried: list = []
        self._queue_emit_suspended = False
        self._pending_qids = deque()
        arm_queue(self)
        # Every row keeps its live object now (RETRY re-downloads from it).
        self._job_objs = {it["qid"]: object() for it in self._queue}

    def _emit_queue(self):
        # The real gate: a suspended emit is dropped, the batch emits once.
        if self._queue_emit_suspended:
            return
        self.emits += 1

    def _set_status(self, text):
        self.statuses.append(text)

    def _start_retry(self, item, obj):
        # RETRY ALL drops the rows in one pass, then re-downloads each from
        # the object its row kept; the re-download lands here.
        self.retried.append(item["qid"])

    def _retry_queue_refetch(self, item):
        self.retried.append(("refetch", item["qid"]))

    def by_status(self):
        return [it["status"] for it in self._queue]


def test_stop_marks_queued_and_running_rows_stopped_and_keeps_every_row():
    stub = _QueueStub(["done", "failed", "running", "queued", "queued"])
    stub.stopAll()
    # Every row is still there; the live ones read cancelled, the settled
    # ones are untouched.
    assert stub.by_status() == ["done", "failed", "cancelled", "cancelled", "cancelled"]
    # The transfers themselves are still ended.
    assert all(ev.is_set() for ev in stub._job_aborts.values())
    # The scan generation moved, so any scan in flight is stale.
    assert stub._scan_gen == 1
    # Buttons of the stopped rows (and the artist aggregate) go back to idle;
    # the settled rows' buttons are left alone.
    assert set(stub.downloadState.emits) == {("m3", ""), ("m4", ""), ("m5", ""), ("art1", "")}
    assert stub._artist_groups == {}
    assert stub._paused is False
    assert stub.statuses[-1] == "Downloads stopped"


def test_the_aborted_workers_later_mark_is_the_same_status_and_emits_nothing():
    # stopAll marked the rows in one pass; each aborted Worker reaching its
    # own cancelled mark afterwards must not re-marshal the queue per row.
    stub = _QueueStub(["running", "queued"])
    stub.stopAll()
    before = stub.emits
    stub._set_queue_status(1, "cancelled")
    stub._set_queue_status(2, "cancelled")
    assert stub.emits == before
    # A real change still emits.
    stub._set_queue_status(2, "queued")
    assert stub.emits == before + 1


def test_each_sections_retry_all_takes_its_own_rows_only():
    stub = _QueueStub(["done", "failed", "cancelled", "queued", "cancelled"])
    stub.retryAllFailed()
    assert stub.retried == [2], "Failed's RETRY ALL took a stopped row"
    stub.retried.clear()
    stub.retryAllStopped()
    assert stub.retried == [3, 5], "Stopped's RETRY ALL strayed, or lost the queue order"


def test_retry_all_marshals_the_queue_once_for_the_lot():
    # A STOP leaves hundreds of rows in one section. Each retry drops its
    # row and enqueues a fresh one, two emits of the whole queue apiece, and
    # QML reconciles every row on each: RETRY ALL over them was quadratic
    # on the GUI thread (1.5 s at 200 rows, 9.4 s at 500). Batched like a
    # discography's enqueue: one queueChanged at the end.
    stub = _QueueStub(["cancelled"] * 300)
    stub.retryAllStopped()
    assert len(stub.retried) == 300
    assert stub.emits == 1
    assert stub._queue_emit_suspended is False, "the gate is released for what follows"
    stub = _QueueStub(["failed"] * 50)
    stub.retryAllFailed()
    assert stub.emits == 1


def test_a_retry_that_raises_costs_only_its_own_row():
    """A kept object can raise on a property (a stale tidalapi object does).
    RETRY ALL dropped every retryable row before re-starting any of them, so
    one raise took every row after it with no retry started and no row left to
    press RETRY on again: exactly the loss the Failed section exists to
    prevent (issue #18)."""
    stub = _QueueStub(["cancelled", "cancelled", "cancelled"])
    bad = stub._queue[1]["qid"]

    def start(item, obj):
        if item["qid"] == bad:
            raise RuntimeError("a row that would not re-queue")
        stub.retried.append(item)

    stub._start_retry = start
    stub.retryAllStopped()

    assert len(stub.retried) == 2, "the rows either side of the raise still retried"
    assert [it["qid"] for it in stub._queue] == [bad], "the row that could not restart keeps its place"
    assert stub._queue_emit_suspended is False, "a raise inside the batch must not mute the queue for good"
    assert stub.emits == 1, "and the batch is still one delivery"


def test_each_sections_clear_leaves_the_stopped_rows_to_their_own():
    stub = _QueueStub(["done", "failed", "cancelled", "queued"])
    stub.clearFinished()
    assert stub.by_status() == ["failed", "cancelled", "queued"], "Completed's CLEAR took a stopped row"
    stub.clearFailed()
    assert stub.by_status() == ["cancelled", "queued"], "Failed's CLEAR took a stopped row"
    stub.clearStopped()
    assert stub.by_status() == ["queued"]


def test_stoppeds_clear_never_takes_a_failure():
    stub = _QueueStub(["failed", "cancelled", "running"])
    stub.clearStopped()
    assert stub.by_status() == ["failed", "running"]


def test_clear_all_still_sweeps_stopped_rows():
    stub = _QueueStub(["cancelled", "running", "failed"])
    stub.clearQueue()
    assert stub.by_status() == ["running"]


def test_retry_accepts_exactly_failed_and_stopped():
    assert frozenset({"failed", "cancelled"}) == _RETRYABLE


class _RetryStub:
    retryQueueItem = WavesBridge.retryQueueItem
    _queue_item = WavesBridge._queue_item
    _reindex_queue = WavesBridge._reindex_queue
    _remove_rows_where = WavesBridge._remove_rows_where
    _remove_row = WavesBridge._remove_row
    _row_object = WavesBridge._row_object
    _start_retry = WavesBridge._start_retry

    def __init__(self, status):
        self._queue = [
            {
                "qid": 1,
                "media_id": "m1",
                "status": status,
                "type": "album",
                "name": "r1",
                "template": "t",
                "collection": True,
            }
        ]
        self._queue_lock = Lock()
        self._queue_index = {1: self._queue[0]}
        self._objs = {"album": {"m1": object()}}
        self._merge_plans: dict = {}
        self.downloads: list = []
        self.emits = 0
        arm_queue(self)

    def _emit_queue(self):
        self.emits += 1

    def _download(self, obj, type_media, name, template, collection, media_id, merge_plan=None, keep_ask=None):
        self.downloads.append(media_id)


def test_a_stopped_row_retries_like_a_failed_one():
    for st in ("failed", "cancelled"):
        stub = _RetryStub(st)
        stub.retryQueueItem(1)
        assert stub.downloads == ["m1"], st
        assert stub._queue == [], st


def test_a_live_row_is_not_retried():
    for st in ("queued", "running", "done"):
        stub = _RetryStub(st)
        stub.retryQueueItem(1)
        assert stub.downloads == [], st


# ---------------------------------------------------------------------------
# 3. The sweep's edition handling follows 'Most-complete edition only'
# ---------------------------------------------------------------------------


class _EditionGateStub(_DiscoStub):
    def __init__(self, collapse: bool, merge: bool):
        super().__init__(_VideoArtist([]), video_download=False)
        self.collapse = collapse
        self.merge = merge
        self.calls: list = []

    def _waves_pref_bool(self, key):
        return self.collapse if key == "collapse_editions" else False

    def _merge_pref_on(self):
        return self.merge

    def _artist_releases(self, artist):
        return [SimpleNamespace(id="std"), SimpleNamespace(id="dlx")], [], True

    def _merge_editions(self, albums, stop_check=None):
        self.calls.append("merge")
        # What a merge does to a Standard + Deluxe pair: one identity.
        return [], [(albums[1], [("plan",)])]

    def _collapse_editions(self, albums, stop_check=None):
        self.calls.append("collapse")
        return [albums[1]]


def test_with_the_switch_off_every_edition_downloads_whole_even_with_best_of_both_on():
    stub = _EditionGateStub(collapse=False, merge=True)
    stub.downloadArtist("art1")
    assert stub.calls == [], "the sweep merged or collapsed with 'Most-complete edition only' off"
    assert stub._albumsQueued.emits == [(0, ["std", "dlx"])]
    assert stub._merge_plans == {}


def test_with_the_switch_off_and_best_of_both_off_nothing_is_scanned_either():
    stub = _EditionGateStub(collapse=False, merge=False)
    stub.downloadArtist("art1")
    assert stub.calls == []
    assert stub._albumsQueued.emits == [(0, ["std", "dlx"])]


def test_a_plan_an_earlier_run_left_behind_does_not_merge_with_the_switch_off():
    # A 'best of both' run with the switch on stashes its plan under the
    # complete edition's key, and only a SUCCESSFUL download pops it (so a
    # failed merge can be retried as a merge). Stopped or failed, the plan
    # outlives the run. The user then switches 'Most-complete edition only'
    # off and sweeps again: downloadAlbum peeks the stash unconditionally,
    # so without the sweep clearing it the album merged against the setting.
    stub = _EditionGateStub(collapse=True, merge=True)
    stub.downloadArtist("art1")
    assert stub._merge_plans == {"dlx": [("plan",)]}, "the earlier run's plan is stashed"

    stub.collapse = False
    stub._albumsQueued.emits.clear()
    stub.downloadArtist("art1")
    assert stub._albumsQueued.emits == [(0, ["std", "dlx"])]
    assert stub._merge_plans == {}, "the sweep that decided 'plain' cleared the plan it did not make"


def test_a_plan_an_earlier_run_left_behind_survives_a_sweep_that_merges_again():
    # The converse: with the switch still on the new sweep makes its own
    # plan for the same key, which simply replaces the old one.
    stub = _EditionGateStub(collapse=True, merge=True)
    stub.downloadArtist("art1")
    stub._merge_plans["dlx"] = [("stale",)]
    stub.downloadArtist("art1")
    assert stub._merge_plans == {"dlx": [("plan",)]}


def test_with_the_switch_on_best_of_both_builds_the_one_edition():
    stub = _EditionGateStub(collapse=True, merge=True)
    stub.downloadArtist("art1")
    assert stub.calls == ["merge"]
    assert stub._albumsQueued.emits == [(0, ["dlx"])]
    assert "dlx" in stub._merge_plans


def test_with_the_switch_on_and_best_of_both_off_the_plain_collapse_runs():
    stub = _EditionGateStub(collapse=True, merge=False)
    stub.downloadArtist("art1")
    assert stub.calls == ["collapse"]
    assert stub._albumsQueued.emits == [(0, ["dlx"])]


def test_the_help_says_the_sweep_follows_the_switch():
    # The control stays visible (hiding it is the old silent-merge bug); the
    # words are what tell the user when it applies to a discography. Read
    # from the settings schema, the way the page reads it, so the sentences
    # are pinned to the FIELDS they belong to: a substring search over the
    # whole bridge source passed with them moved into a comment or onto the
    # wrong field.
    from test_discography_video_source import _schema_stub

    fields = {f["key"]: f for s in WavesBridge.settingsSchema(_schema_stub()) for f in s["fields"]}
    conflict = fields["edition_conflict"]["help"]
    collapse = fields["collapse_editions"]["help"]
    assert "'Best of both' included, only" in conflict
    assert "takes effect when 'Most-complete edition only' is on" in conflict
    assert "With this off, every edition is downloaded as it is." in collapse
    # 550c7e1 put a playlist's full-albums sweep behind the same switch; both
    # helps name it.
    assert "'Download full albums'" in conflict and "'Download full albums'" in collapse
