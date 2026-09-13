"""The library scanner process: its protocol, and the app-side launcher.

The scan must never hold the app's interpreter (the launch water dropped
frames for every sweep, probed live 2026-09-01 and 2026-09-11), so it runs
in a child process (waves.library_worker) that the bridge talks to over
pipes (waves.waves_ui.library_proc). Pinned here:
  * the protocol, in-process over byte streams: a scan answers with a done
    event carrying the cache's verdicts, a probe with probe_done, a bad job
    with an error event that does not end the process, quit ends it, and
    only protocol ever reaches the real stdout;
  * the launcher against the REAL child: a scan of a temp library round
    trips, progress events reach the handler, a superseded job kills the
    child and answers None, a command that cannot start disables the worker
    once (the bridge then scans in-process), and a child that dies mid-job
    hands the job back;
  * the frozen app re-executes its own binary with the flag.
"""

from __future__ import annotations

import io
import json
import os
import sys

import pytest

from waves import library_worker
from waves.library_index import SCAN_OK
from waves.waves_ui import library_proc
from waves.waves_ui.library_proc import LibraryWorker, WorkerFailed

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# One under the retire threshold, so a single success has to clear it.
_MAX_CRASHES_FOR_TEST = library_proc._MAX_CRASHES - 1


def _lines(stdout: io.BytesIO) -> list[dict]:
    return [json.loads(line) for line in stdout.getvalue().splitlines() if line.strip()]


def _jobs(*jobs) -> io.BytesIO:
    return io.BytesIO("".join(json.dumps(j) + "\n" for j in jobs).encode("utf-8"))


def test_protocol_scan_probe_error_and_quit(tmp_path):
    root = tmp_path / "music"
    (root / "Artist" / "Album").mkdir(parents=True)
    cache = str(tmp_path / "cache.sqlite3")
    stdin = _jobs(
        {"op": "scan", "id": 1, "cache": cache, "root": str(root), "force_full": True, "recover": False},
        {
            "op": "probe",
            "id": 2,
            "cache": cache,
            "root": str(root),
            "names": ["Artist"],
            "spellings": {"Artist": ["Artist"]},
        },
        {"op": "dance", "id": 3},
        "not a job",
        {"op": "quit"},
        {"op": "scan", "id": 9, "cache": cache, "root": str(root)},
    )
    stdout = io.BytesIO()
    assert library_worker.serve(stdin, stdout) == 0
    events = _lines(stdout)
    assert events[0] == {"ev": "ready"}
    done = [e for e in events if e["ev"] == "done"]
    assert len(done) == 1 and done[0]["id"] == 1
    assert done[0]["status"] == SCAN_OK and done[0]["count"] == 0 and done[0]["partial"] is False
    assert done[0]["shape"] == [0, 0] and done[0]["recovered"] == 0
    probe = [e for e in events if e["ev"] == "probe_done"]
    assert len(probe) == 1 and probe[0]["id"] == 2 and probe[0]["found"] in (0, None)
    errors = [e for e in events if e["ev"] == "error"]
    assert [e["id"] for e in errors] == [3, None], "a bad job and an unreadable line each answer with an error"
    assert not [e for e in events if e.get("id") == 9], "quit ends the loop; nothing after it runs"


def test_protocol_forwards_the_scanner_log(tmp_path):
    root = tmp_path / "music"
    root.mkdir()
    cache = str(tmp_path / "cache.sqlite3")
    stdout = io.BytesIO()
    library_worker.serve(_jobs({"op": "scan", "id": 1, "cache": cache, "root": str(root)}, {"op": "quit"}), stdout)
    logs = [e for e in _lines(stdout) if e["ev"] == "log"]
    assert logs and all(e["name"].startswith("waves") for e in logs)
    assert any("library scan" in e["msg"] for e in logs), [e["msg"] for e in logs]


def _real_command() -> list[str]:
    return [sys.executable, "-m", "waves.library_worker"]


