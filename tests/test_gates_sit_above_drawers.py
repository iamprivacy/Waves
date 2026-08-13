"""Full-screen gates sit in the window's overlay layer, above any open Drawer.

WHAT THIS FENCES OFF
--------------------
A Qt Quick Controls ``Drawer`` (the download queue) does not render inside the
page: it is placed in the window's OVERLAY layer, which paints over ordinary
content regardless of the z that content carries. exitGate and updateOptInGate
are full-screen Rectangles that used to be parented to the page with z 1200,
which reads like "on top" but is not: with the queue drawer open, the exit
prompt was masked by the drawer's dim and the window looked un-closable.

The fix is ``parent: Overlay.overlay``, which moves the gate into the same
layer so its z finally means something. That is easy to "tidy" back out, since
the z alone looks sufficient, so this test pins the parent.

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

GATES = ("exitGate", "updateOptInGate")


def test_gates_are_in_the_overlay_layer_not_the_page():
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="waves-gate-layer-test-")
    proc = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), "--run-scenario"],
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    tail = "\n".join((proc.stdout + proc.stderr).strip().splitlines()[-10:])
    import pytest

    if proc.returncode == _EXIT_NO_QT:
        pytest.skip("PySide6 / offscreen Qt unavailable")
    if proc.returncode == _EXIT_PRECONDITION:
        pytest.skip(f"could not set up the scenario in this environment:\n{tail}")
    assert proc.returncode == _EXIT_OK, (
        "a full-screen gate left the overlay layer, so an open queue drawer will mask it again. "
        f"Scenario exit={proc.returncode}:\n{tail}"
    )


def _run_scenario() -> int:
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

    settle(150)
    q(PARK_LOGIN_QML)
    settle(60)

    # Open the drawer so the failure mode is actually present, then check each
    # gate is a child of the overlay rather than of the page.
    q("queueDrawer.open()")
    settle(120)
    drawer_open = bool(q("queueDrawer.visible"))

    verdicts = {name: bool(q(f"{name}.parent === Overlay.overlay")) for name in GATES}
    print(f"drawer_open={drawer_open} verdicts={verdicts}", flush=True)

    ok = drawer_open and all(verdicts.values())
    return _EXIT_OK if ok else _EXIT_REGRESSED


if __name__ == "__main__":
    raise SystemExit(_run_scenario())
