"""Settings > Releases offers what the auto-download takes, on the real page.

WHAT THIS FENCES OFF
--------------------
"What downloads automatically" is a row of four checkboxes (Albums, EPs,
Singles, Featured) under "Download new releases automatically". On the real
SettingsPage.qml inside Main.qml:

1. The row is hidden while the switch is off and shows once it is on.
2. It ships with Albums, EPs and Singles ticked and Featured not.
3. A real click on a box ticks or unticks it, and the page stages the pick
   in the options' own order, ready for SAVE CHANGES.

The bridge is built offline (``tidal=None``, cached login parked). Runs in a
SUBPROCESS like the other Main.qml scenarios: building the bridge installs
process-global handlers that must not leak into the suite.
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


def test_the_auto_download_kinds_row_shows_under_the_switch_and_ticks():
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="waves-auto-kinds-test-")
    env["HOME"] = env["XDG_CONFIG_HOME"]  # nothing in the scenario may reach the real home
    proc = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), "--run-scenario"],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    tail = "\n".join((proc.stdout + proc.stderr).strip().splitlines()[-10:])
    import pytest

    if proc.returncode == _EXIT_NO_QT:
        pytest.skip("PySide6 / offscreen Qt unavailable")
    if proc.returncode == _EXIT_PRECONDITION:
        pytest.skip(f"could not set up the scenario in this environment:\n{tail}")
    assert (
        proc.returncode == _EXIT_OK
    ), f"the auto-download kinds row regressed. Scenario exit={proc.returncode}:\n{tail}"


def _run_scenario() -> int:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    try:
        from PySide6.QtCore import QEventLoop, QPoint, Qt, QTimer, QUrl
        from PySide6.QtGui import QGuiApplication
        from PySide6.QtQml import QQmlApplicationEngine, QQmlEngine, QQmlExpression
        from PySide6.QtQuick import QQuickWindow
        from PySide6.QtTest import QTest
    except Exception as exc:
        print(f"Qt unavailable: {exc}", file=sys.stderr)
        return _EXIT_NO_QT

    from _qml_offline import PARK_LOGIN_QML, patch_offline

    patch_offline()

    app = QGuiApplication.instance() or QGuiApplication([])
    if sys.platform != "darwin":
        app.setApplicationName("Waves")
        app.setOrganizationName("Waves")
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
    if not roots or not isinstance(roots[0], QQuickWindow):
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

    def visual_items():
        stack = [root.contentItem()]
        while stack:
            it = stack.pop()
            yield it
            stack.extend(it.childItems())

    def box(kind: str):
        return next((it for it in visual_items() if it.objectName() == "settingsKind_" + kind), None)

    def center_of(it):
        p = it.mapToScene(it.boundingRect().center())
        return QPoint(int(p.x()), int(p.y()))

    root.resize(1280, 900)
    root.setVisible(True)
    settle(120)
    q("bootOverlay.done = true")
    q("bootContentShown = 1")
    q(PARK_LOGIN_QML)
    q("termsGate.visible = false")
    q("ffmpegGate.visible = false")
    q("root.settingsOpen = true")
    settle(200)
    ids = str(q('settingsPage.groups.map(function(g) { return g.id }).join(",")') or "").split(",")
    if "releases" not in ids:
        print(f"no Releases section in the schema: {ids}", file=sys.stderr)
        return _EXIT_PRECONDITION
    # Releases on show, so the row sits on screen for a real click.
    q('settingsPage.showSection("releases")')
    settle(400)

    failures: list[str] = []
    album = box("album")
    if album is None:
        print("the kinds row was never built", file=sys.stderr)
        return _EXIT_PRECONDITION
    if album.isVisible():
        failures.append("the kinds row shows while the auto-download switch is off")

    q('settingsPage.setv("releases_auto_download", true)')
    settle(300)
    boxes = {k: box(k) for k in ("album", "ep", "single", "featured")}
    if not all(b is not None and b.isVisible() for b in boxes.values()):
        failures.append(
            f"the kinds row did not show once the switch was on: { {k: bool(b and b.isVisible()) for k, b in boxes.items()} }"
        )
    ticks = {k: bool(b.property("on")) for k, b in boxes.items() if b is not None}
    if ticks != {"album": True, "ep": True, "single": True, "featured": False}:
        failures.append(f"the shipped pick is wrong: {ticks}")

    c = center_of(boxes["featured"])
    if not (0 < c.x() < root.width() and 0 < c.y() < root.height()):
        print(f"the Featured box is off screen at {c}", file=sys.stderr)
        return _EXIT_PRECONDITION
    QTest.mouseClick(root, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, c)
    settle(200)
    staged = q('settingsPage.editMap["releases_auto_kinds"]')
    if staged != "album,ep,single,featured":
        failures.append(f"ticking Featured staged {staged!r}")
    QTest.mouseClick(root, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, center_of(boxes["album"]))
    settle(200)
    staged = q('settingsPage.editMap["releases_auto_kinds"]')
    if staged != "ep,single,featured":
        failures.append(f"unticking Albums staged {staged!r}")
    if bool(boxes["album"].property("on")) or not bool(boxes["featured"].property("on")):
        failures.append("the boxes do not show the staged pick")

    shot = os.environ.get("WAVES_AUTO_KINDS_SHOT")
    if shot:
        top = max(0, int(album.mapToScene(album.boundingRect().topLeft()).y()) - 260)
        root.grabWindow().copy(0, top, int(root.width()), 520).save(shot)

    if failures:
        print("\n".join(failures), file=sys.stderr)
        return _EXIT_REGRESSED
    print("ok")
    return _EXIT_OK


if __name__ == "__main__" and "--run-scenario" in sys.argv:
    sys.exit(_run_scenario())
