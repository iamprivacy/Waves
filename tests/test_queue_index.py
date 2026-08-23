"""The qid index stays a faithful mirror of the queue list.

_queue_item() sits on the per-tick progress path (_report_pct), and a
discography with videos can hold ~2000 rows, so it reads a qid -> row dict
instead of scanning the list. The dict is only correct if every mutation of
self._queue keeps it in step: appends add, wholesale rebuilds reindex. These
tests bind the real queue methods over a minimal stub and check the mirror
after each kind of mutation.
"""

from __future__ import annotations

from threading import Lock

from tidaler.waves_ui.backend import WavesBridge


class _Stub:
    """Bare object the real methods get bound onto."""


def _bind(stub, name):
    return getattr(WavesBridge, name).__get__(stub, type(stub))


def _stub():
    stub = _Stub()
    stub._queue = []
    stub._queue_index = {}
    stub._queue_seq = 0
    stub._queue_lock = Lock()
    stub._queue_emit_suspended = False
    stub._emit_queue = lambda: None
    # The delta protocol's dirty marks (what QML has not been told yet) and
    # the per-row job state the remove path prunes.
    stub._qdirty_added = []
    stub._qdirty_changed = {}
    stub._qdirty_removed = []
    stub._qdirty_full = False
    stub._job_specs = {}
    stub._job_objs = {}
    stub._job_tracks = {}
    stub._job_owned = {}
    stub._job_fetched = {}
    # A queued row states the tier its job will ask for; the setting behind
    # that word is not what these tests are about.
    stub._target_tier = lambda: "LOSSLESS"
    # Likewise the raw quality a row pins for its job (see
    # test_quality_pinned_per_job.py); these tests are about the index.
    stub._queued_quality_value = lambda: "LOSSLESS"
    # And likewise the library-skip gate a row pins for its job (see
    # test_queue_row_pins_the_library_skip.py).
    stub._library_bulk_skip_on = lambda: True
    stub._job_aborts = {}
    stub._QUEUE_SETTLED = WavesBridge._QUEUE_SETTLED
    stub._QUEUE_HISTORY_MAX = WavesBridge._QUEUE_HISTORY_MAX
    for name in (
        "_enqueue",
        "_reindex_queue",
        "_queue_item",
        "_trim_queue_history",
        "_remove_rows_where",
        "_remove_row",
        "removeQueueItem",
        "clearFinished",
        "clearFailed",
        "clearStopped",
        "clearQueued",
        "clearQueue",
    ):
        setattr(stub, name, _bind(stub, name))
    return stub


def _mirror_ok(stub) -> bool:
    return stub._queue_index == {it["qid"]: it for it in stub._queue}


def test_enqueue_indexes_the_exact_row_object():
    stub = _stub()
    qid = stub._enqueue("A", "track", media_id="m1")
    assert stub._queue_item(qid) is stub._queue[0]
    assert _mirror_ok(stub)


def test_remove_reindexes():
    stub = _stub()
    qids = [stub._enqueue(n, "track", media_id=n) for n in ("A", "B", "C")]
    stub.removeQueueItem(qids[1])
    assert stub._queue_item(qids[1]) is None
    assert [it["qid"] for it in stub._queue] == [qids[0], qids[2]]
    assert _mirror_ok(stub)


def test_each_section_clear_takes_only_its_own_section():
    # Every section clears itself and nothing else, and none of them ever
    # touches a running row: stopping a live transfer is the row's own control.
    # A cancelled row is one STOP ended; it files under its own Stopped
    # section, whose CLEAR is the only one that takes it (issue #27).
    for slot, gone in (
        ("clearFinished", {"done"}),  # the Completed section
        ("clearFailed", {"failed"}),
        ("clearStopped", {"cancelled"}),
        ("clearQueued", {"queued"}),
    ):
        stub = _stub()
        states = ("done", "cancelled", "failed", "queued", "running")
        qids = {st: stub._enqueue(st, "track", media_id=st) for st in states}
        for st, qid in qids.items():
            stub._queue_item(qid)["status"] = st
        getattr(stub, slot)()
        left = {it["status"] for it in stub._queue}
        assert left == set(states) - gone, f"{slot} cleared {set(states) - left}, expected {gone}"
        assert "running" in left, f"{slot} must never take a row that is downloading"
        assert _mirror_ok(stub)


def test_clear_all_empties_everything_except_the_running_row():
    stub = _stub()
    states = ("done", "cancelled", "failed", "queued", "running")
    qids = {st: stub._enqueue(st, "track", media_id=st) for st in states}
    for st, qid in qids.items():
        stub._queue_item(qid)["status"] = st
    stub.clearQueue()
    assert [it["status"] for it in stub._queue] == ["running"]
    assert _mirror_ok(stub)


def _retry_all_stub():
    stub = _stub()
    for name in ("retryAllFailed", "retryAllStopped", "_retry_all_with_status", "_row_object"):
        setattr(stub, name, _bind(stub, name))
    retried = []
    # RETRY ALL drops the rows in one pass and re-downloads each from the
    # object the row kept; recording _start_retry sees exactly that set.
    stub._start_retry = lambda item, obj: retried.append(item["qid"])
    stub._retry_queue_refetch = lambda item: retried.append(("refetch", item["qid"]))
    qids = [stub._enqueue(n, "track", media_id=n) for n in ("A", "B", "C", "D", "E", "F")]
    for qid, st in zip(qids, ("failed", "running", "failed", "cancelled", "done", "cancelled"), strict=False):
        stub._queue_item(qid)["status"] = st
        stub._job_objs[qid] = object()  # the row's kept live object
    return stub, qids, retried


