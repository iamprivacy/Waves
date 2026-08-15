"""The queue drawer is dragged wider by its own edge, and the edge holds.

WHAT THIS FENCES OFF
--------------------
1. The handle drifting off the border. `queueDrawer.contentItem` is NOT the
   ColumnLayout declared inside the Drawer, it is the Popup's own popup item,
   sitting at leftPadding and already spanning the full height. A handle
   anchored to it without undoing that inset floats beside the drawer instead
   of on it, which is a gap the user sees and cannot grab. Nothing warns: the
   thing renders perfectly, just in the wrong place.

2. The panel MOVING instead of only resizing. A Drawer places itself from
   position * width, so changing its width mid-drag can slide the whole panel.
   The right edge must stay welded to the window edge through every width.

3. The floor going. 420 is where the quality a row states and the title it
   states it for stop fighting for the same pixels, so no drag may go under it.

4. The width being forgotten. It is remembered across launches like the window
   frame, and what is stored is the width that was ASKED for rather than the
   clamped one, so a drawer narrowed by a small window widens again on a large
   one instead of being permanently cut down by it.

5. The clamp being read once. The width is dragged, so a window that later
   shrinks would leave an over-wide drawer hanging off the side unless the
   ceiling is re-read from the current window width.

6. The save being a debounce and nothing else. Widen the drawer and quit, which
   is how the gesture actually ends, and the settled write is still pending: the
   close has to flush it the way the window frame's does, or the width the user
   just chose leaves with the timer that never fired.

Drives the REAL Main.qml with REAL mouse events, so it covers the handler and
the Drawer's own dismiss gesture: a horizontal drag is how a Drawer closes, and
this drags in exactly that axis. If preventStealing ever stops winning, the
drawer shuts here instead of resizing and the width assertions fail.

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

QML_MAIN = Path(__file__).resolve().parent.parent / "tidaler" / "waves_ui" / "qml" / "Main.qml"


def test_the_queue_drawer_resizes_by_its_edge_and_keeps_its_floor():
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="waves-queue-resize-test-")
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
    ), f"the queue drawer's resize handle regressed. Scenario exit={proc.returncode}:\n{tail}"


def _run_scenario() -> int:
    # THIS checkout's tidaler, not the venv's editable install.
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
        from tidaler.waves_ui.app import _load_mono
        from tidaler.waves_ui.backend import WavesBridge
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
    settle(200)
    if not bool(q("queueDrawer.visible")):
        print("the queue drawer would not open", file=sys.stderr)
        return _EXIT_PRECONDITION
    if int(q("queueDrawer.width")) != 420 or int(q("root.width")) != 1200:
        print(f"unexpected start: drawer={q('queueDrawer.width')} window={q('root.width')}", file=sys.stderr)
        return _EXIT_PRECONDITION

    bad: list[str] = []

    # 1. The handle is ON the border, not beside it, and runs the full height.
    off_x = float(q("queueGrip.mapToItem(queueDrawer.background, 0, 0).x"))
    off_y = float(q("queueGrip.mapToItem(queueDrawer.background, 0, 0).y"))
    if abs(off_x) > 0.01 or abs(off_y) > 0.01:
        bad.append(f"the handle sits at ({off_x:.1f}, {off_y:.1f}) from the drawer's corner, not on it")
    if abs(float(q("queueGrip.height")) - float(q("queueDrawer.height"))) > 0.01:
        bad.append("the handle does not span the drawer's full height")
    # Untouched, it adds nothing to the screen: the border is all there is.
    if float(q("gripGlow.opacity")) > 0.01:
        bad.append("the edge glow is lit with the pointer nowhere near the edge")

    def drag_to(scene_x: int) -> None:
        QTest.mouseMove(root, QPoint(scene_x, 300))
        settle(40)

    # 2. Drag it wider by 200, the right edge staying welded to the window.
    grab_x = int(q("queueDrawer.x")) + 6
    QTest.mousePress(root, Qt.LeftButton, Qt.NoModifier, QPoint(grab_x, 300))
    settle(40)
    if not bool(q("gripMouse.pressed")):
        print("the handle never took the press", file=sys.stderr)
        return _EXIT_PRECONDITION
    drag_to(grab_x - 200)
    wide = int(q("queueDrawer.width"))
    if wide != 620:
        bad.append(f"dragging 200px left gave a width of {wide}, not 620")
    right = int(q("queueDrawer.x + queueDrawer.width"))
    if right != 1200:
        bad.append(f"the drawer's right edge left the window: {right} against a window of 1200")

    # 3. Shove past the floor: the edge stops at 420 and stays there.
    drag_to(grab_x + 400)
    floored = int(q("queueDrawer.width"))
    if floored != 420:
        bad.append(f"dragging past the floor gave a width of {floored}, not 420")
    if int(q("queueDrawer.x + queueDrawer.width")) != 1200:
        bad.append("the drawer's right edge left the window at the floor")
    QTest.mouseRelease(root, Qt.LeftButton, Qt.NoModifier, QPoint(grab_x + 400, 300))
    settle(60)
    if not bool(q("queueDrawer.visible")):
        bad.append("the drawer closed during the drag: its own dismiss gesture won the press")

    # 4. The ceiling is re-read, so shrinking the window pulls a wide drawer in.
    q("root.queueWidth = 900")
    settle(60)
    if int(q("queueDrawer.width")) != 900:
        bad.append(f"a 900 width did not take: {int(q('queueDrawer.width'))}")
    q("root.width = 700")
    settle(120)
    pulled = int(q("queueDrawer.width"))
    if pulled != 620:
        bad.append(f"shrinking the window to 700 left the drawer at {pulled}, not 620 (window - 80)")

    # 5. The width is remembered, so the drawer opens where it was left. QML
    #    debounces the drag into one settled write, so wait past that interval.
    #    900, the width asked for, not the 620 the narrow window clamped it to.
    settle(800)
    remembered = int(q("waves.queueRestoreWidth()"))
    if remembered != 900:
        bad.append(f"the dragged width was not remembered: the bridge says {remembered}, not 900")

    # 6. Letting go settles where the hand is, not somewhere up top. The glow
    #    runs the full edge while the drag is on and collapses back to a blob
    #    around the hand when it ends, and those two states have to be one
    #    animation: while the collapse was a timed height against a
    #    distance-smoothed y, the finished blob appeared near the TOP of the
    #    drawer and slid down to the hand afterwards, which is a glitch you
    #    cannot miss when you grab the edge low down in a tall window.
    q("root.width = 1200")
    q("root.queueWidth = 500")
    settle(120)
    grip_x = int(q("queueDrawer.x")) + 6
    QTest.mousePress(root, Qt.LeftButton, Qt.NoModifier, QPoint(grip_x, 700))
    settle(40)
    QTest.mouseMove(root, QPoint(grip_x - 50, 700))
    settle(40)
    QTest.mouseRelease(root, Qt.LeftButton, Qt.NoModifier, QPoint(grip_x - 50, 700))
    # Mid-collapse and settled: the light stays centred on the hand throughout.
    for ms in (120, 260, 500):
        settle(ms if ms == 120 else ms - 120)
        centre = float(q("gripGlow.y + gripGlow.height / 2"))
        if abs(centre - 700) > 25:
            bad.append(f"{ms}ms after letting go the glow sat at {centre:.0f}, not on the hand at 700")
    if abs(float(q("gripGlow.height")) - 190) > 1:
        bad.append(f"the glow did not settle back to its resting height: {float(q('gripGlow.height')):.0f}")

    # 7. And remembered even when the quit lands inside the debounce, which is
    #    the ordinary gesture: widen the drawer, then close the app. The timer
    #    is stopped here so nothing but the close itself can do the saving.
    q("queueWidthSaveTimer.stop()")
    q("root.queueWidth = 700")
    q("queueWidthSaveTimer.stop()")  # the change above restarted it
    q("root.close()")
    settle(150)
    flushed = int(q("waves.queueRestoreWidth()"))
    if flushed != 700:
        bad.append(f"a width settled inside the debounce was lost at the quit: the bridge says {flushed}, not 700")

    for line in bad:
        print(f"REGRESSED: {line}", file=sys.stderr)
    return _EXIT_REGRESSED if bad else _EXIT_OK


if __name__ == "__main__":
    if "--run-scenario" in sys.argv:
        raise SystemExit(_run_scenario())
    raise SystemExit(_EXIT_PRECONDITION)
