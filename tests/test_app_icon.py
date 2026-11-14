"""Guards for the packaged app icon.

A truncated ``icon.ico`` (a single 16x16 frame) once shipped and gave Windows a
generic taskbar icon: Nuitka brands the EXE from ``icon.ico``, and the taskbar
needs a >=32px frame it can't get from a 16-only file. These tests fail the
build if the committed icon regresses, and pin the runtime "is this icon usable"
guard so a degenerate icon can never again slip through to ``setWindowIcon``.
"""

from __future__ import annotations

import struct
from pathlib import Path

import pytest

_ICO = Path(__file__).resolve().parent.parent / "waves" / "ui" / "icon.ico"
_EXPECTED_SIZES = {16, 32, 48, 64, 128, 256}


def _ico_frames(path: Path) -> list[tuple[int, int]]:
    data = path.read_bytes()
    reserved, kind, count = struct.unpack_from("<HHH", data, 0)
    assert reserved == 0 and kind == 1, f"{path} is not an ICO file"
    frames = []
    for i in range(count):
        w, h = struct.unpack_from("<BB", data, 6 + i * 16)[0:2]
        frames.append((w or 256, h or 256))
    return frames


def test_icon_ico_has_full_size_ladder():
    """The committed icon.ico must carry every taskbar-relevant frame.

    This is the exact regression that shipped in v0.1.7 (a 692-byte, single
    16x16 file); a build from a tree that fails this test must not go out.
    """
    assert _ICO.is_file(), f"missing {_ICO}"
    sizes = {w for w, _ in _ico_frames(_ICO)}
    assert sizes >= _EXPECTED_SIZES, f"icon.ico is missing sizes {_EXPECTED_SIZES - sizes}; has {sorted(sizes)}"
    assert max(sizes) >= 256, "icon.ico needs a 256px frame for high-DPI surfaces"


def test_icon_usable_guard_rejects_degenerate_icons():
    """The runtime guard must reject a 16-only / empty icon and accept a good one."""
    QGuiApplication = pytest.importorskip("PySide6.QtGui").QGuiApplication
    QIcon = pytest.importorskip("PySide6.QtGui").QIcon
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    _ = QGuiApplication.instance() or QGuiApplication([])

    from waves.waves_ui.app import _icon_usable

    good = QIcon(str(_ICO))
    assert _icon_usable(good) is True

    empty = QIcon()
    empty.addFile("/does/not/exist.png")  # the non-null-but-empty footgun
    assert _icon_usable(empty) is False

    assert _icon_usable(QIcon()) is False


def _qt_app():
    QtGui = pytest.importorskip("PySide6.QtGui")
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    return QtGui.QGuiApplication.instance() or QtGui.QGuiApplication([])


def test_mac_icon_never_comes_from_the_full_bleed_png_ladder(monkeypatch):
    """On macOS setWindowIcon replaces the Dock icon, so it must be the grid-fit icns.

    The PNG ladder holds the full-bleed art for Windows and Linux; handing it to
    the Dock made the running app swap its bundle icon for that art.
    """
    _ = _qt_app()
    from waves.waves_ui import app as app_mod

    monkeypatch.setattr(app_mod.sys, "platform", "darwin")
    real_qicon = app_mod.QIcon
    seen: list[str] = []

    def recording_qicon(*args):
        seen.extend(str(a) for a in args)
        return real_qicon(*args)

    monkeypatch.setattr(app_mod, "QIcon", recording_qicon)
    icon = app_mod._app_icon()
    assert icon is not None  # source run: waves/ui/icon.icns
    assert seen and all(p.endswith("icon.icns") for p in seen)


def test_mac_bundle_without_icns_leaves_the_bundle_icon(monkeypatch, tmp_path):
    """A packaged bundle ships no icon.icns beside the binary: set no icon at all."""
    _ = _qt_app()
    from waves.waves_ui import app as app_mod

    monkeypatch.setattr(app_mod.sys, "platform", "darwin")
    monkeypatch.setattr(app_mod, "__file__", str(tmp_path / "waves" / "waves_ui" / "app.py"))
    assert app_mod._app_icon() is None
