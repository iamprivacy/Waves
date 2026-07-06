"""Entry point for the Waves QML UI.

Launch with::

    python -m tidaler.waves_ui

or import :func:`waves_activate` and call it (optionally passing an existing
``Tidal`` session).
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtGui import QFontDatabase, QGuiApplication, QIcon, QWindow
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkDiskCache
from PySide6.QtQml import QQmlApplicationEngine, QQmlNetworkAccessManagerFactory

from tidaler.config import Tidal

from . import proc
from .backend import WavesBridge

_QML_MAIN = Path(__file__).parent / "qml" / "Main.qml"
_FONT_DIR = Path(__file__).parent / "fonts"


class _ArtCacheFactory(QQmlNetworkAccessManagerFactory):
    """Give the QML image loader an HTTP disk cache.

    Every ``Image`` in the UI fetches cover art through the engine's network
    manager, which by default has NO cache, so each launch re-downloaded
    every cover it showed. TIDAL's image CDN serves content-addressed URLs
    with long cache lifetimes, so a modest disk cache makes search results,
    browse shelves and tile mosaics paint instantly from local storage on
    every launch after the first, spending zero network on repeat art."""

    def __init__(self, cache_dir: str) -> None:
        super().__init__()
        self._cache_dir = cache_dir

    def create(self, parent) -> QNetworkAccessManager:
        nam = QNetworkAccessManager(parent)
        cache = QNetworkDiskCache(nam)
        cache.setCacheDirectory(self._cache_dir)
        cache.setMaximumCacheSize(120 * 1024 * 1024)  # ~120 MB of covers
        nam.setCache(cache)
        return nam


def _load_mono() -> str:
    """Register the bundled monospace font and return its family name.

    The Console UI uses a monospace face for numeric readouts, the ASCII
    download bar (█/░) and the ASCII wave logo. Bundling JetBrains Mono (OFL)
    guarantees identical rendering and block-glyph coverage across platforms;
    if the files are missing we fall back to the platform's generic monospace.
    """
    families: list[str] = []
    for name in ("JetBrainsMono-Regular.ttf", "JetBrainsMono-Bold.ttf"):
        font_id = QFontDatabase.addApplicationFont(str(_FONT_DIR / name))
        if font_id != -1:
            families += QFontDatabase.applicationFontFamilies(font_id)
    return families[0] if families else "Monospace"


def _icon_usable(icon: QIcon) -> bool:
    """True only if ``icon`` carries a frame the Windows taskbar can actually use.

    The taskbar asks for ~32-48px and Qt never *upscales* a QIcon, so a 16x16-only
    icon (or the non-null-but-empty icon you get from ``addFile`` on a missing
    path) yields a 16px pixmap that Windows rejects in favour of a generic glyph.
    Requiring a >=32px frame refuses those degenerate icons so we can leave the
    EXE-embedded resource icon standing instead of blanking the taskbar with a
    bad ``setWindowIcon``. (A truncated single-frame ``icon.ico`` shipped once and
    caused exactly this.)
    """
    return not icon.isNull() and any(s.width() >= 32 for s in icon.availableSizes())


def _icon_debug(msg: str) -> None:
    """Emit an icon-resolution diagnostic when ``WAVES_DEBUG`` is set.

    The packaged Windows build is console-less (``--windows-console-mode=disable``),
    so a stderr line is invisible there. Mirror the line to ``waves-icon-debug.log``
    in the user's home dir so a failing taskbar icon can be diagnosed on a real
    machine without a console. Best-effort: never let logging break startup.
    """
    print(msg, file=sys.stderr)
    try:
        with open(Path.home() / "waves-icon-debug.log", "a", encoding="utf-8") as fh:
            fh.write(msg + "\n")
    except Exception:
        logging.getLogger(__name__).debug("icon debug log write failed", exc_info=True)


def _app_icon() -> QIcon | None:
    """Window/taskbar icon for the running app.

    The Nuitka ``--windows-icon-from-ico`` / ``--macos-app-icon`` flags only
    brand the executable (what Explorer/Finder show); the live window's icon is
    Qt's to set. We build from the individual PNG size ladder rather than the
    single ``icon.ico`` blob: the PNGs are addressed per size, so one bad frame
    can't cripple every surface the way a truncated ``.ico`` did. The ``.ico`` is
    only a fallback, and whatever we return must pass :func:`_icon_usable`.
    """
    debug = bool(os.environ.get("WAVES_DEBUG"))
    roots = (
        Path(sys.executable).resolve().parent / "ui",  # packaged: data files beside the binary
        Path(sys.argv[0]).resolve().parent / "ui",  # Nuitka: sys.executable is a phantom python.exe
        Path(__file__).resolve().parent.parent / "ui",  # from source: tidaler/ui
    )
    for root in roots:
        icon: QIcon | None = None
        source = ""
        pngs = sorted(root.glob("icon*.png"))
        if pngs:
            icon = QIcon()
            for png in pngs:
                icon.addFile(str(png))
            source = "png"
        else:
            ico = root / "icon.ico"
            if ico.is_file():
                icon = QIcon(str(ico))
                source = "ico"
        if icon is not None and _icon_usable(icon):
            if debug:
                sizes = sorted(s.width() for s in icon.availableSizes())
                _icon_debug(f"WAVES icon: root={root} source={source} sizes={sizes} px48={icon.pixmap(48, 48).width()}")
            return icon
    if debug:
        _icon_debug("WAVES icon: no usable icon found in any root")
    return None


def _ui_font() -> str:
    """Family for button/tab labels: the platform's native UI sans.

    The Console button spec (re-chosen in the Button Lab, 2026-07) sets labels
    in the system sans, SF on macOS, Segoe on Windows, the desktop default on
    Linux, so buttons read native everywhere with nothing to bundle.
    """
    return QGuiApplication.font().family()


def waves_activate(tidal: Tidal | None = None) -> int:
    # Download conversions go through python-ffmpeg, which would flash a
    # console window per spawn on the console-less Windows build.
    proc.silence_python_ffmpeg()
    if sys.platform == "win32":
        # Give the taskbar an explicit AppUserModelID BEFORE the first window is
        # created. Without this, a Qt app's taskbar button is generic even when
        # the EXE itself carries a valid icon (Explorer shows it, the taskbar
        # doesn't); the running button's icon is resolved through the process
        # AUMID, not the EXE resource. This must run in the PACKAGED build too:
        # Nuitka does NOT set an AUMID (it only writes company/product into the
        # VERSION resource), so gating this to from-source runs left every frozen
        # build with a generic taskbar icon. is_frozen() no longer gates it.
        try:
            import ctypes

            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Waves.Waves")
            if os.environ.get("WAVES_DEBUG"):
                _icon_debug("WAVES aumid: set Waves.Waves")
        except Exception:
            logging.getLogger(__name__).debug("could not set AppUserModelID", exc_info=True)
            if os.environ.get("WAVES_DEBUG"):
                _icon_debug("WAVES aumid: FAILED to set")
    owns_app = QGuiApplication.instance() is None
    app = QGuiApplication.instance() or QGuiApplication(sys.argv)
    app.setApplicationName("Waves")
    app.setOrganizationName("Waves")
    icon = _app_icon()
    if icon is not None:
        app.setWindowIcon(icon)

    engine = QQmlApplicationEngine()
    bridge = WavesBridge(tidal=tidal)
    # HTTP disk cache for artwork (must be installed before the QML loads).
    art_cache = _ArtCacheFactory(os.path.join(os.path.dirname(bridge.settings.file_path), "art_cache"))
    engine.setNetworkAccessManagerFactory(art_cache)
    app._waves_art_cache = art_cache  # type: ignore[attr-defined]  # keep alive
    engine.rootContext().setContextProperty("waves", bridge)
    # Monospace family for the QML layer (numeric readouts + ASCII art).
    engine.rootContext().setContextProperty("monoFont", _load_mono())
    # UI-label family for buttons/tabs (Console button spec).
    engine.rootContext().setContextProperty("uiFontFamily", _ui_font())
    # Keep a reference so it isn't garbage-collected.
    app._waves_bridge = bridge  # type: ignore[attr-defined]
    # Non-consuming filter for the back-swipe gesture (won't affect scrolling).
    app.installEventFilter(bridge)
    # Abort downloads and drain the worker pools before the Qt object graph is
    # torn down, otherwise quitting mid-download hangs in QThreadPool teardown.
    app.aboutToQuit.connect(bridge.shutdown)

    engine.load(QUrl.fromLocalFile(str(_QML_MAIN)))
    root_objects = engine.rootObjects()
    if not root_objects:
        print("Failed to load Waves QML UI", file=sys.stderr)
        return 1

    # Also set the icon on the actual top-level window, not just the application
    # default. app.setWindowIcon only sets a fallback that a Nuitka-compiled
    # PySide6 build may fail to surface to the Windows taskbar; QWindow.setIcon
    # is the per-window API the taskbar reads directly.
    if icon is not None and isinstance(root_objects[0], QWindow):
        root_objects[0].setIcon(icon)
    if os.environ.get("WAVES_DEBUG"):
        _icon_debug(
            f"WAVES window: root_type={type(root_objects[0]).__name__} "
            f"is_qwindow={isinstance(root_objects[0], QWindow)} icon_set={icon is not None}"
        )

    rc = app.exec()
    if owns_app:
        # Standalone launch: background workers (e.g. the search popularity
        # enrichment that fires a request per artist, or a download) may still
        # be parked in a network read, and on macOS the QML render thread can
        # deadlock during teardown, either makes a normal exit hang until the
        # user force-quits. State is already persisted and downloads are aborted
        # (shutdown(), via aboutToQuit), so skip the fragile C++ teardown.
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(rc)
    return rc


if __name__ == "__main__":
    raise SystemExit(waves_activate())
