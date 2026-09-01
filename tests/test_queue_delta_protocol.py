"""The queue tells QML what changed, and builds one job at a time.

WHAT THIS FENCES OFF (issue #30's lag half)
-------------------------------------------
Two designs that each cost O(queue) for O(1) of news, measured with the
stress harness (scratchpad/queue_stress) before the change:

* Every row mutation emitted the WHOLE queue (queueChanged), and QML
  reconciled every row against the copy: 19 ms per change at 9,000 rows, and
  a worker thread emitting snapshots faster than the window absorbed them
  left a full copy of the queue in every queued signal (13 GB of growth
  while a blocked account failed 2,000 queued albums). Now a mutation marks
  its qids dirty and one GUI-thread flush emits three delta signals carrying
  only the rows concerned; queueChanged remains for the rare full resync.

* Every queued row was born holding its whole job: a Download object, a rich
  Progress, a relay QObject and a pooled Worker (about 19 KB apiece), with
  the 500 ms track poll walking all of them. Now a queued row waits as a
  _JobSpec and _pump_queue builds the job when the pool is free, in queue
  order: one Worker alive at a time however long the backlog.
"""

from __future__ import annotations

import threading
from threading import Lock
from unittest.mock import patch

from _dispatch_stub import arm_dispatch, arm_queue

from waves.waves_ui import backend
from waves.waves_ui.backend import WavesBridge


def _plain_relay():
    """The per-job relay is a QObject parented to the bridge; these carcasses
    are not QObjects, and the relay's wiring is not what is under test."""
    return patch.object(backend, "_ProgressSignals", lambda *a, **k: object())


class _Sig:
    def __init__(self, log: list, name: str) -> None:
        self._log = log
        self._name = name

    def emit(self, *args) -> None:
        self._log.append((self._name, args[0] if len(args) == 1 else args))


class _Stub:
    """A queue carcass with the real delta pipeline bound on."""


def _bind(stub, *names):
    for name in names:
        setattr(stub, name, getattr(WavesBridge, name).__get__(stub, type(stub)))


def _delta_stub():
    stub = _Stub()
    stub._queue = []
    stub._queue_index = {}
    stub._queue_seq = 0
    stub._queue_lock = Lock()
    stub._queue_emit_suspended = False
    stub._target_tier = lambda: "LOSSLESS"
    stub._queued_quality_value = lambda: "LOSSLESS"
    stub._library_bulk_skip_on = lambda: True
    stub._QUEUE_SETTLED = WavesBridge._QUEUE_SETTLED
    stub._QUEUE_HISTORY_MAX = WavesBridge._QUEUE_HISTORY_MAX
    arm_queue(stub)
    stub.log = []
    stub.queueChanged = _Sig(stub.log, "full")
    stub.queueRowsAdded = _Sig(stub.log, "added")
    stub.queueRowsChanged = _Sig(stub.log, "changed")
    stub.queueRowsRemoved = _Sig(stub.log, "removed")
    stub._queueFlushRequested = _Sig(stub.log, "flush-request")
    _bind(
        stub,
        "_enqueue",
        "_reindex_queue",
        "_queue_item",
        "_queue_mark_changed",
        "_remove_rows_where",
        "_remove_row",
        "_queue_resync",
        "_emit_queue",
        "_flush_queue_changes",
        "_trim_queue_history",
        "_prune_job_tracks",
        "_set_queue_status",
    )
    return stub


def _payloads(stub, name):
    return [args for n, args in stub.log if n == name]


# ---- what one change costs the wire -----------------------------------------


def test_an_enqueue_emits_one_add_carrying_only_the_new_row():
    stub = _delta_stub()
    qid = stub._enqueue("A", "album", media_id="m1")
    adds = _payloads(stub, "added")
    assert [len(a) for a in adds] == [1]
    assert adds[0][0]["qid"] == qid
    assert not _payloads(stub, "full") and not _payloads(stub, "changed")


def test_a_status_change_emits_one_patch_for_that_row_alone():
    stub = _delta_stub()
    qids = [stub._enqueue(n, "album", media_id=n) for n in ("A", "B", "C")]
    stub.log.clear()
    stub._set_queue_status(qids[1], "running")
    changed = _payloads(stub, "changed")
    assert [len(c) for c in changed] == [1]
    assert changed[0][0]["qid"] == qids[1] and changed[0][0]["status"] == "running"
    assert not _payloads(stub, "full") and not _payloads(stub, "added")


