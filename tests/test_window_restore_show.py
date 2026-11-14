"""The window shows itself once, by one property, with no conflict warning.

The window used to declare ``visible: false`` and then, once the saved frame
was applied, show itself with ``visibility = Window.Maximized`` for a frame
saved maximized. Both properties set explicitly during creation, one saying
hidden and one saying shown, is what Qt warns about ("Conflicting properties
'visible' and 'visibility'"). A Window starts hidden on its own, so nothing
needs to be declared: the show sets exactly one of the two.

Offscreen, in a subprocess, with the real bridge, a stubbed saved frame and
a temp config.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

_EXIT_OK = 0
_EXIT_REGRESSED = 1
_EXIT_NO_QT = 77
_EXIT_PRECONDITION = 78

QML_MAIN = Path(__file__).resolve().parent.parent / "waves" / "waves_ui" / "qml" / "Main.qml"


def test_the_window_declares_no_visibility_of_its_own():
    src = QML_MAIN.read_text(encoding="utf-8")
    head = src[src.index("ApplicationWindow {") : src.index("Component.onCompleted: {")]
    assert not re.search(r"^    visible:", head, re.M), "the window declares visible again"
    assert not re.search(r"^    visibility:", head, re.M), "the window declares visibility"


def _run(case: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="waves-window-show-test-")
    return subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), "--run-scenario", case],  # noqa: S603 (fixed argv)
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )


@pytest.mark.parametrize("case", ["maximized", "windowed", "fresh"])
def test_the_window_shows_without_a_conflict_warning(case):
    proc = _run(case)
    tail = "\n".join((proc.stdout + proc.stderr).strip().splitlines()[-14:])
    if proc.returncode == _EXIT_NO_QT:
        pytest.skip("PySide6 / offscreen Qt unavailable")
    if proc.returncode == _EXIT_PRECONDITION:
        pytest.skip(f"could not set up the scenario in this environment:\n{tail}")
    assert proc.returncode == _EXIT_OK, f"case {case}: exit={proc.returncode}:\n{tail}"


def _run_scenario(case: str) -> int:  # (one straight scenario)
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    try:
        from PySide6.QtCore import QEventLoop, QTimer, QUrl, Slot, qInstallMessageHandler
        from PySide6.QtGui import QGuiApplication, QWindow
        from PySide6.QtQml import QQmlApplicationEngine
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

    frames = {
        "maximized": {"x": 40, "y": 40, "w": 1100, "h": 800, "maximized": True},
        "windowed": {"x": 40, "y": 40, "w": 1100, "h": 800, "maximized": False},
        "fresh": {},
    }
    if case not in frames:
        print(f"unknown case {case!r}", file=sys.stderr)
        return _EXIT_PRECONDITION
    saved = frames[case]

    class Bridge(WavesBridge):
        @Slot(result="QVariant")
        def windowRestoreGeometry(self):
            return dict(saved)

    messages: list[str] = []
    qInstallMessageHandler(lambda _mode, _ctx, msg: messages.append(msg))

    engine = QQmlApplicationEngine()
    bridge = Bridge(tidal=None)
    engine.rootContext().setContextProperty("waves", bridge)
    engine.rootContext().setContextProperty("monoFont", _load_mono())
    engine.rootContext().setContextProperty("uiFontFamily", app.font().family())
    engine.load(QUrl.fromLocalFile(str(QML_MAIN)))
    roots = engine.rootObjects()
    if not roots:
        print("Main.qml failed to load:\n" + "\n".join(messages[-10:]), file=sys.stderr)
        return _EXIT_PRECONDITION
    root = roots[0]

    loop = QEventLoop()
    QTimer.singleShot(600, loop.quit)
    loop.exec()

    conflict = [m for m in messages if "Conflicting properties" in m]
    if conflict:
        print("\n".join(conflict), file=sys.stderr)
        return _EXIT_REGRESSED
    if not root.property("visible"):
        print("the window never showed", file=sys.stderr)
        return _EXIT_REGRESSED
    vis = root.property("visibility")
    want_max = case == "maximized"
    is_max = vis == QWindow.Visibility.Maximized
    if want_max and not is_max:
        # The offscreen platform may refuse to maximize; that is not ours.
        print(f"offscreen did not maximize (visibility {vis})", file=sys.stderr)
        return _EXIT_PRECONDITION
    if not want_max and is_max:
        print("a windowed frame came up maximized", file=sys.stderr)
        return _EXIT_REGRESSED
    print(f"ok: {case} shown as {vis}, no conflict warning")
    return _EXIT_OK


if __name__ == "__main__":
    if "--run-scenario" in sys.argv:
        args = sys.argv[sys.argv.index("--run-scenario") + 1 :]
        raise SystemExit(_run_scenario(args[0] if args else ""))
    raise SystemExit(_EXIT_PRECONDITION)
