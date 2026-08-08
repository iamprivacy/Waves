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
    for name in ("_enqueue", "_reindex_queue", "_queue_item", "removeQueueItem", "clearFinished", "clearQueue"):
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


def test_clear_finished_reindexes():
    stub = _stub()
    qids = [stub._enqueue(n, "track", media_id=n) for n in ("A", "B", "C")]
    stub._queue_item(qids[0])["status"] = "done"
    stub._queue_item(qids[2])["status"] = "failed"
    stub.clearFinished()
    assert [it["qid"] for it in stub._queue] == [qids[1]]
    assert stub._queue_item(qids[0]) is None and stub._queue_item(qids[2]) is None
    assert _mirror_ok(stub)


def test_clear_queue_reindexes():
    stub = _stub()
    qids = [stub._enqueue(n, "track", media_id=n) for n in ("A", "B")]
    stub._queue_item(qids[0])["status"] = "running"
    stub.clearQueue()
    assert [it["qid"] for it in stub._queue] == [qids[0]]
    assert _mirror_ok(stub)