def test_a_removal_emits_the_qids_that_left():
    stub = _delta_stub()
    qids = [stub._enqueue(n, "album", media_id=n) for n in ("A", "B", "C")]
    stub.log.clear()
    stub._remove_rows_where(lambda it: it["qid"] != qids[0])
    stub._emit_queue()
    assert _payloads(stub, "removed") == [qids[1:]]


def test_a_batch_flushes_once_with_everything():
    # The discography path: emits suspended, one delivery for the lot.
    stub = _delta_stub()
    stub._queue_emit_suspended = True
    qids = [stub._enqueue(str(n), "album", media_id=str(n)) for n in range(40)]
    assert stub.log == [], "nothing may cross while the batch is open"
    stub._queue_emit_suspended = False
    stub._emit_queue()
    adds = _payloads(stub, "added")
    assert [len(a) for a in adds] == [40]
    assert [r["qid"] for r in adds[0]] == qids


def test_a_patch_for_a_row_added_in_the_same_flush_rides_the_add():
    stub = _delta_stub()
    stub._queue_emit_suspended = True
    qid = stub._enqueue("A", "album", media_id="m1")
    stub._set_queue_status(qid, "running")
    stub._queue_emit_suspended = False
    stub._emit_queue()
    adds = _payloads(stub, "added")
    assert [r["status"] for r in adds[0]] == ["running"], "the add carries the row's current fields"
    assert not _payloads(stub, "changed"), "no second copy of a row the add already delivered"


def test_a_wholesale_patch_set_falls_back_to_one_resync():
    # STOP over thousands of queued rows: one reconcile is cheaper than that
    # many per-row patches, and nothing visible differs.
    stub = _delta_stub()
    stub._queue_emit_suspended = True
    qids = [stub._enqueue(str(n), "album", media_id=str(n)) for n in range(1200)]
    stub._queue_emit_suspended = False
    stub._emit_queue()
    stub.log.clear()
    with stub._queue_lock:
        for it in stub._queue:
            it["status"] = "cancelled"
            stub._qdirty_changed[it["qid"]] = None
    stub._emit_queue()
    fulls = _payloads(stub, "full")
    assert len(fulls) == 1 and len(fulls[0]) == 1200
    assert not _payloads(stub, "changed")
    assert all(r["status"] == "cancelled" for r in fulls[0])
    assert qids  # keep the enqueue honest


def test_the_trim_reports_what_it_dropped():
    stub = _delta_stub()
    stub._queue_emit_suspended = True
    qids = [stub._enqueue(str(n), "album", media_id=str(n)) for n in range(stub._QUEUE_HISTORY_MAX + 3)]
    for qid in qids:
        stub._queue_item(qid)["status"] = "done"
        stub._job_tracks[qid] = {"t": {}}
    stub._queue_emit_suspended = False
    stub._emit_queue()
    removed = _payloads(stub, "removed")
    assert removed == [qids[:3]], "oldest settled rows past the cap, reported to QML"
    for qid in qids[:3]:
        assert qid not in stub._job_tracks, "the registry goes with the row"


def test_a_worker_thread_posts_one_flush_request_for_a_burst():
    stub = _delta_stub()
    qid = stub._enqueue("A", "album", media_id="m1")
    stub.log.clear()

    def burst():
        for st in ("running", "failed", "cancelled"):
            stub._queue_item(qid)["status"] = st
            stub._queue_mark_changed(qid)
            stub._emit_queue()

    t = threading.Thread(target=burst)
    t.start()
    t.join()
    assert _payloads(stub, "flush-request") == [()], "one queued request however many changes pile up"
    assert not _payloads(stub, "changed"), "the delivery itself waits for the GUI thread"
    # The GUI thread's flush picks up everything marked by then.
    stub._flush_queue_changes()
    changed = _payloads(stub, "changed")
    assert [len(c) for c in changed] == [1] and changed[0][0]["status"] == "cancelled"


# ---- one job at a time -------------------------------------------------------


class _HoldPool:
    def __init__(self) -> None:
        self.started = []

    def start(self, worker) -> None:
        self.started.append(worker)


