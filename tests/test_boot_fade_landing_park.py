"""The wordmark fade must not share its frames with the landing build.

WHAT THIS FENCES OFF
--------------------
On a warm cache the Browse landing payload arrives ~1.5s into launch, in
the middle of the boot wordmark's 900ms fade-up (bootIntro). Applying it
there costs the fade its frames: even the asynchronous build's section
shells drop a visible frame gap, and the fade stutters (reported from
livetesting, confirmed with the launch probe). onBrowseLoaded now parks a
first build that arrives while bootIntro is running, and bootIntro's
onFinished applies it the moment the composed mark is still.

HOW THIS STAYS FIXED
--------------------
Three behaviors, each asserted here:
1. A non-error payload arriving mid-fade with nothing on screen is parked,
   not applied.
2. The parked payload is applied when the fade finishes.
3. An error payload goes straight through even mid-fade: the boot's error
   path must never sit on the wordmark.

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


def test_fade_parks_the_first_landing_and_applies_it_after():
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="waves-fade-park-test-")
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
    assert proc.returncode == _EXIT_OK, f"the fade-park regressed. Scenario exit={proc.returncode}:\n{tail}"


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

    app = QGuiApplication.instance() or QGuiApplication([])
    try:
        from waves.waves_ui.app import _load_mono
        from waves.waves_ui.backend import WavesBridge
    except Exception as exc:
        print(f"Qt platform/backend unavailable: {exc}", file=sys.stderr)
        return _EXIT_NO_QT

    class _QuietBridge(WavesBridge):
        # Same silencing as test_boot_handover_gate: payloads in this scenario
        # are driven by hand, never by a real fetch.
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

    settle()
    # Freeze the boot machinery so the fade state is driven purely by this
    # scenario. Payloads carry no sections, so no delegate ever incubates:
    # browseChips is the applied/not-applied witness.
    q("bootSeq.stop()")
    q("bootHandover.stop()")
    q("bootBlk.stop()")
    q("bootZoom.stop()")
    q("handoverCap.stop()")
    q("bootOverlay.done = false")
    q("browseSections = []")
    q("_browseParked = null")

    # 1. Mid-fade, first build: the payload must park, not apply.
    q("bootIntro.restart()")
    settle(30)
    if not bool(q("bootIntro.running")):
        print("could not hold bootIntro running", file=sys.stderr)
        return _EXIT_PRECONDITION
    bridge.browseLoaded.emit({"sections": [], "genres": ["parked-genre"], "moods": [], "decades": [], "error": False})
    settle(30)
    if not bool(q("_browseParked !== null")):
        print("mid-fade payload was not parked", file=sys.stderr)
        return _EXIT_REGRESSED
    if q("(browseChips.genres || []).length") != 0:
        print("mid-fade payload was applied during the fade", file=sys.stderr)
        return _EXIT_REGRESSED

    # 2. The fade finishing must apply the parked payload.
    q("bootIntro.complete()")
    settle(60)
    if not bool(q("_browseParked === null")):
        print("parked payload still held after the fade", file=sys.stderr)
        return _EXIT_REGRESSED
    if q("(browseChips.genres || [])[0]") != "parked-genre":
        print("parked payload was not applied when the fade finished", file=sys.stderr)
        return _EXIT_REGRESSED

    # 3. An error payload mid-fade must go straight through.
    q("browseSections = []")
    q("browseChips = ({ genres: [], moods: [], decades: [] })")
    q("browseError = false")
    q("bootIntro.restart()")
    settle(30)
    bridge.browseLoaded.emit({"sections": [], "genres": [], "moods": [], "decades": [], "error": True})
    settle(30)
    if not bool(q("browseError")):
        print("error payload did not apply during the fade", file=sys.stderr)
        return _EXIT_REGRESSED
    if not bool(q("_browseParked === null")):
        print("error payload was parked instead of applied", file=sys.stderr)
        return _EXIT_REGRESSED
    q("bootIntro.stop()")

    return _EXIT_OK


if __name__ == "__main__":
    if "--run-scenario" in sys.argv:
        sys.exit(_run_scenario())
    print("run via pytest, or with --run-scenario", file=sys.stderr)
    sys.exit(2)
