"""The interface is painted before the launch zoom reveals it.

WHAT THIS FENCES OFF
--------------------
Qt Quick's renderer skips a subtree whose opacity is 0: nothing in it is
rastered, uploaded, or built until the frame it first becomes visible. The
interface hides that way during the launch sequence, so the whole Browse
page (every texture, every glyph, every material) was paid for in a single
frame, and the reveal starts 350ms into a 700ms wordmark zoom. The result
was a hitch in the middle of the zoom, in the same place every launch,
because the reveal always begins in the same place.

HOW THIS STAYS FIXED
--------------------
root.bootWarming lifts the interface to 0.004 while the version readout
drains, which is above the renderer's 0.001 skip threshold and far below
anything an eye can see over the launch scrim. The heavy first frame is
spent during the drain, where the only thing moving is a text readout on
its own timer.

Two ways this could regress, both pinned here:

1. warming wired to bootContentShown instead of its own dial, which would
   also ungate input and hand issue #13 back (the interface must stay inert
   and shielded while invisible);
2. the warm starting with the zoom rather than before it, which would put
   the first frame back inside the animation it was moved out of.

The behavioral half runs in a SUBPROCESS for the same reason as the other
boot scenarios: building the bridge installs process-global handlers that
must not leak into the rest of the suite.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

_EXIT_OK = 0
_EXIT_REGRESSED = 1
_EXIT_NO_QT = 77
_EXIT_PRECONDITION = 78

QML_MAIN = Path(__file__).resolve().parent.parent / "tidaler" / "waves_ui" / "qml" / "Main.qml"


def test_the_warm_runs_before_the_zoom_not_inside_it():
    src = QML_MAIN.read_text()

    # The drain's starter arms the warm; the zoom is started later, by the
    # drain's own last tick (see test_boot_version_drain_order).
    handover = src[src.index("id: bootHandover") :]
    handover = handover[: handover.index("SequentialAnimation {")]
    assert "root.bootWarming = true" in handover, "the warm no longer starts with the version drain"
    assert "bootBlk.restart()" in handover

    zoom = src[src.index("id: bootZoom") :]
    zoom = zoom[: zoom.index("\n        }")]
    assert "bootWarming" not in zoom, "the warm moved into the zoom, which is the frame it exists to spare"


def test_warming_is_not_the_reveal_dial():
    # bootContentShown ungates input (issue #13). If warming rode it, the
    # interface would be live under the launch screen again.
    src = QML_MAIN.read_text()

    assert re.search(r"property bool bootWarming: false", src)
    assert "enabled: root.bootContentShown > 0" in src
    assert "enabled: root.bootContentShown === 0" in src  # the shield


def test_the_interface_is_rendered_but_invisible_and_inert_while_warming():
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="waves-boot-warm-test-")
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
    assert proc.returncode == _EXIT_OK, f"the launch pre-warm regressed. Scenario exit={proc.returncode}:\n{tail}"


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
    # Back to the launch frame, then drive the dials by hand so the
    # assertions are about the bindings rather than about timing.
    for stop in ("bootSeq", "bootHandover", "bootBlk", "handoverCap", "bootZoom"):
        q(f"{stop}.stop()")
    q("bootOverlay.done = false")
    q("bootContentShown = 0")
    q("bootWarming = false")
    settle(60)
    # Before the drain: nothing of the interface is drawn at all.
    cold = float(q("mainColumn.opacity")) == 0.0

    q("bootWarming = true")
    settle(60)
    warm_op = float(q("mainColumn.opacity"))
    # Above the renderer's skip threshold, so the page is actually painted...
    rendered = warm_op > 0.001
    # ... and far below anything visible over the launch scrim.
    unseen = warm_op < 0.02
    # Warming must not hand back issue #13: still inert, still shielded.
    inert = not bool(q("mainColumn.enabled")) and bool(q("bootShield.enabled"))

    # The reveal still owns the fade: warming cannot clamp or offset it.
    q("bootContentShown = 0.5")
    settle(60)
    reveal_exact = abs(float(q("mainColumn.opacity")) - 0.5) < 1e-6
    q("bootContentShown = 1")
    settle(60)
    revealed_open = bool(q("mainColumn.enabled")) and not bool(q("bootShield.enabled"))

    ok = cold and rendered and unseen and inert and reveal_exact and revealed_open
    print(
        f"cold={cold} warm_opacity={warm_op} rendered={rendered} unseen={unseen} "
        f"inert={inert} reveal_exact={reveal_exact} revealed_open={revealed_open}",
        flush=True,
    )
    return _EXIT_OK if ok else _EXIT_REGRESSED


if __name__ == "__main__":
    raise SystemExit(_run_scenario())