def _job_stub(pool=None):
    from types import SimpleNamespace

    stub = _delta_stub()
    stub._logged_in = True
    stub.settings = SimpleNamespace(
        data=SimpleNamespace(download_base_path="/tmp/waves-out", download_delay=False, downloads_concurrent_max=2)
    )
    stub.dl_pool = pool if pool is not None else _HoldPool()
    stub.downloadState = _Sig(stub.log, "state")
    stub.downloadProgress = _Sig(stub.log, "progress")
    stub._job_aborts = {}
    stub._job_signals = {}
    stub._job_dls = {}
    stub._merge_plans = {}
    stub._redownload_overrides = set()
    stub._library_claim_overrides = set()
    stub._download_gate = lambda: "ok"
    stub._ffmpeg_gate_holds = lambda media_id, retry: False
    stub._job_quality = lambda qid: None
    stub._job_library_skip = lambda qid: False
    stub._gate_reachability = lambda retry, media_id: True
    stub._set_queue_progress = lambda qid, pct: None
    stub._set_status = lambda msg: None
    stub._bump_download_groups = lambda media_id, pct, status: None
    stub._release_job_signals = lambda qid: stub._job_signals.pop(qid, None)
    stub.built = []
    stub._build_download = lambda signals, **kw: stub.built.append(kw) or _NullDl()
    _bind(stub, "_download", "cancelQueueItem", "clearQueued", "stopAll", "resumeQueue", "pauseQueue")
    arm_dispatch(stub)
    # stopAll and pause/resume touch more of the bridge than these tests do.
    stub._scan_gen = 0
    stub._event_run = threading.Event()
    stub.pausedChanged = _Sig(stub.log, "paused")
    stub._artist_groups = {}
    stub._artist_lock = Lock()
    stub._folder_groups = {}
    stub._folder_lock = Lock()
    return stub


class _NullDl:
    path_base = ""
    write_count = 1
    ok_count = 1
    fail_count = 0
    unavailable_count = 0
    list_unavailable = False

    def items(self, **kw):
        return None

    def item(self, **kw):
        return True, "/tmp/waves-out/x.flac"


def _albumish(mid: str):
    from types import SimpleNamespace

    return SimpleNamespace(
        id=mid, name=mid, artist=SimpleNamespace(name="A"), artists=[], audio_quality=None, album=None, duration=1
    )


def test_a_backlog_holds_one_job_and_the_rest_wait_as_specs():
    stub = _job_stub()
    with _plain_relay():
        for n in range(5):
            stub._download(_albumish(f"m{n}"), "track", f"m{n}", "{title}", False, f"m{n}")
    assert len(stub.dl_pool.started) == 1, "one Worker in the pool however long the queue"
    assert len(stub._job_signals) == 1 and len(stub._job_aborts) == 1
    assert len(stub._job_specs) == 4, "the waiting rows are specs, not jobs"
    assert stub._running_qid is not None


def test_jobs_run_in_queue_order_and_hand_over():
    class _InlinePool:
        def start(self, worker) -> None:
            worker.run()

    stub = _job_stub(pool=_InlinePool())
    order = []
    stub._set_queue_status = lambda qid, status: order.append((qid, status))
    with _plain_relay():
        for n in range(3):
            stub._download(_albumish(f"m{n}"), "track", f"m{n}", "{title}", False, f"m{n}")
    ran = [qid for qid, st in order if st == "running"]
    assert ran == sorted(ran) and len(ran) == 3, order
    assert stub._running_qid is None and not stub._job_specs


def test_cancelling_a_waiting_row_drops_its_spec_and_row():
    stub = _job_stub()
    with _plain_relay():
        for n in range(3):
            stub._download(_albumish(f"m{n}"), "track", f"m{n}", "{title}", False, f"m{n}")
    waiting = list(stub._job_specs)
    victim = waiting[0]
    stub.cancelQueueItem(victim)
    assert victim not in stub._job_specs
    assert stub._queue_item(victim) is None
    # Its turn comes and goes without a job.
    stub._running_qid = None
    with _plain_relay():
        stub._pump_queue()
    assert len(stub.dl_pool.started) == 2, "the cancelled row was skipped, the next one started"


def test_stop_clears_every_waiting_spec():
    stub = _job_stub()
    with _plain_relay():
        for n in range(4):
            stub._download(_albumish(f"m{n}"), "track", f"m{n}", "{title}", False, f"m{n}")
    stub.stopAll()
    assert not stub._job_specs and not stub._pending_qids
    assert all(it["status"] == "cancelled" for it in stub._queue)
    assert len(stub.dl_pool.started) == 1, "no new job may start off the back of a stop"


def test_a_paused_queue_starts_nothing_until_resume():
    stub = _job_stub()
    stub._paused = True
    with _plain_relay():
        stub._download(_albumish("m1"), "track", "m1", "{title}", False, "m1")
    assert stub.dl_pool.started == [], "paused: the row waits as a spec"
    with _plain_relay():
        stub.resumeQueue()
    assert len(stub.dl_pool.started) == 1
