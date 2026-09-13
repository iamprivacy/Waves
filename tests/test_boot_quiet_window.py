"""Nothing new may run on the interpreter during the launch water.

WHAT THIS FENCES OFF
--------------------
The launch water is a QML Video in the same scene graph as the interface,
so every one of its frames needs the GUI thread, and the GUI thread waits
for the interpreter lock whenever any Python runs anywhere. A pool job
dispatched between the bridge's construction and the reveal therefore
competes with the picture for every frame it holds the lock. The library
walk did exactly that (now a child process, waves.library_worker); a
presence-index build did (now the sqlite cache itself); and every fix
before this one was undone by the next feature that added a boot job.

HOW THIS STAYS FIXED
--------------------
The scenario below builds the real bridge in a sandbox with every pool
start and thread start recorded, drives it to the reveal, and compares the
jobs that ran against the allowlist here. A new boot job fails the test
until it is added below WITH the reason it may hold the interpreter under
the water (short, or off the interpreter, or measured harmless with the
native render-loop log: see tools/launch_probe.py). Runs in a SUBPROCESS
like the other boot scenarios: building the bridge installs process-global
handlers that must not leak into the rest of the suite.
"""

from __future__ import annotations

import contextlib
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

_EXIT_OK = 0
_EXIT_NO_QT = 77
_EXIT_PRECONDITION = 78
_MARK = "BOOT-JOBS:"
# The launch's last dispatch, and the one this whole guard is about. The window
# is closed on it rather than on a stopwatch: a fixed wait long enough on this
# machine is a FALSE PASS on a slower one, where a job dispatched after the
# clock ran out is simply never seen.
_SWEEP = "LibraryMixin._rebuild_library_index.<locals>.work"
# How long the sweep has to appear, and how long the sample runs on past it to
# catch whatever is dispatched alongside it.
_SWEEP_WAIT_MS = 15_000
_TRAILING_MS = 400

# Every job the launch may dispatch before the reveal, by the qualified name
# of the callable handed to the pool (or the thread's target), with why.
ALLOWED_BOOT_JOBS = {
    # The badge seed: one sqlite read of the last scan, so the cards carry
    # their library verdict in the payload from the first page.
    "LibraryMixin._rebuild_library_index.<locals>.seed",
    "LibraryMixin._seed_library_badges.<locals>.<lambda>",
    # The scan job: the walk runs in the scanner PROCESS; this pool thread
    # only relays its events and publishes the finished index.
    "LibraryMixin._rebuild_library_index.<locals>.work",
    # The scanner process's stdout reader: blocked on a pipe read.
    "LibraryWorker._read",
    # The scanner process's stderr reader: blocked on a pipe read, and on a
    # healthy child nothing ever arrives on it. The child's own log records
    # leave by the protocol on stdout and are stopped from propagating to its
    # stderr handler, so this stream carries only what the protocol cannot:
    # a traceback, an import failure, another library's warning. It wakes on
    # those, which is the point of having it, and they mean the scan is
    # already in trouble.
    "LibraryWorker._read_stderr",
    # The diagnostics log queue drain: asleep until a record is queued.
    "QueueListener._monitor",
    # The saved-session login: one HTTPS round trip.
    "WavesBridge._try_token_login.<locals>.work",
    # The settings writer: asleep until a save is queued.
    "_SingleFlightWriter._run",
}


def test_only_allowlisted_jobs_run_under_the_launch_water():
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="waves-boot-quiet-test-")
    proc = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), "--run-scenario"],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    out = proc.stdout + proc.stderr
    tail = "\n".join(out.strip().splitlines()[-8:])
    if proc.returncode == _EXIT_NO_QT:
        pytest.skip("PySide6 / offscreen Qt unavailable")
    if proc.returncode == _EXIT_PRECONDITION:
        pytest.skip(f"could not set up the scenario in this environment:\n{tail}")
    assert proc.returncode == _EXIT_OK, f"scenario failed, exit={proc.returncode}:\n{tail}"
    lines = [ln[len(_MARK) :].strip() for ln in out.splitlines() if ln.startswith(_MARK)]
    assert lines, f"the scenario reported no job list:\n{tail}"
    ran = set(filter(None, lines[-1].split(",")))
    assert ran, "no job ran before the reveal, which means the launch no longer masks the load at all"
    # The deleted test_boot_library_scan_deferral.py used to be what pinned
    # that the library sweep is dispatched AT ALL. Without this line, deleting
    # the sweep would turn this guard green: a launch that runs nothing is a
    # very quiet window and a missing feature.
    assert _SWEEP in ran, (
        f"the library sweep was never dispatched at launch ({sorted(ran)}). This guard measures what the "
        "launch runs under the water, so a launch that stopped running the scan passes it for the wrong reason."
    )
    new = ran - ALLOWED_BOOT_JOBS
    assert not new, (
        "a job now runs on the interpreter during the launch water. Either move it after "
        "bootRevealed, take it off the interpreter, or measure it with tools/launch_probe.py "
        f"and add it to ALLOWED_BOOT_JOBS with the reason: {sorted(new)}"
    )


