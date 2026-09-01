"""The boot shield must release the cursor the moment the reveal starts.

WHAT THIS FENCES OFF
--------------------
The launch overlay's input shield (bootShield, issue #13) is a full-window
MouseArea at z:100000. A MouseArea claims its cursorShape even while
DISABLED (the same Qt behavior the library pill documents); only an
invisible one claims nothing. The shield used to be gated by `enabled`
alone, so any session where bootOverlay.done never flipped true (an
interrupted boot zoom leaves exactly that: content shown, done false) kept
the plain arrow cursor over every button in the app for the whole session,
while clicks and hover passed through and made the app look otherwise
healthy (reported from a real session, 2026-09-01). The shield is now
visibility-gated too, so a stuck `done` cannot cost the pointing hand.

HOW THIS STAYS FIXED
--------------------
Two behaviors, each asserted here:
1. In the latch state (content shown, done still false, overlay visible)
   a hover over a pointing-hand control sets the window's pointing hand,
   and the hover reaches the control (containsMouse), proving the probe
   mechanism is live rather than vacuously agreeing.
2. During the boot proper (content not shown, overlay up) the shield still
   eats hover: the same control's containsMouse stays false, so the
   issue #13 shielding is kept. (The arrow cursor itself is no longer the
   witness there: current Qt does not resolve cursors through the
   opacity-hidden interface at all, shield or no shield.)

Runs in a SUBPROCESS for the same reason as test_boot_handover_gate:
building the bridge installs process-global handlers that must not leak
into the rest of the suite.
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


def test_boot_shield_releases_the_cursor_when_content_shows():
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="waves-boot-shield-cursor-test-")
    proc = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), "--run-scenario"],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    tail = "\n".join((proc.stdout + proc.stderr).strip().splitlines()[-8:])
    import pytest

    if proc.returncode == _EXIT_NO_QT:
        pytest.skip("PySide6 / offscreen Qt unavailable")
    if proc.returncode == _EXIT_PRECONDITION:
        pytest.skip(f"could not set up the scenario in this environment:\n{tail}")
    assert (
        proc.returncode == _EXIT_OK
    ), f"the boot shield's cursor release regressed. Scenario exit={proc.returncode}:\n{tail}"


def _run_scenario() -> int:
    # THIS checkout's waves, not the venv's editable install.
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    try:
        from PySide6.QtCore import QEvent, QEventLoop, QPoint, QPointF, Qt, QTimer, QUrl
        from PySide6.QtGui import QGuiApplication, QMouseEvent
        from PySide6.QtQml import QQmlApplicationEngine, QQmlEngine, QQmlExpression
    except Exception as exc:
        print(f"Qt unavailable: {exc}", file=sys.stderr)
        return _EXIT_NO_QT

    app = QGuiApplication.instance() or QGuiApplication([])
    try:
        from waves.waves_ui.app import _load_mono
        from waves.waves_ui.backend import WavesBridge
    except Exception as exc:
        print(f"Qt platform/backend unavailable: {exc}", file=sys.stderr)
        return _EXIT_NO_QT

    class _QuietBridge(WavesBridge):
        # Payloads in this scenario are driven by hand, never by a real fetch.
        def loadBrowse(self) -> None:
            return

    engine = QQmlApplicationEngine()
    bridge = _QuietBridge(tidal=None)
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
        ctx = QQmlEngine.contextForObject(root)
        e = QQmlExpression(ctx, root, expr)
        r = e.evaluate()
        if e.hasError():
            raise RuntimeError(e.error().toString())
        return r[0] if isinstance(r, tuple) else r

    def settle(ms: int = 120) -> None:
        loop = QEventLoop()
        QTimer.singleShot(ms, loop.quit)
        loop.exec()

    def hover(x: float, y: float):
        ev = QMouseEvent(
            QEvent.MouseMove,
            QPointF(x, y),
            root.mapToGlobal(QPoint(int(x), int(y))),
            Qt.NoButton,
            Qt.NoButton,
            Qt.NoModifier,
        )
        app.sendEvent(root, ev)
        app.processEvents()
        return root.cursor().shape()

    settle(300)
    # Freeze the boot machinery so overlay state is driven purely by this
    # scenario, and take the sign-in overlay (its own full-window shield,
    # correct behavior) out of the way.
    for anim in ("bootSeq", "bootHandover", "bootBlk", "bootZoom", "bootIntro", "handoverCap"):
        q(f"{anim}.stop()")
    q("loginPanel.visible = false")
    settle(60)

    # A pointing-hand, hover-enabled control to aim at. Stashed in the
    # _browseParked var property so both legs read the SAME item's
    # containsMouse back (a fresh QQmlExpression cannot carry a JS object
    # across evaluations); nulled again before the scenario ends.
    q(
        "_browseParked = (function(){"
        "  function up(it){ var n=it; while(n){ if(!n.visible) return false; n=n.parent } return true }"
        "  function rec(it){"
        "    if (!it) return null;"
        "    var kids = it.children;"
        "    for (var i=0;i<kids.length;i++){"
        "      var c = kids[i];"
        "      if (c && c.cursorShape === Qt.PointingHandCursor && c.hoverEnabled === true"
        "          && up(c) && c.enabled && c.width > 2 && c.height > 2) return c;"
        "      var r = rec(c); if (r) return r;"
        "    }"
        "    return null;"
        "  }"
        "  return rec(contentItem);"
        "})()"
    )
    if not bool(q("_browseParked !== null")):
        print("no visible hover-enabled pointing-hand MouseArea found to aim at", file=sys.stderr)
        return _EXIT_PRECONDITION
    x = q("_browseParked.mapToItem(null, _browseParked.width/2, _browseParked.height/2).x")
    y = q("_browseParked.mapToItem(null, _browseParked.width/2, _browseParked.height/2).y")

    from PySide6.QtCore import Qt as QtNS

    # 1. The latch: content shown, done never flipped. The overlay is still
    # visible and the shield must no longer claim the cursor; the hover
    # reaching the control proves this probe is live.
    q("bootContentShown = 1")
    q("bootOverlay.done = false")
    settle(60)
    if not bool(q("bootOverlay.visible")):
        print("latch precondition failed: overlay not visible with done=false", file=sys.stderr)
        return _EXIT_PRECONDITION
    hover(2, 2)
    got = hover(x, y)
    if not bool(q("_browseParked.containsMouse")):
        print("hover probe is dead: the control never saw the pointer", file=sys.stderr)
        return _EXIT_PRECONDITION
    if got != QtNS.PointingHandCursor:
        print(f"latched boot overlay still claims the cursor: got {got}", file=sys.stderr)
        return _EXIT_REGRESSED

    # 2. The boot proper: content not shown, overlay up, and the shield must
    # still eat hover before it reaches the interface (issue #13, kept).
    q("bootContentShown = 0")
    settle(60)
    if not (bool(q("bootShield.visible")) and bool(q("bootShield.enabled"))):
        print("boot precondition failed: shield not up at bootContentShown 0", file=sys.stderr)
        return _EXIT_REGRESSED
    hover(2, 2)
    hover(x, y)
    if bool(q("_browseParked.containsMouse")):
        print("shield no longer eats hover during the boot", file=sys.stderr)
        return _EXIT_REGRESSED

    q("_browseParked = null")
    return _EXIT_OK


if __name__ == "__main__":
    if "--run-scenario" in sys.argv:
        sys.exit(_run_scenario())
    print("run via pytest, or with --run-scenario", file=sys.stderr)
    sys.exit(2)