def _worker(**kw) -> LibraryWorker:
    w = LibraryWorker(_real_command(), **kw)
    # The bridge runs from the checkout; the test may not.
    w._command = _real_command()
    return w


def test_launcher_round_trips_a_scan_with_progress(tmp_path, monkeypatch):
    monkeypatch.chdir(_ROOT)
    root = tmp_path / "music"
    (root / "A" / "B").mkdir(parents=True)
    w = _worker()
    seen = []
    try:
        out = w.run_scan(
            {"cache": str(tmp_path / "c.sqlite3"), "root": str(root), "force_full": True, "recover": False},
            alive=lambda: True,
            on_progress=seen.append,
        )
        assert out is not None and out["ev"] == "done" and out["status"] == SCAN_OK
        assert isinstance(out["count"], int)
        assert seen, "the scanner's progress sink must reach the handler"
        assert all(isinstance(e, dict) and "phase" in e for e in seen)
        again = w.run_probe(
            {"cache": str(tmp_path / "c.sqlite3"), "root": str(root), "names": ["A"], "spellings": {"A": ["A"]}},
            alive=lambda: True,
        )
        assert again is not None and again["ev"] == "probe_done"
        assert not w.disabled
    finally:
        w.close()
    assert w._proc is None


def test_launcher_kills_a_superseded_job_and_answers_none(tmp_path, monkeypatch):
    monkeypatch.chdir(_ROOT)
    root = tmp_path / "music"
    root.mkdir()
    w = _worker()
    try:
        out = w.run_scan(
            {"cache": str(tmp_path / "c.sqlite3"), "root": str(root)}, alive=lambda: False, on_progress=None
        )
        assert out is None
        assert w._proc is None, "a superseded job kills the child"
    finally:
        w.close()


def test_launcher_disables_itself_when_the_command_cannot_start(tmp_path):
    w = LibraryWorker([os.path.join(str(tmp_path), "no-such-binary"), "--library-worker"])
    with pytest.raises(WorkerFailed):
        w.run_scan({"cache": str(tmp_path / "c.sqlite3"), "root": str(tmp_path)}, alive=lambda: True, on_progress=None)
    assert w.disabled
    with pytest.raises(WorkerFailed):
        w.run_scan({"cache": str(tmp_path / "c.sqlite3"), "root": str(tmp_path)}, alive=lambda: True, on_progress=None)


def test_launcher_hands_back_a_job_when_the_child_dies(tmp_path):
    w = LibraryWorker([sys.executable, "-c", "import sys; sys.exit(3)"])
    with pytest.raises(WorkerFailed):
        w.run_scan({"cache": str(tmp_path / "c.sqlite3"), "root": str(tmp_path)}, alive=lambda: True, on_progress=None)
    assert not w.disabled, "one death is a retry, not a verdict"
    with pytest.raises(WorkerFailed):
        w.run_scan({"cache": str(tmp_path / "c.sqlite3"), "root": str(tmp_path)}, alive=lambda: True, on_progress=None)
    assert w.disabled, "two deaths and the session scans in-process"


def test_default_command_frozen_and_source(monkeypatch):
    from waves.waves_ui import updater

    monkeypatch.setattr(updater, "is_frozen", lambda: False)
    assert library_proc.default_command() == [sys.executable, "-m", "waves.library_worker"]
    monkeypatch.setattr(updater, "is_frozen", lambda: True)
    monkeypatch.setattr(updater, "_current_exe", lambda: "/Applications/Waves.app/Contents/MacOS/Waves")
    assert library_proc.default_command() == ["/Applications/Waves.app/Contents/MacOS/Waves", "--library-worker"]


