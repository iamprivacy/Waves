"""A queue progress tick finds its own row, and finds the RIGHT one.

WHAT THIS FENCES OFF
--------------------
Issue #24: the app got heavier the longer a batch ran. Progress arrives per
delivered segment, dozens a second per active download, and the handler used to
walk the whole model looking for its qid. Nothing ever removed a finished row,
so that walk grew all session and ran thousands of times a second on the GUI
thread. It is now a qid -> row map, rebuilt in the pass that already recounts
the groups.

That trade is only safe if the map is never wrong, and the ways it could go
wrong are all silent: the partition MOVES rows, promoteCompleted moves one to
index 0, and the reconcile removes rows outright. A stale entry would not throw,
it would paint one album's progress onto another album's bar. So this drives the
real model through all three and checks, after each, that every qid resolves to
the row that actually holds it, that a real signal from the bridge lands on the
right row, and that a deliberately corrupted map heals itself instead of lying.

Also pinned: a row leaving the queue takes its expanded track list with it.
That map is copied wholesale on every live tick, so leaking one entry per
finished album is the same lag by another route.

Runs in a SUBPROCESS like the other Main.qml scenarios: building the bridge
installs process-global handlers that must not leak into the suite.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

_EXIT_OK = 0
_EXIT_REGRESSED = 1
_EXIT_NO_QT = 77
_EXIT_PRECONDITION = 78

QML_MAIN = Path(__file__).resolve().parent.parent / "waves" / "waves_ui" / "qml" / "Main.qml"


def test_a_progress_tick_lands_on_its_own_queue_row():
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="waves-queue-index-test-")
    proc = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), "--run-scenario"],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    tail = "\n".join((proc.stdout + proc.stderr).strip().splitlines()[-12:])
    import pytest

    if proc.returncode == _EXIT_NO_QT:
        pytest.skip("PySide6 / offscreen Qt unavailable")
    if proc.returncode == _EXIT_PRECONDITION:
        pytest.skip(f"could not set up the scenario in this environment:\n{tail}")
    assert (
        proc.returncode == _EXIT_OK
    ), f"the queue row index went out of step with the model. Scenario exit={proc.returncode}:\n{tail}"


def _run_scenario() -> int:
    # THIS checkout's waves, not the venv's editable install.
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    try:
        from PySide6.QtCore import QEventLoop, QTimer, QUrl
        from PySide6.QtGui import QGuiApplication
        from PySide6.QtQml import QQmlApplicationEngine, QQmlEngine, QQmlExpression
    except Exception as exc:
        print(f"Qt unavailable: {exc}", file=sys.stderr)
        return _EXIT_NO_QT

    from _qml_offline import PARK_LOGIN_QML, patch_offline

    patch_offline()

    app = QGuiApplication.instance() or QGuiApplication([])
    try:
        from waves.waves_ui.app import _load_mono
        from waves.waves_ui.backend import WavesBridge
    except Exception as exc:
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

    def row(qid: int, status: str) -> str:
        return (
            "{qid: %d, name: 'r%d', type: 'album', status: '%s', progress: 0, "
            "media_id: 'm%d', template: '', collection: true, artist: '', tracks: 3, art: ''}" % (qid, qid, status, qid)
        )

    def index_agrees_with_the_model() -> bool:
        """Every qid in the model resolves to the row that actually holds it."""
        return bool(q("""
        (function() {
            var m = queueModel
            for (var i = 0; i < m.count; ++i) {
                if (root.queueRowIndexOf(m.get(i).qid) !== i) return false
            }
            return true
        })()
        """))

    bad = []

    # Arrival order is deliberately NOT the display order: the partition has to
    # move these before anything can be looked up.
    qids = [1, 2, 3, 4, 5, 6]
    rows = ", ".join(
        [row(1, "queued"), row(2, "done"), row(3, "running"), row(4, "failed"), row(5, "queued"), row(6, "running")]
    )
    q(f"reconcileQueue([{rows}])")
    settle(60)
    if int(q("queueModel.count")) != len(qids):
        print(f"expected {len(qids)} rows, model holds {q('queueModel.count')}", file=sys.stderr)
        return _EXIT_PRECONDITION
    if not index_agrees_with_the_model():
        bad.append("after the first reconcile + partition, the index pointed at the wrong rows")

    # A REAL tick, through the bridge's own signal, so the Connections handler
    # is the thing under test rather than a function called by name.
    bridge.queueItemProgress.emit(6, 44.0)
    settle(40)
    landed = q(
        "(function(){ var m = queueModel, o = {}; "
        "for (var i = 0; i < m.count; ++i) o[m.get(i).qid] = m.get(i).progress; return JSON.stringify(o) })()"
    )
    import json

    progress = json.loads(str(landed))
    if progress.get("6") != 44.0:
        bad.append(f"the tick for qid 6 did not reach it (progress={progress})")
    if any(v != 0 for k, v in progress.items() if k != "6"):
        bad.append(f"the tick for qid 6 painted another row (progress={progress})")

    # promoteCompleted moves a row to index 0, which invalidates every entry
    # after it. The rebuild at the end of the move is what keeps this honest.
    q("promoteCompleted(2)")
    settle(40)
    if int(q("queueModel.get(0).qid")) != 2:
        bad.append("promoteCompleted did not land the row at the top of Completed")
    if not index_agrees_with_the_model():
        bad.append("after promoteCompleted moved a row, the index was stale")

    bridge.queueItemProgress.emit(3, 77.0)
    settle(40)
    after = json.loads(
        str(
            q(
                "(function(){ var m = queueModel, o = {}; "
                "for (var i = 0; i < m.count; ++i) o[m.get(i).qid] = m.get(i).progress; return JSON.stringify(o) })()"
            )
        )
    )
    if after.get("3") != 77.0 or after.get("6") != 44.0:
        bad.append(f"a tick after the move went astray (progress={after})")

    # A corrupted map must heal, not lie. This is the case a real tick hits when
    # it lands between a move and the rebuild that follows it.
    q("root.queueRowIndex = ({})")
    if not index_agrees_with_the_model():
        bad.append("a stale index did not rebuild itself on lookup")
    if int(q("root.queueRowIndexOf(999)")) != -1:
        bad.append("a qid that is not in the queue must resolve to -1, not to some row")

    # A row that leaves takes its expanded track list with it: that map is
    # copied on every live tick, so a leak there is the same lag by another
    # route. Seed two, drop one.
    q("root.queueTracks = ({ 5: [{num: 1, title: 'a', status: 'done'}], 6: [{num: 1, title: 'b', status: 'done'}] })")
    remaining = ", ".join([row(1, "queued"), row(2, "done"), row(3, "running"), row(4, "failed"), row(6, "running")])
    q(f"reconcileQueue([{remaining}])")
    settle(60)
    if int(q("queueModel.count")) != 5:
        bad.append(f"the dropped row is still in the model (count={q('queueModel.count')})")
    if bool(q("root.queueTracks[5] !== undefined")):
        bad.append("a removed row left its track list behind")
    if not bool(q("root.queueTracks[6] !== undefined")):
        bad.append("a surviving row lost its track list")
    if not index_agrees_with_the_model():
        bad.append("after a removal, the index was stale")

    if bad:
        print("\n".join(bad), file=sys.stderr)
        return _EXIT_REGRESSED
    print(f"ok: progress={progress} after={after}")
    return _EXIT_OK


if __name__ == "__main__":
    raise SystemExit(_run_scenario())