def test_retry_all_failed_retries_exactly_the_failed_rows():
    # RETRY ALL on the Failed header takes the failed rows and nothing else:
    # not the rows STOP ended (they have their own header, issue #27), never a
    # live or finished row.
    stub, qids, retried = _retry_all_stub()
    stub.retryAllFailed()
    assert retried == [qids[0], qids[2]]


def test_retry_all_stopped_retries_exactly_the_stopped_rows_in_order():
    stub, qids, retried = _retry_all_stub()
    stub.retryAllStopped()
    assert retried == [qids[3], qids[5]]


def test_clear_queue_reindexes():
    stub = _stub()
    qids = [stub._enqueue(n, "track", media_id=n) for n in ("A", "B")]
    stub._queue_item(qids[0])["status"] = "running"
    stub.clearQueue()
    assert [it["qid"] for it in stub._queue] == [qids[0]]
    assert _mirror_ok(stub)


# --- Automatic history retention (issue #24) ----------------------------------
# Nothing ever removed a finished queue row, and every per-change cost is
# proportional to the list's length (a full marshal to QML, a row-by-row
# reconcile there, a per-track registry held per collection row). A long batch
# therefore got heavier the longer it ran. The finished half is now bounded
# automatically, with no switch to find and nothing asked of the user: manual
# clearing should not be the thing standing between them and a responsive app.


def _settled_queue(stub, live=0, done=0, failed=0):
    """A queue in arrival order: `live` still queued, then `done` finished,
    then `failed`. Returns the qids in that order."""
    order = []
    for i in range(live):
        order.append(stub._enqueue(f"live{i}", "album", media_id=f"L{i}"))
    for i in range(done):
        qid = stub._enqueue(f"done{i}", "album", media_id=f"D{i}")
        stub._queue_item(qid)["status"] = "done"
        order.append(qid)
    for i in range(failed):
        qid = stub._enqueue(f"fail{i}", "album", media_id=f"F{i}")
        stub._queue_item(qid)["status"] = "failed"
        order.append(qid)
    return order


def test_a_queue_under_the_cap_is_left_alone():
    stub = _stub()
    qids = _settled_queue(stub, done=stub._QUEUE_HISTORY_MAX)
    stub._trim_queue_history()
    assert [it["qid"] for it in stub._queue] == qids


def test_settled_rows_past_the_cap_go_oldest_first():
    stub = _stub()
    over = 12
    qids = _settled_queue(stub, done=stub._QUEUE_HISTORY_MAX + over)
    stub._trim_queue_history()
    assert len(stub._queue) == stub._QUEUE_HISTORY_MAX
    # The oldest are what went, and the newest are all still there in order.
    assert [it["qid"] for it in stub._queue] == qids[over:]
    assert _mirror_ok(stub)


def test_a_stopped_row_is_never_trimmed_either():
    # A cancelled row is one STOP ended and kept for RETRY (issue #27): the
    # same record a failed row is, so the cap passes over it the same way.
    stub = _stub()
    qids = _settled_queue(stub, done=stub._QUEUE_HISTORY_MAX + 1)
    stub._queue_item(qids[0])["status"] = "cancelled"
    stub._trim_queue_history()
    assert len(stub._queue) == stub._QUEUE_HISTORY_MAX
    assert stub._queue_item(qids[0]) is not None, "the stopped row went with the history"
    assert stub._queue_item(qids[1]) is None, "the oldest done row is what should have gone"


def test_failed_rows_are_never_trimmed():
    # A failed row is the only record that something still needs retrying, and
    # RETRY ALL is the whole point of the Failed section. Age is no argument.
    stub = _stub()
    qids = _settled_queue(stub, failed=stub._QUEUE_HISTORY_MAX + 40)
    stub._trim_queue_history()
    assert [it["qid"] for it in stub._queue] == qids


def test_live_work_is_never_trimmed_and_holds_the_queue_over_the_cap():
    # A discography queues hundreds of rows at once. They are all live work,
    # so the cap simply does not apply until they finish.
    stub = _stub()
    qids = _settled_queue(stub, live=stub._QUEUE_HISTORY_MAX + 50)
    stub._trim_queue_history()
    assert [it["qid"] for it in stub._queue] == qids


def test_the_trim_takes_settled_rows_out_from_among_live_ones():
    # Arrival order interleaves: finished albums sit between still-queued ones,
    # and only the finished ones may be taken, oldest first.
    stub = _stub()
    live = _settled_queue(stub, live=stub._QUEUE_HISTORY_MAX)
    done = _settled_queue(stub, done=10)
    more_live = _settled_queue(stub, live=5)
    stub._trim_queue_history()
    assert [it["qid"] for it in stub._queue] == live + more_live
    assert all(it["status"] == "queued" for it in stub._queue)
    assert done and _mirror_ok(stub)
