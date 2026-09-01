"""A delisted track reads UNAVAILABLE in the queue, and its album still finishes.

WHAT THIS FENCES OFF
--------------------
TIDAL delists tracks. The engine skips them (``allow_streaming`` false) and says
so in the log, but it returns the same empty-handed ``(False, "")`` a broken
download returns, and the GUI used to tally that as a failure. A commentary
edition whose every track TIDAL had withdrawn came back as
``RuntimeError: 15 of 15 tracks failed``: the album red, a RETRY button that
could never succeed, and nothing on screen saying who had refused (issue #25).

The word is the whole point of the fix, so it is asserted on the REAL delegate:
the bridge's own _track_lifecycle feeds the per-track registry, the expansion
renders it, and the outcome column has to read UNAVAILABLE, distinct from both
FAILED and the green COMPLETED beside it. Reading it as FAILED would put a retry
in front of the user for a file that no longer exists anywhere.

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

# The reported album's shape once the fix is in: some tracks landed, one was
# already owned, one genuinely broke, and three are gone from TIDAL.
LEDGER = [
    ("1", "Truth Without Love", "done"),
    ("2", "Time Machine", "unavailable"),
    ("3", "Authors Of Forever", "done"),
    ("4", "Wasted Energy", "unavailable"),
    ("5", "Underdog", "skipped"),
    ("6", "3 Hour Drive", "unavailable"),
    ("7", "Show Me Love", "failed"),
]
WANT = "COMPLETED | UNAVAILABLE | COMPLETED | UNAVAILABLE | IN LIBRARY | UNAVAILABLE | FAILED"


def test_a_delisted_track_reads_unavailable_not_failed():
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="waves-queue-unavailable-test-")
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
    ), f"the queue's UNAVAILABLE word regressed. Scenario exit={proc.returncode}:\n{tail}"


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
    q("queueDrawer.open()")
    settle(120)
    if not bool(q("queueDrawer.visible")):
        print("the queue drawer would not open", file=sys.stderr)
        return _EXIT_PRECONDITION

    qid = bridge._enqueue(
        "ALICIA (With Commentary)", "album", media_id="156010613", collection=True, tracks=len(LEDGER)
    )
    # Report every track the way a real download does, through the bridge's own
    # lifecycle handler. No `path` on the events, so the ownership write no-ops
    # rather than touching this machine.
    for tid, title, status in LEDGER:
        bridge._track_lifecycle(qid, {"id": tid, "num": int(tid), "title": title, "status": status})
    bridge.queueChanged.emit(list(bridge._queue))
    settle(120)

    bad = []

    # 1. The registry-only merge is the real path when the album fetch comes
    #    back empty, which is every offline case and any expand after a job.
    bridge._merge_queue_tracks(qid, [])
    q(f"root.queueExpanded = ({{ {qid}: true }})")
    settle(400)

    words = str(q("""(function () {
                var out = [];
                function walk(o) {
                    if (!o) return;
                    if (o.objectName === 'qTrackWord' && ('' + o.target) !== '')
                        out.push('' + o.target);
                    var kids = o.children;
                    for (var i = 0; i < (kids ? kids.length : 0); ++i) walk(kids[i]);
                }
                walk(queueList.itemAtIndex(0));
                return out.join(' | ');
            })()"""))
    if words != WANT:
        bad.append(f"the expanded ledger's outcome column read {words!r}, want {WANT!r}")

    # 2. UNAVAILABLE must not wear the failure red: the two states call for
    #    different things from the reader, and only one of them can be retried.
    colors = str(q("""(function () {
                var out = [];
                function walk(o) {
                    if (!o) return;
                    if (o.objectName === 'qTrackWord' && ('' + o.target) !== '')
                        out.push(('' + o.target) + '=' + ('' + o.color));
                    var kids = o.children;
                    for (var i = 0; i < (kids ? kids.length : 0); ++i) walk(kids[i]);
                }
                walk(queueList.itemAtIndex(0));
                return out.join(' | ');
            })()"""))
    seen = dict(part.split("=", 1) for part in colors.split(" | ") if "=" in part)
    if seen.get("UNAVAILABLE") == seen.get("FAILED"):
        bad.append(f"UNAVAILABLE is painted the same as FAILED ({colors!r})")
    if seen.get("UNAVAILABLE") == seen.get("COMPLETED"):
        bad.append(f"UNAVAILABLE is painted the same as COMPLETED ({colors!r})")

    # 3. A refused track is settled, not still pending: a row left at zero reads
    #    as work in flight for the rest of the job and stalls the album's bar.
    pcts = [bridge._job_tracks[qid][tid].get("pct") for tid, _t, status in LEDGER if status == "unavailable"]
    if pcts != [100.0, 100.0, 100.0]:
        bad.append(f"refused tracks did not settle their rows (pct={pcts})")

    if bad:
        print("\n".join(bad), file=sys.stderr)
        return _EXIT_REGRESSED
    print("ok: a delisted track says UNAVAILABLE, and says it in its own colour")
    return _EXIT_OK


if __name__ == "__main__":
    raise SystemExit(_run_scenario())
