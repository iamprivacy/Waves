"""Tests for window-geometry persistence in the bridge (issue #6).

Two layers are covered:

  * ``_fit_frame`` (the pure screen-clamp helper) exercised directly with
    synthetic screen layouts, so the "keep the window reachable" math is checked
    without a display, and
  * the ``windowSaveGeometry`` / ``windowRestoreGeometry`` round trip through a
    real ``waves.json`` on disk, exercised through a Qt-free stub that binds the
    real bridge methods onto a plain object (same approach as
    tests/test_ownership_bridge.py). The screen clamp is stubbed to identity in
    the round-trip layer so it does not depend on a real screen; the clamp math
    is covered on its own above.

Like tests/test_ownership_bridge.py these import WavesBridge, so they collect
only in the full runtime venv (PySide6 present).
"""

from __future__ import annotations

import json
import types

import tidaler.waves_ui.backend as backend
from tidaler.waves_ui.backend import WavesBridge, _fit_frame

# A single 1080p screen at the origin, docks/taskbar already excluded.
ONE_SCREEN = [(0, 0, 1920, 1080)]
# Two side-by-side 1080p screens (a second monitor to the right).
TWO_SCREENS = [(0, 0, 1920, 1080), (1920, 0, 1920, 1080)]


# ---------------------------------------------------------------------------
# _fit_frame: the pure screen-clamp helper
# ---------------------------------------------------------------------------


def test_fit_frame_fully_inside_is_unchanged():
    """A frame that already fits is returned untouched (no gratuitous nudging of
    a perfectly good position)."""
    assert _fit_frame((100, 100, 1000, 800), ONE_SCREEN) == (100, 100, 1000, 800)


def test_fit_frame_second_monitor_position_preserved():
    """A frame living entirely on a still-connected second monitor stays there;
    the clamp only intervenes when a frame does not fit."""
    assert _fit_frame((2000, 100, 800, 600), TWO_SCREENS) == (2000, 100, 800, 600)


def test_fit_frame_monitor_gone_recentres_onto_primary():
    """The window was on a second monitor that has since been unplugged: it must
    come back onto the remaining (primary) screen, fully visible."""
    fitted = _fit_frame((2000, 100, 800, 600), ONE_SCREEN)
    assert fitted == (1120, 100, 800, 600)  # x clamped to 1920-800, still on-screen
    _assert_fully_inside(fitted, ONE_SCREEN)


def test_fit_frame_off_right_edge_is_pulled_back():
    """A frame pushed off the right edge (e.g. a resolution that shrank) is
    pulled left so the whole window is reachable."""
    fitted = _fit_frame((3000, 100, 1000, 800), ONE_SCREEN)
    assert fitted == (920, 100, 1000, 800)
    _assert_fully_inside(fitted, ONE_SCREEN)


def test_fit_frame_negative_offset_clamped_onscreen():
    """A frame partly above/left of the origin (its title bar off-screen) is
    clamped so its top-left is visible."""
    fitted = _fit_frame((-200, -100, 800, 600), ONE_SCREEN)
    assert fitted == (0, 0, 800, 600)
    _assert_fully_inside(fitted, ONE_SCREEN)


def test_fit_frame_larger_than_screen_is_shrunk_to_fit():
    """A saved window wider/taller than the current screen (a bigger monitor is
    gone) is capped to the screen so it cannot exceed the visible area."""
    fitted = _fit_frame((0, 0, 1600, 1000), [(0, 0, 1280, 800)])
    assert fitted == (0, 0, 1280, 800)
    _assert_fully_inside(fitted, [(0, 0, 1280, 800)])


def test_fit_frame_no_screens_returns_none():
    """No screen info at all: the caller keeps the saved frame unchanged."""
    assert _fit_frame((100, 100, 800, 600), []) is None


def _assert_fully_inside(frame, screens):
    """Every fitted frame must sit wholly within some screen's available area."""
    x, y, w, h = frame
    assert any(
        sx <= x and y >= sy and x + w <= sx + sw and y + h <= sy + sh for sx, sy, sw, sh in screens
    ), f"{frame} is not fully inside any of {screens}"


# ---------------------------------------------------------------------------
# windowSaveGeometry / windowRestoreGeometry round trip
# ---------------------------------------------------------------------------


