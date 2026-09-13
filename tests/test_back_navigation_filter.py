"""Back navigation input: the macOS swipe filter and the side-button MouseArea.

The bridge's event filter handles ONE thing now, the discrete macOS
three-finger swipe (NativeGesture), and it is installed on the window's
content item, never on the window or the application. A Python event filter
is a crossing into the interpreter for every event its target receives; on
the window that was one per frame (the render loop's update request), each
waiting for the interpreter behind the launch workers, sampled 2026-09-11 as
the largest single GUI-thread cost while the landing built under the boot
water. The content item receives only what no item under the pointer
accepted, which is the swipe and nothing else while the scene is idle.

The mouse back and forward side buttons moved to a MouseArea at the top of
the scene (Main.qml), pinned here by text: it accepts exactly those two
buttons, so every other press, wheel and hover passes through to the page.

The window's activate and deactivate events are no longer swallowed: the
per-item walk they trigger cost ~0.3-0.5 s only while an application-wide
filter made every hop cross into Python, which no filter does any more.
"""

from __future__ import annotations

import pathlib
import re

from conftest import _Signal
from PySide6.QtCore import QEvent, Qt

from waves.waves_ui import backend as backend_mod
from waves.waves_ui.backend import WavesBridge

_ROOT = pathlib.Path(__file__).resolve().parents[1]
_MAIN_QML = _ROOT / "waves" / "waves_ui" / "qml" / "Main.qml"
_APP_PY = _ROOT / "waves" / "waves_ui" / "app.py"


class _MouseEvent:
    def __init__(self, etype, button):
        self._etype = etype
        self._button = button

    def type(self):
        return self._etype

    def button(self):
        return self._button


class _SwipeEvent:
    def __init__(self, value=1.0):
        self._value = value

    def type(self):
        return QEvent.Type.NativeGesture

    def gestureType(self):
        return Qt.NativeGestureType.SwipeNativeGesture

    def value(self):
        return self._value


class _PlainEvent:
    def __init__(self, etype):
        self._etype = etype

    def type(self):
        return self._etype


class _Stub:
    def __init__(self):
        self.backRequested = _Signal()
        self.forwardRequested = _Signal()


def _filter(stub, event):
    return WavesBridge.eventFilter(stub, object(), event)


def test_swipe_emits_but_never_consumes(monkeypatch):
    monkeypatch.setattr(backend_mod, "_IS_MACOS", True)
    stub = _Stub()
    consumed = _filter(stub, _SwipeEvent(1.0))
    assert consumed is False
    assert len(stub.backRequested.emits) == 1
    assert stub.forwardRequested.emits == []


def test_swipe_ignored_off_macos(monkeypatch):
    monkeypatch.setattr(backend_mod, "_IS_MACOS", False)
    stub = _Stub()
    consumed = _filter(stub, _SwipeEvent(1.0))
    assert consumed is False
    assert stub.backRequested.emits == []


def test_swipe_the_other_way_is_not_back(monkeypatch):
    monkeypatch.setattr(backend_mod, "_IS_MACOS", True)
    stub = _Stub()
    assert _filter(stub, _SwipeEvent(-1.0)) is False
    assert stub.backRequested.emits == []


def test_mouse_buttons_and_window_events_pass_through_the_filter(monkeypatch):
    """The filter no longer reads any of these: the side buttons are the
    MouseArea's (below), and activation events are left to Qt."""
    monkeypatch.setattr(backend_mod, "_IS_MACOS", True)
    stub = _Stub()
    for ev in (
        _MouseEvent(QEvent.Type.MouseButtonPress, Qt.MouseButton.BackButton),
        _MouseEvent(QEvent.Type.MouseButtonPress, Qt.MouseButton.ForwardButton),
        _MouseEvent(QEvent.Type.MouseButtonPress, Qt.MouseButton.LeftButton),
        _PlainEvent(QEvent.Type.WindowActivate),
        _PlainEvent(QEvent.Type.WindowDeactivate),
        _PlainEvent(QEvent.Type.UpdateRequest),
    ):
        assert _filter(stub, ev) is False
    assert stub.backRequested.emits == []
    assert stub.forwardRequested.emits == []


def test_filter_is_installed_on_the_content_item_not_the_window():
    src = _APP_PY.read_text(encoding="utf-8")
    assert "root_objects[0].installEventFilter(bridge)" not in src
    assert "app.installEventFilter(bridge)" not in src
    assert re.search(
        r"contentItem\(\).*\n.*installEventFilter\(bridge\)", src
    ), "the swipe filter belongs on the content item"


def test_side_buttons_are_a_root_tap_handler_accepting_only_those_two():
    qml = _MAIN_QML.read_text(encoding="utf-8")
    m = re.search(
        r"Item \{\s*\n\s*anchors\.fill: parent\s*\n\s*z: 1000000\s*\n\s*TapHandler \{\s*\n"
        r"\s*acceptedButtons: Qt\.BackButton \| Qt\.ForwardButton\s*\n(?P<body>.*?)\n\s*\}\s*\n\s*\}",
        qml,
        re.S,
    )
    assert m, "the root back/forward handler is gone or changed shape"
    body = m.group("body")
    assert "root.navBack()" in body and "root.navForward()" in body
    # A handler on a plain Item: no MouseArea at the top of the scene may
    # take the cursor or the hover from the page beneath (the boot shield's
    # cursor release scenario caught exactly that).
    assert "MouseArea" not in body and "cursorShape" not in body and "hoverEnabled" not in body
    # No other handler or area may claim the side buttons for itself.
    others = [x for x in re.findall(r"acceptedButtons:[^\n]*", qml) if "BackButton" in x or "ForwardButton" in x]
    assert others == ["acceptedButtons: Qt.BackButton | Qt.ForwardButton"], others
