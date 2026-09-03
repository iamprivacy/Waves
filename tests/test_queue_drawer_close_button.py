"""The queue drawer carries its own way out: a square X in the header.

WHAT THIS FENCES OFF
--------------------
1. The way out being invisible. A Drawer dismisses on a click outside it, but
   that is a gesture you have to already know. The header ends in a close
   button, and a REAL click on it shuts the panel.

2. The button going away with the queue. PAUSE hides when there is nothing
   queued and STOP when nothing is running, which is exactly the state where a
   user is most likely to be hunting for the exit, so the X is visible with an
   empty queue and with a full one alike.

3. It drifting off the corner. It sits last in the header row, to the right of
   PAUSE, where every window puts a close.

4. The square going. It is an icon-only SpecBtn: same fill, border and hover as
   the worded buttons beside it, exactly as tall as they are, and as wide as it
   is tall. A glyph button that measures its (empty) label wrongly reads as a
   thin sliver, and nothing warns.

5. Closing costing the user work. The X only shuts the panel: it must not pause
   the queue, stop anything, or drop a row.

Drives the REAL Main.qml with a REAL mouse click on the button's own pixels, so
the wiring is what is under test, not the function behind it.

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


def test_the_queue_drawer_closes_from_its_own_header_button():
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="waves-queue-close-test-")
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
    ), f"the queue drawer's close button regressed. Scenario exit={proc.returncode}:\n{tail}"


def _run_scenario() -> int:
    # THIS checkout's waves, not the venv's editable install.
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    try:
        from PySide6.QtCore import QEventLoop, QPoint, Qt, QTimer, QUrl
        from PySide6.QtGui import QGuiApplication
        from PySide6.QtQml import QQmlApplicationEngine, QQmlEngine, QQmlExpression
        from PySide6.QtTest import QTest
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

    q("root.width = 1200")
    q("root.height = 800")
    q("root.visible = true")
    settle(150)
    q(PARK_LOGIN_QML)
    q("queueDrawer.open()")
    settle(250)
    if not bool(q("queueDrawer.visible")):
        print("the queue drawer would not open", file=sys.stderr)
        return _EXIT_PRECONDITION

    bad: list[str] = []

    # 2. Empty queue: PAUSE and STOP are both gone and the X is still there.
    if int(q("queueModel.count")) != 0:
        print(f"the scenario started with {q('queueModel.count')} queued rows", file=sys.stderr)
        return _EXIT_PRECONDITION
    if bool(q("queuePauseBtn.visible")):
        print("PAUSE is showing with an empty queue, so this proves nothing", file=sys.stderr)
        return _EXIT_PRECONDITION
    if not bool(q("queueCloseBtn.visible")):
        bad.append("the close button is hidden when the queue is empty, which is when it is needed most")

    # 4. Square, and the same height as the worded buttons it sits beside.
    w = float(q("queueCloseBtn.width"))
    h = float(q("queueCloseBtn.height"))
    if abs(w - h) > 0.51:
        bad.append(f"the close button is {w:.0f}x{h:.0f}, not square")
    if w < 24:
        bad.append(f"the close button collapsed to {w:.0f}px wide: the glyph has no room")
    pause_h = float(q("queuePauseBtn.height"))
    if abs(h - pause_h) > 0.51:
        bad.append(f"the close button stands {h:.0f}px against PAUSE's {pause_h:.0f}: the row is ragged")

    # 3. Last in the row, to the right of PAUSE (which is measured even hidden).
    close_x = float(q("queueCloseBtn.mapToItem(null, 0, 0).x"))
    pause_x = float(q("queuePauseBtn.mapToItem(null, 0, 0).x"))
    if close_x <= pause_x:
        bad.append(f"the close button sits at x={close_x:.0f}, left of PAUSE at x={pause_x:.0f}")

    # 1. A real click on its own pixels shuts the drawer.
    cx = int(q("queueCloseBtn.mapToItem(null, queueCloseBtn.width / 2, queueCloseBtn.height / 2).x"))
    cy = int(q("queueCloseBtn.mapToItem(null, queueCloseBtn.width / 2, queueCloseBtn.height / 2).y"))
    was_paused = bool(q("waves.paused"))
    QTest.mouseClick(root, Qt.LeftButton, Qt.NoModifier, QPoint(cx, cy))
    settle(400)
    if bool(q("queueDrawer.visible")):
        bad.append(f"clicking the close button at ({cx}, {cy}) left the drawer open")

    # 5. And it did nothing else on the way out.
    if bool(q("waves.paused")) != was_paused:
        bad.append("closing the drawer paused or resumed the queue")
    if int(q("queueModel.count")) != 0:
        bad.append("closing the drawer changed the queue")

    # 2 again, with rows in it: the X keeps its place beside PAUSE and STOP.
    q("queueDrawer.open()")
    settle(250)
    # Straight into the view's model: all this needs is a row for PAUSE to
    # appear beside, not a working download behind it.
    q(
        "queueModel.append({'qid': 'close-btn-row', 'title': 'Song', 'sub': 'Artist',"
        " 'state': 'queued', 'uiGroup': 'queued'})"
    )
    settle(150)
    if int(q("queueModel.count")) < 1:
        print("could not put a row in the queue model", file=sys.stderr)
        return _EXIT_PRECONDITION
    if not bool(q("queueCloseBtn.visible")):
        bad.append("the close button disappeared once the queue had a row in it")
    if not bool(q("queuePauseBtn.visible")):
        print("PAUSE did not appear for a queued row", file=sys.stderr)
        return _EXIT_PRECONDITION
    close_x = float(q("queueCloseBtn.mapToItem(null, 0, 0).x"))
    pause_right = float(q("queuePauseBtn.mapToItem(null, 0, 0).x + queuePauseBtn.width"))
    if close_x < pause_right:
        bad.append(f"the close button at x={close_x:.0f} overlaps PAUSE, which ends at {pause_right:.0f}")

    if bad:
        for line in bad:
            print(f"REGRESSED: {line}", file=sys.stderr)
        return _EXIT_REGRESSED
    return _EXIT_OK


if __name__ == "__main__":
    if "--run-scenario" in sys.argv:
        raise SystemExit(_run_scenario())
    raise SystemExit(_EXIT_PRECONDITION)