class _PrefsStub:
    """Bare stand-in for WavesBridge carrying only the geometry-persistence
    surface, with the real methods bound on and a real waves.json on disk. The
    screen clamp delegates to the real pure _fit_frame against a synthetic
    (display-free) screen list, so the windowRestoreGeometry -> clamp WIRING is
    exercised end-to-end without a display."""

    screens = ONE_SCREEN  # override per-instance to simulate a different layout

    def __init__(self, tmp_path):
        self._waves_prefs_path = str(tmp_path / "waves.json")
        self.save_calls = 0
        for name in (
            "_default_waves_prefs",
            "_load_waves_prefs",
            "_waves_pref_bool",
            "windowSaveGeometry",
            "windowRestoreGeometry",
        ):
            setattr(self, name, getattr(WavesBridge, name).__get__(self, _PrefsStub))
        self._waves_prefs = self._load_waves_prefs()

    # Run the REAL clamp against a synthetic screen list (not identity), so a
    # restore that stopped calling the clamp would be caught.
    def _fit_geometry_to_screens(self, x, y, w, h):
        return _fit_frame((x, y, w, h), self.screens) or (x, y, w, h)

    # Real atomic write, but counted so "only save when changed" is testable.
    def _save_waves_prefs(self):
        self.save_calls += 1
        WavesBridge._save_waves_prefs(self)


def test_fresh_install_has_no_saved_geometry(tmp_path):
    """With nothing saved (the zero win_w sentinel) restore returns an empty
    object, so QML falls back to the default size, OS-placed."""
    stub = _PrefsStub(tmp_path)
    assert stub.windowRestoreGeometry() == {}


def test_save_then_restore_round_trip(tmp_path):
    """A saved frame comes back verbatim through a fresh load (simulating the
    next launch)."""
    _PrefsStub(tmp_path).windowSaveGeometry(120, 140, 1000, 800, False)

    reloaded = _PrefsStub(tmp_path)  # fresh instance reads the file from scratch
    assert reloaded.windowRestoreGeometry() == {
        "x": 120,
        "y": 140,
        "w": 1000,
        "h": 800,
        "maximized": False,
    }


def test_geometry_is_stored_as_ints_not_strings(tmp_path):
    """The dedicated slot must write real ints; routing geometry through the
    generic setWavesPref would str()-coerce them and break restore."""
    _PrefsStub(tmp_path).windowSaveGeometry(10, 20, 900, 700, False)

    data = json.loads((tmp_path / "waves.json").read_text(encoding="utf-8"))
    for key in ("win_x", "win_y", "win_w", "win_h"):
        assert isinstance(data[key], int), f"{key} was stored as {type(data[key])}, not int"
    assert data["win_w"] == 900 and data["win_h"] == 700


def test_maximized_state_persists(tmp_path):
    """The maximized flag round-trips (issue #6 calls this out explicitly)."""
    _PrefsStub(tmp_path).windowSaveGeometry(0, 0, 1400, 900, True)

    assert _PrefsStub(tmp_path).windowRestoreGeometry()["maximized"] is True


def test_zero_size_does_not_clobber_a_good_save(tmp_path):
    """A 0x0 frame (a teardown artefact) must be ignored, never overwrite a
    previously saved frame."""
    stub = _PrefsStub(tmp_path)
    stub.windowSaveGeometry(50, 60, 1100, 850, False)
    stub.windowSaveGeometry(0, 0, 0, 0, False)  # must be a no-op

    assert _PrefsStub(tmp_path).windowRestoreGeometry() == {
        "x": 50,
        "y": 60,
        "w": 1100,
        "h": 850,
        "maximized": False,
    }


def test_unchanged_geometry_does_not_rewrite_the_file(tmp_path):
    """Saving the identical frame twice writes once: the debounced change storm
    must not rewrite waves.json when nothing actually moved."""
    stub = _PrefsStub(tmp_path)
    stub.windowSaveGeometry(30, 40, 1000, 800, False)
    assert stub.save_calls == 1
    stub.windowSaveGeometry(30, 40, 1000, 800, False)  # identical: no write
    assert stub.save_calls == 1
    stub.windowSaveGeometry(30, 40, 1000, 800, True)  # flag changed: writes
    assert stub.save_calls == 2


