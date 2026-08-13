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
    stub._job_aborts = {}
    for name in (
        "_enqueue",
        "_reindex_queue",
        "_queue_item",
        "removeQueueItem",
        "clearFinished",
        "clearFailed",
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
    for slot, gone in (
        ("clearFinished", {"done", "cancelled"}),  # the Completed section
        ("clearFailed", {"failed"}),
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


def test_retry_all_failed_retries_exactly_the_failed_rows():
    stub = _stub()
    stub.retryAllFailed = _bind(stub, "retryAllFailed")
    retried = []
    stub.retryQueueItem = retried.append
    qids = [stub._enqueue(n, "track", media_id=n) for n in ("A", "B", "C", "D")]
    stub._queue_item(qids[0])["status"] = "failed"
    stub._queue_item(qids[1])["status"] = "running"
    stub._queue_item(qids[2])["status"] = "failed"
    stub._queue_item(qids[3])["status"] = "cancelled"
    stub.retryAllFailed()
    assert retried == [qids[0], qids[2]]


def test_clear_queue_reindexes():
    stub = _stub()
    qids = [stub._enqueue(n, "track", media_id=n) for n in ("A", "B")]
    stub._queue_item(qids[0])["status"] = "running"
    stub.clearQueue()
    assert [it["qid"] for it in stub._queue] == [qids[0]]
    assert _mirror_ok(stub)
