"""The momentum guard laid over a page never costs its buttons the pointing hand.

A MouseArea owns the cursor over everything it covers even when it takes no
buttons and names no cursorShape (Qt 6.11), so the guard's overlay turned
every hand under it into an arrow: the Settings buttons, the preview and
download buttons, everything inside a scrolling page, while the top bar
outside any page kept its hand. QML cannot give the claim back, so the guard
wears the cursor of whatever is under the pointer, tracked by a HoverHandler
on the page itself: one on the guard (or any hover of the guard's own) would
starve the rows beneath of their hover, which the hover-prefetch scenario
depends on.

The scene below runs Main.qml's own guard over a hover-enabled button and
proves both halves at once: the window shows the hand over the button, and
the button still sees the pointer. It also shows the I-beam over a text
field (TextInput names no cursorShape) and no hand over a disabled button,
as Qt itself does. Runs in a SUBPROCESS like the other Main.qml
scenarios (offscreen Qt, a sandboxed config dir).
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

QML_MAIN = Path(__file__).resolve().parent.parent / "waves" / "waves_ui" / "qml" / "Main.qml"

_EXIT_OK = 0
_EXIT_FAIL = 1
_EXIT_NO_QT = 3


def _guard_block() -> str:
    src = QML_MAIN.read_text(encoding="utf-8")
    start = src.index("    component MomentumGuard: MouseArea {")
    return src[start : src.index("\n    }\n", start) + len("\n    }")]


def test_the_guard_wears_the_cursor_of_what_is_beneath() -> None:
    block = _guard_block()
    assert re.search(r"cursorShape:\s*under", block), "the guard's cursorShape must follow `under`"
    assert "HoverHandler {" in block, "the guard needs a HoverHandler to follow the pointer"
    assert re.search(r"HoverHandler \{[^}]*parent:\s*guard\.flick", block, re.S), (
        "the guard's HoverHandler must sit on the page (parent: guard.flick): on the guard "
        "it starves the rows beneath of their hover"
    )
    assert "hoverEnabled: true" not in block, "the guard's own hover would block the rows beneath"
    assert (
        "childAt(" not in block.split("function inner")[0]
    ), "childAt's single topmost item misses an area under a sibling label: walk every child"


def test_a_hand_under_the_guard_shows_through_and_the_button_keeps_its_hover() -> None:
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="waves-guard-cursor-test-")
    proc = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), "--run-scenario"],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    tail = "\n".join((proc.stdout + proc.stderr).strip().splitlines()[-8:])
    if proc.returncode == _EXIT_NO_QT:
        pytest.skip("PySide6 / offscreen Qt unavailable")
    assert proc.returncode == _EXIT_OK, f"the guard shape lost the hand or the hover:\n{tail}"


# The scene: Main.qml's own guard component (read from the file, so the test
# can never drift from it) over a hover-enabled button whose label sits above
# its area, a text field, and a button that is disabled (busy).
_SCENE = """
import QtQuick
import QtQuick.Window
Window {
    width: 300; height: 300; visible: true
%s
    Flickable {
        id: flick; anchors.fill: parent; contentHeight: 600
        Rectangle {
            x: 50; y: 50; width: 100; height: 40; color: "green"
            MouseArea {
                objectName: "btn"; anchors.fill: parent
                hoverEnabled: true; cursorShape: Qt.PointingHandCursor
            }
            // The label sits ABOVE the area, as every house button's does.
            Text { anchors.centerIn: parent; text: "LABEL" }
        }
        TextInput { x: 50; y: 120; width: 160; height: 30; text: "a path" }
        Rectangle {
            x: 50; y: 180; width: 100; height: 40; color: "gray"
            enabled: false
            MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor }
        }
    }
    MomentumGuard { flick: flick }
}
"""


def _run_scenario() -> int:
    try:
        from PySide6.QtCore import QPoint, Qt, QTimer, QUrl
        from PySide6.QtGui import QGuiApplication
        from PySide6.QtQml import QQmlApplicationEngine
        from PySide6.QtQuick import QQuickItem
        from PySide6.QtTest import QTest
    except Exception as exc:  # pragma: no cover - environment guard
        print(f"PySide6 unavailable: {exc}", file=sys.stderr)
        return _EXIT_NO_QT
    app = QGuiApplication(sys.argv)
    qml = Path(tempfile.mkdtemp(prefix="waves-guard-cursor-scene-")) / "scene.qml"
    qml.write_text(_SCENE % _guard_block(), encoding="utf-8")
    engine = QQmlApplicationEngine()
    engine.load(QUrl.fromLocalFile(str(qml)))
    roots = engine.rootObjects()
    if not roots:
        print("scene failed to load", file=sys.stderr)
        return _EXIT_FAIL
    win = roots[0]
    btn = win.findChild(QQuickItem, "btn")
    state = {"rc": _EXIT_FAIL}

    def probe() -> None:
        QTest.mouseMove(win, QPoint(100, 140))
        app.processEvents()
        QTest.mouseMove(win, QPoint(100, 70))  # onto the button, over its label
        app.processEvents()
        QTest.mouseMove(win, QPoint(101, 71))
        app.processEvents()
        shape = win.cursor().shape()
        hovered = bool(btn.property("containsMouse"))
        print(f"over the button: cursor={shape.name} button hovered={hovered}")
        QTest.mouseMove(win, QPoint(100, 100))  # off it again
        app.processEvents()
        back = win.cursor().shape()
        print(f"off the button: cursor={back.name}")
        QTest.mouseMove(win, QPoint(100, 135))  # over the text field
        app.processEvents()
        QTest.mouseMove(win, QPoint(101, 136))
        app.processEvents()
        text = win.cursor().shape()
        print(f"over the text field: cursor={text.name}")
        QTest.mouseMove(win, QPoint(100, 200))  # over the disabled button
        app.processEvents()
        QTest.mouseMove(win, QPoint(101, 201))
        app.processEvents()
        busy = win.cursor().shape()
        print(f"over the disabled button: cursor={busy.name}")
        ok = (
            shape == Qt.CursorShape.PointingHandCursor
            and hovered
            and back == Qt.CursorShape.ArrowCursor
            and text == Qt.CursorShape.IBeamCursor
            and busy == Qt.CursorShape.ArrowCursor
        )
        state["rc"] = _EXIT_OK if ok else _EXIT_FAIL
        app.quit()

    QTimer.singleShot(300, probe)
    app.exec()
    return state["rc"]


if __name__ == "__main__" and "--run-scenario" in sys.argv:
    sys.exit(_run_scenario())