def _qualname(fn) -> str:
    fn = getattr(fn, "__func__", fn)
    return getattr(fn, "__qualname__", None) or type(fn).__qualname__


def _run_scenario() -> int:
    # THIS checkout's waves, not the venv's editable install.
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    import threading

    try:
        from PySide6.QtCore import QEventLoop, QThreadPool, QTimer
        from PySide6.QtGui import QGuiApplication
    except Exception as exc:
        print(f"Qt unavailable: {exc}", file=sys.stderr)
        return _EXIT_NO_QT

    seen: list[str] = []
    lock = threading.Lock()
    real_pool_start = QThreadPool.start
    real_thread_start = threading.Thread.start

    def pool_start(self, runnable, *a, **k):
        fn = getattr(runnable, "fn", runnable)
        with lock:
            seen.append(_qualname(fn))
        return real_pool_start(self, runnable, *a, **k)

    def thread_start(self):
        target = getattr(self, "_target", None)
        with lock:
            seen.append(_qualname(target) if target is not None else type(self).__qualname__)
        return real_thread_start(self)

    QThreadPool.start = pool_start
    threading.Thread.start = thread_start

    app = QGuiApplication.instance() or QGuiApplication([])
    try:
        from waves.waves_ui.backend import WavesBridge
    except Exception as exc:
        print(f"backend unavailable: {exc}", file=sys.stderr)
        return _EXIT_NO_QT

    # A library folder with one album, switched on in the sandbox's prefs, so
    # the launch takes its library path (badge seed, scanner process).
    import json

    from waves.helper.path import path_config_base

    cfg = path_config_base()
    os.makedirs(cfg, exist_ok=True)
    lib = os.path.join(os.path.dirname(cfg), "library")
    album = os.path.join(lib, "Lorna Shore", "Pain Remains")
    os.makedirs(album, exist_ok=True)
    Path(album, "01.flac").write_bytes(b"")
    with open(os.path.join(cfg, "waves.json"), "w", encoding="utf-8") as fh:
        json.dump({"library_enabled": True, "library_source": "separate", "library_folder": lib}, fh)

    bridge = WavesBridge(tidal=None)
    loop = QEventLoop()
    # What a real launch does between construction and the reveal: the
    # landing page is asked for, the water plays, the interface warms.
    try:
        bridge.loadHome()
    except Exception as exc:
        print(f"loadHome unavailable: {exc}", file=sys.stderr)
        return _EXIT_PRECONDITION
    # Closed on the sweep, not on a stopwatch (see _SWEEP above). The cap is
    # only there so a launch that never dispatches it still ends; the assertion
    # on the far side is what calls that a failure.
    waited = 0

    def _settle(ms: int) -> None:
        QTimer.singleShot(ms, loop.quit)
        loop.exec()

    while waited < _SWEEP_WAIT_MS:
        with lock:
            if _SWEEP in seen:
                break
        _settle(50)
        waited += 50
    _settle(_TRAILING_MS)
    with lock:
        ran = sorted(set(seen))
    bridge.bootRevealed()
    QTimer.singleShot(200, loop.quit)
    loop.exec()
    print(_MARK + ",".join(ran))
    with contextlib.suppress(Exception):
        bridge.shutdown()
    app.quit()
    return _EXIT_OK


if __name__ == "__main__" and "--run-scenario" in sys.argv:
    raise SystemExit(_run_scenario())
