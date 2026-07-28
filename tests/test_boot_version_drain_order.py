"""The launch handover plays its two beats in order, never on top of
each other.

WHAT THIS FENCES OFF
--------------------
The handover is read as one gesture: the version readout under the wordmark
empties out (a bar fills, dims, then clears its cells), and only then does
the wordmark zoom toward the viewer. The wait between them was a fixed
700ms while the drain is a per-cell walk whose length follows the version
string ("v0.1.11" takes 792ms), so the last cells were still emptying after
the zoom had begun and the two beats were seen overlapping (reported from
livetesting).

HOW THIS STAYS FIXED
--------------------
bootHandover's pause is bound to bootBlk.runMs, the walk's own length, so
the readout is always finished before the zoom starts, for any version
string. This samples the running animation and pins the ORDER (last frame
with ink in the readout comes before the first frame of zoom), which holds
whatever the machine's timing, plus a loose bound proving the zoom still
follows promptly rather than after a dead beat.

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

# The zoom must start promptly once the readout is clear: the drain's
# closing tick, plus a generous allowance for sampling and a loaded CI box.
_MAX_GAP_MS = 300
_SAMPLE_MS = 25

QML_MAIN = Path(__file__).resolve().parent.parent / "tidaler" / "waves_ui" / "qml" / "Main.qml"


def test_the_version_drains_before_the_wordmark_zooms():
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="waves-boot-drain-test-")
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
    ), f"the launch handover's two beats overlap again. Scenario exit={proc.returncode}:\n{tail}"


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
    # Back to the launch frame. The sequence runs on its own here (the
    # readiness cap starts it), so silence every piece first: what follows is
    # then provably the handover started below. bootBlk overwrites bootVer's
    # text (breaking its binding), so the readout is restored by hand.
    for stop in ("bootSeq", "bootHandover", "bootBlk", "handoverCap"):
        q(f"{stop}.stop()")
    q("bootOverlay.done = false")
    q("bootOverlay.handoverHeld = false")
    q("handoverCap.expired = false")
    q("browseBuilding = false")
    q("bootTitle.zoom = 1")
    q("bootTitle.shown = 1")
    q("bootVer.shown = 1")
    q("bootVer.text = 'v' + waves.appVersion")
    settle(120)
    if not q("bootVer.text").strip() or q("bootTitle.zoom") != 1:
        print("could not restore the launch frame", file=sys.stderr)
        return _EXIT_PRECONDITION

    # Sample the whole handover: does the readout still carry ink (any cell
    # not yet blanked), and has the wordmark started growing?
    q("bootHandover.start()")
    samples = []
    for _ in range(120):
        settle(_SAMPLE_MS)
        ink = bool(str(q("bootVer.text")).strip()) and float(q("bootVer.shown")) > 0
        samples.append((ink, float(q("bootTitle.zoom")) > 1.001))
        if q("bootOverlay.done"):
            break

    inked = [i for i, (ink, _) in enumerate(samples) if ink]
    zoomed = [i for i, (_, z) in enumerate(samples) if z]
    if not inked or not zoomed:
        print(f"the handover did not play: ink={len(inked)} zoom={len(zoomed)}", file=sys.stderr)
        return _EXIT_PRECONDITION

    # The order is the whole point: every frame with ink in the readout comes
    # before every frame of zoom.
    ordered = inked[-1] < zoomed[0]
    # ...and the zoom follows promptly, rather than after a dead beat.
    gap_ms = (zoomed[0] - inked[-1]) * _SAMPLE_MS
    prompt = gap_ms <= _MAX_GAP_MS
    print(
        f"ordered={ordered} prompt={prompt} last_ink={inked[-1]} first_zoom={zoomed[0]} gap<={gap_ms}ms",
        flush=True,
    )
    return _EXIT_OK if (ordered and prompt) else _EXIT_REGRESSED


if __name__ == "__main__":
    raise SystemExit(_run_scenario())