def test_corrupt_saved_size_falls_back_to_empty(tmp_path):
    """A non-numeric win_w on disk (hand-edited, or a format from a future
    build) must not crash restore; it reads as "nothing saved"."""
    (tmp_path / "waves.json").write_text(json.dumps({"win_w": "oops", "win_h": 800}), encoding="utf-8")
    assert _PrefsStub(tmp_path).windowRestoreGeometry() == {}


def test_restore_clamps_offscreen_frame_onto_a_live_screen(tmp_path):
    """The core issue-#6 guarantee, exercised through the FULL restore path: a
    frame saved off every current screen (a monitor now gone) must come back
    on-screen. This runs windowRestoreGeometry -> _fit_geometry_to_screens (the
    clamp), so dropping that call in a refactor fails here even though the frame
    round-trips byte-for-byte."""
    _PrefsStub(tmp_path).windowSaveGeometry(3000, 100, 1000, 800, False)  # x=3000 is off a 1920 screen
    r = _PrefsStub(tmp_path).windowRestoreGeometry()
    assert (r["x"], r["y"], r["w"], r["h"]) == (920, 100, 1000, 800)
    _assert_fully_inside((r["x"], r["y"], r["w"], r["h"]), ONE_SCREEN)


def test_unparseable_prefs_file_falls_back_to_defaults(tmp_path):
    """A syntactically broken waves.json (hand-edited, or truncated by a process
    death mid-write, the exact case the atomic write in _save_waves_prefs
    defends against) must load as defaults, not crash startup. This pins the
    JSONDecodeError arm of _load_waves_prefs, distinct from the missing-file arm
    covered by test_fresh_install_has_no_saved_geometry."""
    (tmp_path / "waves.json").write_text("{ not valid json", encoding="utf-8")
    stub = _PrefsStub(tmp_path)
    assert stub._waves_prefs["win_w"] == 0  # fell back to the default schema
    assert stub.windowRestoreGeometry() == {}


# ---------------------------------------------------------------------------
# The live-screen glue: _fit_geometry_to_screens reads QScreen.availableGeometry
# ---------------------------------------------------------------------------
# _fit_geometry_to_screens is the only code that touches the real QScreen layout.
# It does not use self, so we drive it unbound with a fake QtGui module patched
# in, checking the three decisions the pure _fit_frame tests cannot see: that it
# reads availableGeometry (not full geometry), builds the screen tuple in
# (x, y, width, height) order, and falls back to the input when no screens exist.


class _FakeRect:
    def __init__(self, x, y, w, h):
        self._v = (x, y, w, h)

    def x(self):
        return self._v[0]

    def y(self):
        return self._v[1]

    def width(self):
        return self._v[2]

    def height(self):
        return self._v[3]


class _FakeScreen:
    def __init__(self, avail):
        self._avail = avail

    def availableGeometry(self):
        return self._avail


def _patch_screens(monkeypatch, screens):
    monkeypatch.setattr(
        backend,
        "QtGui",
        types.SimpleNamespace(QGuiApplication=types.SimpleNamespace(screens=lambda: screens)),
    )


def test_fit_geometry_reads_available_not_full(monkeypatch):
    """It must clamp against availableGeometry (dock/taskbar excluded). With a
    40px bottom taskbar the usable height is 1040, so a low window is pulled up
    to sit above it; using the full 1080 geometry instead would leave it under
    the taskbar."""
    _patch_screens(monkeypatch, [_FakeScreen(_FakeRect(0, 0, 1920, 1040))])
    x, y, w, h = WavesBridge._fit_geometry_to_screens(None, 0, 1030, 1000, 40)
    assert y + h <= 1040


def test_fit_geometry_tuple_order_not_transposed(monkeypatch):
    """The screen tuple must be (x, y, width, height): a 1500-wide window fits a
    1920-wide screen and stays put. If width/height were swapped, the clamp
    would think the screen is 1080 wide and shrink the window."""
    _patch_screens(monkeypatch, [_FakeScreen(_FakeRect(0, 0, 1920, 1080))])
    assert WavesBridge._fit_geometry_to_screens(None, 100, 100, 1500, 200) == (100, 100, 1500, 200)


def test_fit_geometry_no_screens_returns_input(monkeypatch):
    """No connected screens: the saved frame is returned unchanged rather than
    blocking the restore (windowRestoreGeometry still hands QML something sane)."""
    _patch_screens(monkeypatch, [])
    assert WavesBridge._fit_geometry_to_screens(None, 5, 6, 7, 8) == (5, 6, 7, 8)
