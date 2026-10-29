"""Settings' unsaved marks and deep links, run on the real page.

- The rail dot and the row bars mark only what is staged since the last
  SAVE or CANCEL: a save keeps editMap (so the controls show what was just
  saved) but clears the marks, and the diagnostics switches, which apply
  live, never light them.
- A deep link lands at the top of its section even when that section is the
  one already on show, and even when it arrives while Settings is hidden
  (the remembered spot would otherwise be restored on the way back in).

Runs Main.qml offscreen in a subprocess with a sandboxed HOME.
"""

from __future__ import annotations

import os
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


def test_unsaved_marks_clear_on_save_and_deep_links_land_on_top() -> None:
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    sandbox = tempfile.mkdtemp(prefix="waves-settings-marks-")
    env["XDG_CONFIG_HOME"] = sandbox
    env["HOME"] = sandbox  # nothing in the scenario may reach the real home
    proc = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), "--run-scenario"],
        env=env,
        capture_output=True,
        text=True,
        timeout=240,
    )
    tail = "\n".join((proc.stdout + proc.stderr).strip().splitlines()[-30:])
    if proc.returncode == _EXIT_NO_QT:
        pytest.skip("PySide6 / offscreen Qt unavailable")
    if proc.returncode == _EXIT_PRECONDITION:
        pytest.skip(f"could not set up the scenario in this environment:\n{tail}")
    assert proc.returncode == _EXIT_OK, f"Settings marks or deep links regressed:\n{tail}"


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
            raise RuntimeError(f"{expr}: {e.error().toString()}")
        return r[0] if isinstance(r, tuple) else r

    def settle(ms: int) -> None:
        loop = QEventLoop()
        QTimer.singleShot(ms, loop.quit)
        loop.exec()

    def named(name: str):
        stack = [root.contentItem()]
        while stack:
            it = stack.pop()
            if it.objectName() == name:
                return it
            stack.extend(it.childItems())
        return None

    def click(it) -> None:
        p = it.mapToScene(it.boundingRect().center())
        QTest.mouseClick(
            root, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(int(p.x()), int(p.y()))
        )

    root.setVisible(True)
    settle(120)
    root.resize(int(root.property("minimumWidth")), int(root.property("minimumHeight")))
    q("bootOverlay.done = true")
    q("bootContentShown = 1")
    q(PARK_LOGIN_QML)
    q("termsGate.visible = false")
    q("ffmpegGate.visible = false")
    q("root.settingsOpen = true")
    settle(500)

    pane = named("settingsPane")
    if pane is None:
        print("no settings pane", file=sys.stderr)
        return _EXIT_PRECONDITION
    failures: list[str] = []

    # 1. A staged edit lights its row and its section; SAVE clears both.
    q('settingsPage.showSection("pacing")')
    settle(300)
    was = bool(q('settingsPage.val(settingsPage.fieldByKey("download_delay"))'))
    q(f'settingsPage.setv("download_delay", {"false" if was else "true"})')
    settle(100)
    if not q('settingsPage.fieldDirty(settingsPage.fieldByKey("download_delay"))'):
        failures.append("a staged edit did not mark its row")
    if not q('settingsPage.secDirty("pacing")'):
        failures.append("a staged edit did not light its section's rail dot")
    save = named("settingsSave")
    if save is None:
        failures.append("no SAVE CHANGES button")
    else:
        click(save)
        settle(300)
        if q("settingsPage.dirty"):
            failures.append("SAVE did not take the edit")
        if q('settingsPage.fieldDirty(settingsPage.fieldByKey("download_delay"))'):
            failures.append("the row still reads unsaved after SAVE")
        if q('settingsPage.secDirty("pacing")'):
            failures.append("the rail dot is still lit after SAVE")
        if bool(q('settingsPage.val(settingsPage.fieldByKey("download_delay"))')) == was:
            failures.append("after SAVE the control no longer shows the saved value")

    # 2. A live switch is shown from the edit map but is never unsaved.
    q('settingsPage.showSection("diagnostics")')
    settle(200)
    q('settingsPage.setLive("verbose_diagnostics", true)')
    settle(100)
    if q('settingsPage.fieldDirty(settingsPage.fieldByKey("verbose_diagnostics"))'):
        failures.append("a live diagnostics switch marked its row unsaved")
    if q('settingsPage.secDirty("diagnostics")'):
        failures.append("a live diagnostics switch lit the rail dot")

    # 3. A deep link into the section on show lands on top.
    def scroll_down(sid: str) -> float:
        q(f'settingsPage.showSection("{sid}")')
        settle(400)
        most = max(0.0, float(pane.property("contentHeight")) - float(pane.height()))
        pane.setProperty("contentY", most)
        settle(100)
        return most

    most = scroll_down("downloads")
    if most < 40:
        print(f"downloads does not scroll at the minimum window ({most:.0f}px)", file=sys.stderr)
        return _EXIT_PRECONDITION
    q('settingsPage.jumpToCard("downloads")')
    settle(300)
    if float(pane.property("contentY")) > 1:
        failures.append(f"a deep link into the shown section stayed at {pane.property('contentY'):.0f}px")

    # 4. The same link while Settings is hidden: not the remembered spot either.
    scroll_down("downloads")
    q("root.settingsOpen = false")
    settle(400)
    q('settingsPage.jumpToCard("downloads")')
    q("root.settingsOpen = true")
    settle(500)
    if float(pane.property("contentY")) > 1:
        failures.append(f"a deep link while hidden restored the old spot ({pane.property('contentY'):.0f}px)")

    if failures:
        print("\n".join(failures), file=sys.stderr)
        return _EXIT_REGRESSED
    print("ok")
    return _EXIT_OK


if __name__ == "__main__" and "--run-scenario" in sys.argv:
    sys.exit(_run_scenario())