def test_a_job_stops_when_the_parent_goes_away(tmp_path, monkeypatch):
    # The parent died mid-scan (a crash, a force quit): the stdin EOF must
    # end the walk on its next continue check, never leave an orphan
    # scanning a share for minutes.
    import threading

    checks = []
    released = threading.Event()

    class _SlowLib:
        last_scan_status, last_scan_partial, last_listing_reconciled = SCAN_OK, False, False

        def __init__(self, path):
            pass

        def keys_missing(self):
            return False

        def refresh(self, root, should_continue, on_progress, force_full, root_is_local):
            released.set()
            while should_continue():
                checks.append(True)
                threading.Event().wait(0.01)
            return 0

        def untrusted_listing_shape(self):
            return (0, 0)

        def close(self):
            pass

    monkeypatch.setattr(library_worker, "LibraryIndex", _SlowLib)
    r, w = os.pipe()
    stdin = os.fdopen(r, "rb")
    out = io.BytesIO()
    served = threading.Thread(target=lambda: library_worker.serve(stdin, out))
    served.start()
    os.write(
        w,
        json.dumps(
            {"op": "scan", "id": 1, "cache": str(tmp_path / "c"), "root": str(tmp_path), "recover": False}
        ).encode()
        + b"\n",
    )
    assert released.wait(5.0)
    os.close(w)  # the parent is gone
    served.join(5.0)
    assert not served.is_alive()
    assert checks  # the walk was running, and stopped on the flag


def test_a_cancel_is_not_counted_against_the_scanner(tmp_path):
    """Changing the library folder cancels the running job, which kills the
    child, which reaches the waiting job as the same EOF a crash produces.
    Counted as a crash, two ordinary folder changes retired the scanner for
    the session and the log claimed it had died twice: every scan after that
    ran in the app's own interpreter, which is the stutter the child exists
    to remove.

    A child that never answers, so the cancel is what ends the job rather
    than a race with a scan that finished first."""
    import threading

    for _ in range(3):
        w = LibraryWorker([sys.executable, "-c", "import sys; sys.stdin.read()"])
        timer = threading.Timer(0.3, w.cancel)
        timer.start()
        try:
            out = w.run_scan(
                {"cache": str(tmp_path / "c.sqlite3"), "root": str(tmp_path)},
                alive=lambda: True,
                on_progress=None,
            )
        finally:
            timer.cancel()
        assert out is None, "a cancelled job answers None, the way a superseded one does"
        assert not w.disabled, "a cancel is not a crash"
        assert w._crashes == 0


def test_a_completed_job_clears_the_crash_count(tmp_path, monkeypatch):
    """The count is a run of consecutive failures, not a lifetime tally: a
    scanner that died once and has worked ever since must not retire on an
    unrelated death hours later."""
    monkeypatch.chdir(_ROOT)
    root = tmp_path / "music"
    root.mkdir()
    w = _worker()
    try:
        w._crashes = _MAX_CRASHES_FOR_TEST
        out = w.run_scan(
            {"cache": str(tmp_path / "c.sqlite3"), "root": str(root), "force_full": True, "recover": False},
            alive=lambda: True,
            on_progress=None,
        )
        assert out is not None and out["ev"] == "done"
        assert w._crashes == 0
    finally:
        w.close()


def test_the_scan_gauges_report_the_work_the_child_did(tmp_path, monkeypatch):
    """The walk and the tag reads moved into the child, so the gauge objects
    they drive are the CHILD's and this process's stayed at zero: a verbose
    report showed the two busiest pools in the app as idle through every
    scan. The child sends its readings with the progress it already reports
    and they are mirrored here."""
    from waves.library_index import READ_GAUGE, WALK_GAUGE

    monkeypatch.chdir(_ROOT)
    root = tmp_path / "music"
    for i in range(12):
        (root / f"Artist{i}" / "Album").mkdir(parents=True)
    WALK_GAUGE.peak = 0
    w = _worker()
    try:
        out = w.run_scan(
            {"cache": str(tmp_path / "c.sqlite3"), "root": str(root), "force_full": True, "recover": False},
            alive=lambda: True,
            on_progress=None,
        )
        assert out is not None and out["ev"] == "done"
    finally:
        w.close()
    assert WALK_GAUGE.peak > 0, "the walk the child did must show up on this process's gauge"
    assert WALK_GAUGE.maxThreadCount() >= 1
    assert READ_GAUGE.activeThreadCount() >= 0


