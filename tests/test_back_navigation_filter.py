"""The app-level back/forward-navigation event filter.

Binds the real ``WavesBridge.eventFilter`` onto a stub and drives it with fake
events, pinning the contract:
  * mouse back-button and forward-button presses each emit their own signal
    and are consumed,
  * a rapid second press (delivered by Qt as a double-click) also navigates,
    for either button,
  * the matching release is swallowed so it never reaches the view below,
    for either button,
  * the two buttons never cross-emit each other's signal,
  * other buttons and event types pass through untouched,
  * the macOS swipe path stays back-only and non-consuming.
"""

from __future__ import annotations

from conftest import _Signal
from PySide6.QtCore import QEvent, Qt

from tidaler.waves_ui import backend as backend_mod
from tidaler.waves_ui.backend import WavesBridge


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


class _Stub:
    def __init__(self):
        self.backRequested = _Signal()
        self.forwardRequested = _Signal()


def _filter(stub, event):
    return WavesBridge.eventFilter(stub, object(), event)


def test_back_button_press_emits_and_consumes():
    stub = _Stub()
    consumed = _filter(stub, _MouseEvent(QEvent.Type.MouseButtonPress, Qt.MouseButton.BackButton))
    assert consumed is True
    assert len(stub.backRequested.emits) == 1


def test_back_button_double_click_also_navigates():
    stub = _Stub()
    consumed = _filter(stub, _MouseEvent(QEvent.Type.MouseButtonDblClick, Qt.MouseButton.BackButton))
    assert consumed is True
    assert len(stub.backRequested.emits) == 1


def test_back_button_release_swallowed_without_emit():
    stub = _Stub()
    consumed = _filter(stub, _MouseEvent(QEvent.Type.MouseButtonRelease, Qt.MouseButton.BackButton))
    assert consumed is True
    assert stub.backRequested.emits == []


def test_forward_button_press_emits_and_consumes():
    stub = _Stub()
    consumed = _filter(stub, _MouseEvent(QEvent.Type.MouseButtonPress, Qt.MouseButton.ForwardButton))
    assert consumed is True
    assert len(stub.forwardRequested.emits) == 1


def test_forward_button_double_click_also_navigates():
    stub = _Stub()
    consumed = _filter(stub, _MouseEvent(QEvent.Type.MouseButtonDblClick, Qt.MouseButton.ForwardButton))
    assert consumed is True
    assert len(stub.forwardRequested.emits) == 1


def test_forward_button_release_swallowed_without_emit():
    stub = _Stub()
    consumed = _filter(stub, _MouseEvent(QEvent.Type.MouseButtonRelease, Qt.MouseButton.ForwardButton))
    assert consumed is True
    assert stub.forwardRequested.emits == []


def test_back_button_never_emits_forward_requested():
    stub = _Stub()
    _filter(stub, _MouseEvent(QEvent.Type.MouseButtonPress, Qt.MouseButton.BackButton))
    _filter(stub, _MouseEvent(QEvent.Type.MouseButtonDblClick, Qt.MouseButton.BackButton))
    _filter(stub, _MouseEvent(QEvent.Type.MouseButtonRelease, Qt.MouseButton.BackButton))
    assert stub.forwardRequested.emits == []


def test_forward_button_never_emits_back_requested():
    stub = _Stub()
    _filter(stub, _MouseEvent(QEvent.Type.MouseButtonPress, Qt.MouseButton.ForwardButton))
    _filter(stub, _MouseEvent(QEvent.Type.MouseButtonDblClick, Qt.MouseButton.ForwardButton))
    _filter(stub, _MouseEvent(QEvent.Type.MouseButtonRelease, Qt.MouseButton.ForwardButton))
    assert stub.backRequested.emits == []


def test_left_button_passes_through():
    stub = _Stub()
    consumed = _filter(stub, _MouseEvent(QEvent.Type.MouseButtonPress, Qt.MouseButton.LeftButton))
    assert consumed is False
    assert stub.backRequested.emits == []


def test_swipe_emits_but_never_consumes(monkeypatch):
    monkeypatch.setattr(backend_mod, "_IS_MACOS", True)
    stub = _Stub()
    consumed = _filter(stub, _SwipeEvent())
    assert consumed is False
    assert len(stub.backRequested.emits) == 1
    assert stub.forwardRequested.emits == []


def test_swipe_ignored_off_macos(monkeypatch):
    monkeypatch.setattr(backend_mod, "_IS_MACOS", False)
    stub = _Stub()
    consumed = _filter(stub, _SwipeEvent())
    assert consumed is False
    assert stub.backRequested.emits == []
