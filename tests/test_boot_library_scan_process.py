"""The launch library sweep runs in the scanner process, from construction.

It used to be deferred until the boot reveal because its walk held this
process's interpreter and the boot water dropped frames for it. The walk
now runs in a child process (waves.library_worker) that shares nothing with
the GUI thread, so the sweep starts at construction again, which is what
the launch sequence exists to mask. Pinned: the job goes to the worker with
the cache and root, the outcome lands on the index object, a worker that
cannot be used hands the job back to the in-process scan, and the reveal no
longer carries a release.
"""

from __future__ import annotations

from types import SimpleNamespace

from waves.waves_ui import backend as backend_mod
from waves.waves_ui.bridge_library import _IN_PROCESS, LibraryMixin
from waves.waves_ui.library_proc import LibraryWorker, WorkerFailed


class _Lib:
    def __init__(self, path="/tmp/cache.sqlite3"):
        self.path = path
        self.adopted = None
        self.last_scan_status = ""

    def adopt_scan_outcome(self, status, *, partial, shape, reconciled):
        self.adopted = (status, partial, tuple(shape), reconciled)
        self.last_scan_status = status


class _Worker(LibraryWorker):
    """A LibraryWorker in name (the bridge trusts nothing else) that never
    spawns: it answers with a canned outcome."""

    def __init__(self, outcome=None, fail=False, disabled=False):
        # No super().__init__: nothing here may touch a process.
        self.outcome, self.fail, self._disabled, self.jobs = outcome, fail, disabled, []

    def run_scan(self, job, *, alive, on_progress):
        self.jobs.append(job)
        if self.fail:
            raise WorkerFailed("no")
        return self.outcome

    def run_probe(self, job, *, alive):
        self.jobs.append(job)
        if self.fail:
            raise WorkerFailed("no")
        return self.outcome


def _stub(worker):
    s = SimpleNamespace(
        _library_worker=worker,
        settings=SimpleNamespace(file_path="/tmp/cfg/waves.json"),
        _library_gen=1,
        _library_root_locality=lambda root: True,
        _library_probe_candidates=lambda n: [n, n.upper()],
    )
    s._library_worker_scan = LibraryMixin._library_worker_scan.__get__(s)
    s._library_worker_probe = LibraryMixin._library_worker_probe.__get__(s)
    return s


def test_the_scan_goes_to_the_worker_and_its_outcome_lands_on_the_index():
    w = _Worker({"ev": "done", "count": 7, "status": "ok", "partial": True, "shape": [3, 2], "reconciled": False})
    lib = _Lib()
    out = _stub(w)._library_worker_scan(lib, "/music", True, lambda: True, None)
    assert out["count"] == 7
    assert w.jobs[0]["cache"] == lib.path and w.jobs[0]["root"] == "/music" and w.jobs[0]["force_full"] is True
    assert w.jobs[0]["config_dir"] == "/tmp/cfg" and w.jobs[0]["recover"] is True
    assert lib.adopted == ("ok", True, (3, 2), False)


def test_an_unusable_worker_hands_the_scan_back():
    for w in (_Worker(fail=True), _Worker(disabled=True), None):
        assert _stub(w)._library_worker_scan(_Lib(), "/music", False, lambda: True, None) is None
    assert (
        _stub(_Worker({"ev": "done"}))._library_worker_scan(_Lib(":memory:"), "/music", False, lambda: True, None)
        is None
    )


def test_a_superseded_scan_answers_none_without_adopting():
    lib = _Lib()
    assert _stub(_Worker(None))._library_worker_scan(lib, "/music", False, lambda: False, None) is None
    assert lib.adopted is None


def test_the_probe_goes_to_the_worker_with_the_spellings():
    w = _Worker({"ev": "probe_done", "found": 2})
    s = _stub(w)
    assert s._library_worker_probe(_Lib(), "/music", ["Ab"], 1, 20.0) == 2
    assert w.jobs[0]["spellings"] == {"Ab": ["Ab", "AB"]}
    assert (
        _stub(_Worker(None))._library_worker_probe(_Lib(), "/music", ["Ab"], 1, 20.0) is None
    ), "busy or superseded: never asked"
    assert _stub(_Worker(fail=True))._library_worker_probe(_Lib(), "/music", ["Ab"], 1, 20.0) is _IN_PROCESS
    assert _stub(None)._library_worker_probe(_Lib(), "/music", ["Ab"], 1, 20.0) is _IN_PROCESS


def test_the_caller_decides_how_long_the_child_may_wait_for_the_cache():
    """The drainer asks with 0.0, meaning "do not wait for the cache at all":
    a name it cannot answer now is deferred and re-asked the moment the scan
    publishes. Pinned to the relist constant inside the child, that answer
    became a twenty-second block on the drain thread instead."""
    w = _Worker({"ev": "probe_done", "found": 0})
    _stub(w)._library_worker_probe(_Lib(), "/music", ["Ab"], 1, 0.0)
    assert w.jobs[0]["timeout"] == 0.0, "the child was told to wait anyway"


def test_the_reveal_no_longer_releases_a_deferred_sweep():
    assert not hasattr(LibraryMixin, "_start_boot_library_scan")
    assert not hasattr(backend_mod, "_BOOT_LIBRARY_SCAN_FAILSAFE_MS")


def test_the_real_index_hands_its_file_to_the_worker(tmp_path):
    # The scanner process opens the cache by path; a bare in-memory index
    # (or a path that is not a string, as a doubled decorator once made it)
    # must keep the job in-process rather than crash the scan.
    from waves.library_index import LibraryIndex

    lib = LibraryIndex(str(tmp_path / "cache.sqlite3"))
    try:
        assert lib.path == str(tmp_path / "cache.sqlite3")
        w = _Worker({"ev": "done", "count": 1, "status": "ok", "partial": False, "shape": [0, 0], "reconciled": False})
        out = _stub(w)._library_worker_scan(lib, "/music", False, lambda: True, None)
        assert out["count"] == 1
        assert w.jobs[0]["cache"] == lib.path
    finally:
        lib.close()