def test_a_mirrored_reading_is_ignored_when_it_is_not_three_integers():
    """The readings cross a pipe, so they are validated like any other
    message rather than trusted into the gauges."""
    from waves.library_index import WALK_GAUGE

    before = (WALK_GAUGE.activeThreadCount(), WALK_GAUGE.maxThreadCount(), WALK_GAUGE.peak)
    for junk in (None, "walk", {"walk": "busy"}, {"walk": [1, 2]}, {"walk": ["a", "b", "c"]}):
        LibraryWorker._mirror_gauges(junk)
    assert (WALK_GAUGE.activeThreadCount(), WALK_GAUGE.maxThreadCount(), WALK_GAUGE.peak) == before


def test_the_scan_progress_runs_on_the_thread_that_asked_for_it(tmp_path, monkeypatch):
    """Not on the pipe reader. The handler republishes both presence indexes
    on a committed flush, and running that on the reader meant the pipe went
    undrained for its whole duration: the child blocked on its next write and
    the scan waited for the app, which is the coupling the child exists to
    break. The thread that asked for the scan is parked on the reply queue
    with nothing else to do, so the work belongs to it."""
    import threading

    monkeypatch.chdir(_ROOT)
    root = tmp_path / "music"
    (root / "A" / "B").mkdir(parents=True)
    w = _worker()
    threads: set[int] = set()
    try:
        out = w.run_scan(
            {"cache": str(tmp_path / "c.sqlite3"), "root": str(root), "force_full": True, "recover": False},
            alive=lambda: True,
            on_progress=lambda _e: threads.add(threading.get_ident()),
        )
        assert out is not None and out["ev"] == "done"
        assert threads, "the scanner's progress sink must reach the handler"
        assert threads == {threading.get_ident()}, "the progress handler ran off the caller's thread"
    finally:
        w.close()


def test_a_quit_while_a_scan_runs_does_not_make_the_app_wait(tmp_path, monkeypatch):
    """shutdown() runs on the GUI thread. The child serves one job at a time
    on one thread, so a quit line sent while a scan is running is not read
    until the scan ends and the polite wait cost the whole timeout with the
    window still on screen. A busy child is killed, which is what cancel
    already does and what the protocol is built for.

    A child that never answers, so the job really is in flight."""
    import threading
    import time

    w = LibraryWorker([sys.executable, "-c", "import sys; sys.stdin.read()"])
    job = threading.Thread(
        target=w.run_scan,
        args=({"cache": str(tmp_path / "c.sqlite3"), "root": str(tmp_path)},),
        kwargs={"alive": lambda: True, "on_progress": None},
        daemon=True,
    )
    job.start()
    deadline = time.monotonic() + 10
    while w._current_id is None and time.monotonic() < deadline:
        time.sleep(0.01)
    assert w._current_id is not None, "the job never reached the child"

    t0 = time.monotonic()
    w.close()
    waited = time.monotonic() - t0
    job.join(10)

    assert waited < library_proc._QUIT_WAIT_S / 2, f"the quit waited on a busy child ({waited:.2f}s)"
    assert w._proc is None


def test_an_idle_child_is_still_asked_to_quit_rather_than_killed(tmp_path, monkeypatch):
    """The other half of the rule above: killing unconditionally would throw
    away the child's own orderly close (it shuts its cache down on the way
    out), so an idle child still leaves by the protocol."""
    monkeypatch.chdir(_ROOT)
    root = tmp_path / "music"
    root.mkdir()
    w = _worker()
    w.run_scan(
        {"cache": str(tmp_path / "c.sqlite3"), "root": str(root), "force_full": True, "recover": False},
        alive=lambda: True,
        on_progress=None,
    )
    child = w._proc
    assert child is not None
    w.close()
    assert child.returncode == 0, "an idle child was killed instead of asked"
