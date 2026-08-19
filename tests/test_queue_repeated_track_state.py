"""A track that appears twice in a collection follows its state on both rows.

WHAT THIS FENCES OFF
--------------------
The queue ledger renders the collection's running order, and a playlist or a
mix can list the same track more than once. The engine reports that track ONCE
per event (its per-job registry is keyed by track id), so the drawer has to
apply each state event to every ledger row carrying the id.

The state handler used to stop at the first match, while its sibling that
carries the percentage did not: the second copy of a repeated track moved its
bar but never left QUEUED, and after the job finished it still read as if it
had never been fetched. Harmless while the ledger was album-only (an album
never lists an id twice); opening the ledger to playlists and mixes made it
reachable.

Proved on the real Main.qml (subprocess, like its siblings): a playlist whose
running order repeats an id, one state event for that id, and both rows are
expected to carry the new status and quality while the untouched neighbour is
left alone.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

QML_MAIN = Path(__file__).resolve().parent.parent / "tidaler" / "waves_ui" / "qml" / "Main.qml"

_EXIT_OK = 0
_EXIT_REGRESSED = 1
_EXIT_NO_QT = 77
_EXIT_PRECONDITION = 78


def test_a_repeated_track_moves_on_every_row_it_occupies():
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    sandbox = tempfile.mkdtemp(prefix="waves-queue-repeated-track-")
    env["XDG_CONFIG_HOME"] = sandbox
    env["HOME"] = sandbox
    proc = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), "--run-scenario"],
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    tail = "\n".join((proc.stdout + proc.stderr).strip().splitlines()[-12:])
    if proc.returncode == _EXIT_NO_QT:
        pytest.skip("PySide6 / offscreen Qt unavailable")
    if proc.returncode == _EXIT_PRECONDITION:
        pytest.skip(f"could not set up the scenario in this environment:\n{tail}")
    assert proc.returncode == _EXIT_OK, f"a repeated track's second row was left behind:\n{tail}"


def _run_scenario() -> int:  # (one straight line of scene setup)
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    try:
        from PySide6.QtCore import QEventLoop, QTimer, QUrl
        from PySide6.QtGui import QGuiApplication
        from PySide6.QtQml import QQmlApplicationEngine, QQmlEngine, QQmlExpression
    except Exception as exc:  # pragma: no cover - environment guard
        print(f"Qt unavailable: {exc}", file=sys.stderr)
        return _EXIT_NO_QT

    from _qml_offline import PARK_LOGIN_QML, patch_offline

    patch_offline()
    app = QGuiApplication.instance() or QGuiApplication([])
    try:
        from tidaler.waves_ui.app import _load_mono
        from tidaler.waves_ui.backend import WavesBridge
    except Exception as exc:  # pragma: no cover - environment guard
        print(f"Qt platform/backend unavailable: {exc}", file=sys.stderr)
        return _EXIT_NO_QT

    engine = QQmlApplicationEngine()
    bridge = WavesBridge(tidal=None)
    engine.rootContext().setContextProperty("waves", bridge)
    engine.rootContext().setContextProperty("monoFont", _load_mono())
    engine.rootContext().setContextProperty("uiFontFamily", app.font().family())
    engine.load(QUrl.fromLocalFile(str(QML_MAIN)))
    roots = engine.rootObjects()
    if not roots:
        print("Main.qml failed to load", file=sys.stderr)
        return _EXIT_PRECONDITION
    root = roots[0]

    def q(expr: str):
        e = QQmlExpression(QQmlEngine.contextForObject(root), root, expr)
        r = e.evaluate()
        if e.hasError():
            raise RuntimeError(e.error().toString())
        return r[0] if isinstance(r, tuple) else r

    def settle(ms: int) -> None:
        loop = QEventLoop()
        QTimer.singleShot(ms, loop.quit)
        loop.exec()

    settle(120)
    q(PARK_LOGIN_QML)
    q("queueDrawer.open()")
    settle(120)
    if not bool(q("queueDrawer.visible")):
        print("the queue drawer would not open", file=sys.stderr)
        return _EXIT_PRECONDITION

    # A playlist whose running order lists track 7 twice, around a track 8.
    qid = bridge._enqueue("Repeats", "playlist", media_id="p-1", collection=True, tracks=3)
    bridge.queueChanged.emit(list(bridge._queue))
    settle(150)
    bridge._merge_queue_tracks(
        qid,
        [
            {"id": "7", "num": 1, "title": "same song", "duration": "3:00"},
            {"id": "8", "num": 2, "title": "other song", "duration": "3:00"},
            {"id": "7", "num": 3, "title": "same song", "duration": "3:00"},
        ],
    )
    settle(150)

    def statuses() -> str:
        return str(
            q(
                f"(function(){{ var a = root.queueTracks[{qid}] || []; "
                "return a.map(function(t){ return t.id + ':' + t.status + ':' + (t.quality || '-'); }).join(' | '); })()"
            )
        )

    bad: list[str] = []
    got = statuses()
    if got != "7:pending:- | 8:pending:- | 7:pending:-":
        print(f"the ledger did not seed as expected: {got!r}", file=sys.stderr)
        return _EXIT_PRECONDITION

    # One state event for the repeated id, as the engine sends it (the
    # registry is keyed by id, so there is exactly one).
    bridge._track_lifecycle(qid, {"id": "7", "title": "same song", "status": "running"})
    settle(200)
    got = statuses()
    if got != "7:running:- | 8:pending:- | 7:running:-":
        bad.append(f"after track 7 started, the ledger read {got!r}; both of its rows must be running")

    bridge._track_lifecycle(qid, {"id": "7", "status": "done", "quality": {"tier": "LOSSLESS"}})
    settle(200)
    got = statuses()
    if got != "7:done:LOSSLESS | 8:pending:- | 7:done:LOSSLESS":
        bad.append(f"after track 7 landed, the ledger read {got!r}; both of its rows must be done at LOSSLESS")

    if bad:
        for b in bad:
            print(b, file=sys.stderr)
        return _EXIT_REGRESSED
    return _EXIT_OK


if __name__ == "__main__":
    if "--run-scenario" in sys.argv:
        raise SystemExit(_run_scenario())
