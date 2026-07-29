"""The launch screen is clean water: no scroll dressing paints over it.

WHAT THIS FENCES OFF
--------------------
The edge fades and the crest pill live in one window-level BackToTop, a
sibling of the main column rather than a child of it, so the reveal fade
that hides the interface during the launch sequence did not reach them.
The landing is scrollable from its first frame, so the bottom fade sat at
full strength across the opening water (and the top fade too, whenever the
restored scroll position was off zero), a dark band over the wordmark
frame (reported from livetesting).

HOW THIS STAYS FIXED
--------------------
The instance carries opacity: root.bootContentShown, the same gate the
interface column and the login overlay use. This pins the dressing to
invisible while the launch frame is up and to fully present once the
handover has run.

Runs in a SUBPROCESS for the same reason as the other boot scenarios:
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

QML_MAIN = Path(__file__).resolve().parent.parent / "tidaler" / "waves_ui" / "qml" / "Main.qml"


def test_the_edge_fades_stay_off_the_launch_screen():
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="waves-boot-dressing-test-")
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
    ), f"scroll dressing paints over the launch screen again. Scenario exit={proc.returncode}:\n{tail}"


def _run_scenario() -> int:
    # THIS checkout's tidaler, not the venv's editable install.
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    try:
        from PySide6.QtCore import QEventLoop, QTimer, QUrl
        from PySide6.QtGui import QGuiApplication
        from PySide6.QtQml import QQmlApplicationEngine, QQmlEngine, QQmlExpression
    except Exception as exc:
        print(f"Qt unavailable: {exc}", file=sys.stderr)
        return _EXIT_NO_QT

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
        ctx = QQmlEngine.contextForObject(root)
        e = QQmlExpression(ctx, root, expr)
        r = e.evaluate()
        if e.hasError():
            raise RuntimeError(e.error().toString())
        return r[0] if isinstance(r, tuple) else r

    def settle(ms: int) -> None:
        loop = QEventLoop()
        QTimer.singleShot(ms, loop.quit)
        loop.exec()

    settle(120)
    # Back to the launch frame: silence the sequence, then drive the reveal
    # dial by hand so the assertion is about the binding, not about timing.
    for stop in ("bootSeq", "bootHandover", "bootBlk", "handoverCap"):
        q(f"{stop}.stop()")
    q("bootOverlay.done = false")
    q("bootContentShown = 0")
    settle(60)
    hidden = float(q("scrollDressing.opacity"))

    q("bootContentShown = 1")
    settle(60)
    shown = float(q("scrollDressing.opacity"))

    ok = hidden == 0.0 and shown == 1.0
    print(f"during_launch={hidden} after_handover={shown}", flush=True)
    return _EXIT_OK if ok else _EXIT_REGRESSED


if __name__ == "__main__":
    raise SystemExit(_run_scenario())
