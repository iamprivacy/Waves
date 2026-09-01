"""Entry point for the Waves QML UI.

Launch with::

    python -m waves.waves_ui

or import :func:`waves_activate` and call it (optionally passing an existing
``Tidal`` session).
"""

from __future__ import annotations

import faulthandler
import gc
import logging
import os
import sys
import threading
import time
import traceback
from pathlib import Path

from PySide6.QtCore import QTimer, QUrl
from PySide6.QtGui import QFontDatabase, QGuiApplication, QIcon, QWindow
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkDiskCache, QNetworkRequest, QSslConfiguration
from PySide6.QtQml import QQmlApplicationEngine, QQmlIncubationController, QQmlNetworkAccessManagerFactory

from waves.config import Tidal

from . import diagnostics, proc
from .backend import _ART_CACHE_DIR, WavesBridge


def _data_dir(name: str) -> Path:
    """The bundled ``qml``/``fonts`` directory, wherever this run keeps it.

    From source it sits beside this file. A packaged build cannot ship it
    there: next to the binary, a ``waves/`` data directory would collide with
    the ``Waves`` executable on the case-insensitive filesystems macOS and
    Windows ship with, so the build lands the data at ``waves_ui/<name>``
    beside the binary instead (the --include-data-dir directives in the
    repo-root waves.py). Candidate order mirrors the window-icon lookup
    below: source first, then the packaged locations.
    """
    for root in (
        Path(__file__).parent,  # from source: waves/waves_ui/
        Path(sys.argv[0]).resolve().parent / "waves_ui",  # packaged: beside the binary
        Path(sys.executable).resolve().parent / "waves_ui",  # Nuitka phantom-python fallback
    ):
        candidate = root / name
        if candidate.is_dir():
            return candidate
    return Path(__file__).parent / name


_QML_MAIN = _data_dir("qml") / "Main.qml"
_FONT_DIR = _data_dir("fonts")


class _CacheFirstNAM(QNetworkAccessManager):
    """A network manager that trusts its disk cache for cover art.

    TIDAL cover URLs are content-addressed: a given URL always yields the exact
    same bytes, so a cached cover can never go stale. Yet the default policy
    (``PreferNetwork``) still checks freshness with the CDN on every launch, a
    round-trip per cover, which is why a page of already-downloaded covers can
    still sit on the loading placeholder at startup. Forcing ``PreferCache`` on
    GETs serves a cached cover straight from disk with no network hop; only a
    cover we have never fetched goes to the network. Safe precisely because the
    URLs are immutable."""

    def createRequest(self, op, request, outgoingData=None):
        if op == QNetworkAccessManager.Operation.GetOperation:
            request.setAttribute(
                QNetworkRequest.Attribute.CacheLoadControlAttribute,
                QNetworkRequest.CacheLoadControl.PreferCache,
            )
        # Plain HTTP/1.1 for cover fetches. Qt defaults to HTTP/2, which
        # multiplexes a whole page of covers onto one connection; under load
        # Qt's H2 client was observed desyncing ("HEADERS on invalid stream",
        # then "HTTP/2 protocol error") and killing every art request in
        # flight at once (8 streams in one burst in the 2026-07-13
        # diagnostics export). HTTP/1.1 gives covers ~6 independent
        # connections instead: no shared-connection failure mode, and during
        # audio downloads those extra TCP flows also claim a fairer share of
        # the link for the small image payloads.
        request.setAttribute(QNetworkRequest.Attribute.Http2AllowedAttribute, False)
        return super().createRequest(op, request, outgoingData)


def _warm_tls() -> None:
    """Pay Qt's one-time TLS setup on a background thread at launch.

    The first HTTPS request in the process makes QtNetwork build its default
    QSslConfiguration: the system CA store is read (~160 certificates from
    the keychain on macOS), ~90-100ms measured. Left alone that happened
    inside the FIRST cover-art request, i.e. inside _CacheFirstNAM's Python
    createRequest override on the QML network thread, with the interpreter
    held for the whole call: sampled live as a ~110ms GUI-thread stall
    landing right in the launch animation. Here it runs on its own thread
    while the QML is still loading, and PySide releases the interpreter for
    the call, so nothing waits on it. If a request outruns it the override
    simply pays as before."""
    try:
        QSslConfiguration.defaultConfiguration()
    except Exception:
        logging.getLogger(__name__).debug("TLS warm-up failed", exc_info=True)


class _ArtCacheFactory(QQmlNetworkAccessManagerFactory):
    """Give the QML image loader a cache-first HTTP disk cache.

    Every ``Image`` in the UI fetches cover art through the engine's network
    manager, which by default has NO cache, so each launch re-downloaded every
    cover it showed. A disk cache plus the cache-first policy (see
    :class:`_CacheFirstNAM`) makes search results, browse shelves and tile
    mosaics paint from local storage on every launch after the first, spending
    zero network on repeat art. The cache is sized to hold far more than one
    browsing session's covers so they do not evict (and re-download) each
    other, small thumbnails at ~tens of KB each fit tens of thousands in 1 GB."""

    def __init__(self, cache_dir: str) -> None:
        super().__init__()
        self._cache_dir = cache_dir

    def create(self, parent) -> QNetworkAccessManager:
        nam = _CacheFirstNAM(parent)
        cache = QNetworkDiskCache(nam)
        cache.setCacheDirectory(self._cache_dir)
        # ~1 GB. Covers are tens of KB each, so this holds tens of thousands:
        # a season of browsing rather than one session. Measured at the old
        # 256 MB the cache ran full (9,233 covers) and had already evicted
        # nearly half the covers of pages the user had visited, so those pages
        # went back to the network (and back to the placeholder) on the next
        # visit. This is disk only: the in-memory warm pool that keeps recently
        # shown covers decoded is a separate, much smaller budget (Main.qml).
        cache.setMaximumCacheSize(1024 * 1024 * 1024)
        nam.setCache(cache)
        return nam


class _BootPacedIncubation(QQmlIncubationController):
    """Incubation pacing that keeps the boot water smooth.

    Asynchronous Loaders incubate through the engine's controller, and the
    default one (the window's) drives incubation as hard as the frame loop
    allows. During launch that meant the landing's ~130 shelf and card
    incubations completed back to back, ~10-16 ms each: the GUI thread's
    event loop never idled, the render loop missed its sync slot for
    hundreds of milliseconds at a stretch, and the only visible motion on
    screen (the boot water) froze with it (probe: presentation gaps up to
    356 ms mid-hold, with no single chunk crossing the 40 ms stall
    detector).

    Installed on the engine BEFORE the QML loads (so the window never
    installs its own). While the launch overlay is up it incubates in small
    slices on a timer, leaving the GUI thread free for roughly half of every
    frame, so the water holds its rate while the landing assembles slightly
    slower (the opening hold and the handover gate already absorb exactly
    this). The bridge's bootRevealed() opens the throttle for the rest of
    the session: a near-continuous slice per tick, the pre-existing pace, so
    tab strikes and page builds keep their speed.

    The timer runs unconditionally for the whole boot window (an empty
    incubateFor is near-free), and at release the engine is handed back to
    the window's own stock controller, so this class drives incubation only
    while the launch look is up. Nothing here may depend on the
    incubatingObjectCountChanged virtual firing: the first shipped version
    started its timer from that virtual, whose binding passes the new count
    as an argument, and the no-arg override raised TypeError on every call.
    No timer ever started, no async Loader in the whole app could complete,
    and the launch revealed a blank, dead landing.
    """

    _BOOT_SLICE_MS = 12
    _OPEN_SLICE_MS = 50

    def __init__(self, timer_parent) -> None:
        super().__init__()
        self._boot = True
        self._released = False
        self._notify = None
        self._handback = None
        self._timer = QTimer(timer_parent)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

    def set_count_notifier(self, fn) -> None:
        """The bridge learns the live incubation count (bootIncubationBusy):
        under boot pacing the landing's cards finish registering into the
        veil count only after it has already settled, so the reveal gate
        reads THIS count, the authoritative one, instead."""
        self._notify = fn

    def set_handback(self, fn) -> None:
        """Called once at release; returns True when it restored the window's
        own incubation controller on the engine."""
        self._handback = fn

    def incubatingObjectCountChanged(self, *args) -> None:
        # The binding passes the new count positionally; *args accepts either
        # spelling and the authoritative count is queried, not trusted. This
        # virtual is informational only (the reveal gate's third leg): pacing
        # itself must keep working even if it never fires.
        if self._notify is not None:
            self._notify(self.incubatingObjectCount())

    def _tick(self) -> None:
        self.incubateFor(self._BOOT_SLICE_MS if self._boot else self._OPEN_SLICE_MS)

    def release_throttle(self) -> None:
        if self._released:
            return
        self._released = True
        self._boot = False
        if self._notify is not None:
            self._notify(0)
        restored = False
        if self._handback is not None:
            try:
                restored = bool(self._handback())
            except Exception:
                restored = False
        if restored:
            self._timer.stop()
        # Not restored (no window to hand back to): keep ticking at the open
        # slice so incubation can never go dead, whatever else went wrong.


def _hand_incubation_back_to_window(engine) -> bool:
    """Restore the window's own incubation controller on the engine.

    Stock behavior for the rest of the session: the window's controller
    drives incubation from the frame loop again, so the paced controller
    only ever governs the boot window.
    """
    for win in engine.rootObjects():
        getter = getattr(win, "incubationController", None)
        if callable(getter):
            controller = getter()
            if controller is not None:
                engine.setIncubationController(controller)
                return True
    return False


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
        Path(__file__).resolve().parent.parent / "ui",  # from source: waves/ui
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


# Handle kept module-global: faulthandler holds the fd for the process lifetime.
_crash_log_file = None


def _crash_log_path() -> Path:
    """The persistent crash log, next to settings.json so it is easy to name in
    a bug report: crash.log in the platform's config folder (Application
    Support on macOS, %APPDATA% on Windows, ~/.config elsewhere)."""
    from waves.helper.path import path_config_base

    return Path(path_config_base()) / "crash.log"


def _open_crash_log():
    """Open crash.log for appending (rotating one old copy past ~512 KB) and
    stamp a session header. Returns the open handle, or None on any failure.
    The handle deliberately outlives this function: faulthandler holds it for
    the life of the process."""
    path = _crash_log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        if path.is_file() and path.stat().st_size > 512 * 1024:
            path.replace(path.with_suffix(".log.1"))
    except OSError:
        pass
    fh = open(path, "a", encoding="utf-8")  # noqa: SIM115
    from waves.waves_ui import __version__

    fh.write(f"\n=== Waves {__version__} session start ===\n")
    fh.flush()
    return fh


def _install_crash_diagnostics() -> None:
    """Make crashes and swallowed background errors diagnosable.

    The download/scan work runs on background threads; a native fault (a Qt
    object misused across threads, a segfault in a C dependency) or an uncaught
    Python exception on a worker would otherwise leave no trace. ``faulthandler``
    dumps a C-level traceback on SIGSEGV/SIGABRT/SIGFPE, and the excepthooks
    route any uncaught Python exception (main thread or worker thread) through
    the logger instead of a bare stderr print.

    A packaged app's stderr is invisible to the user, so both are also pointed
    at a persistent crash.log in the config folder; the bug-report template
    tells users where to find it. This is diagnostics only: it records stack
    traces of our own code, never user data. Best-effort and idempotent."""
    global _crash_log_file
    log = logging.getLogger(__name__)
    try:
        _crash_log_file = _open_crash_log()
    except Exception:
        _crash_log_file = None
        log.debug("could not open crash.log", exc_info=True)
    try:
        if not faulthandler.is_enabled():
            faulthandler.enable(file=_crash_log_file or sys.stderr)
    except Exception:
        log.debug("faulthandler.enable() failed", exc_info=True)

    def _record(prefix: str, exc_info) -> None:
        log.critical(prefix, exc_info=exc_info)
        if _crash_log_file is not None:
            try:
                # Scrub before writing. The logger call above passes through
                # _RedactingFilter, but this handle is a bare open() with no
                # filter, formatter or scrubber attached, so an unscrubbed
                # write here puts the home path and the exception's own
                # message (which routinely carries a media file path) into the
                # very file the bug-report template asks users to paste
                # publicly.
                text = f"{prefix}:\n" + "".join(traceback.format_exception(*exc_info))
                _crash_log_file.write(diagnostics.scrub(text))
                _crash_log_file.flush()
            except Exception:
                log.debug("could not append to crash.log", exc_info=True)

    def _hook(exc_type, exc, tb):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc, tb)
            return
        _record("Uncaught exception", (exc_type, exc, tb))

    sys.excepthook = _hook
    # Uncaught exceptions on threading.Thread workers (Python 3.8+).
    threading.excepthook = lambda args: (
        None
        if issubclass(args.exc_type, SystemExit)
        else _record(
            f"Uncaught exception in thread {getattr(args.thread, 'name', '?')}",
            (args.exc_type, args.exc_value, args.exc_traceback),
        )
    )


def _raise_fd_limit() -> None:
    """Lift the open-file-descriptor soft limit toward the hard limit.

    A macOS app launched from Finder/Launchpad inherits a low RLIMIT_NOFILE soft
    limit (often 256), while a large download session opens many at once: HTTP
    sockets for concurrent scans and downloads, per-segment sockets, output
    files, ffmpeg pipes, and the QML network manager's cover-art connections.
    Queueing several discographies pushes toward that ceiling; once crossed,
    socket()/open() start failing and the session degrades or dies. Raising the
    soft limit (never above the hard limit) is safe, reversible per-process, and
    standard for I/O-heavy apps. No-op on platforms without ``resource``."""
    try:
        import resource
    except ImportError:
        return  # Windows: no POSIX resource limits
    try:
        soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
        target = 10240 if hard == resource.RLIM_INFINITY else min(hard, 10240)
        if soft != resource.RLIM_INFINITY and soft < target:
            resource.setrlimit(resource.RLIMIT_NOFILE, (target, hard))
    except (ValueError, OSError):
        logging.getLogger(__name__).debug("could not raise fd limit", exc_info=True)


def _tune_interpreter_for_gui() -> None:
    """Interpreter settings for a GUI process with busy background threads.

    Every one of these was found by sampling the launch animation dropping
    frames while the library scan, the ownership refresh pool and the network
    workers were all running (2026-08-16):

    * The switch interval. A QML binding or handler that calls into the bridge
      needs the interpreter, and when a worker is running Python it gets it
      only when the worker is asked to hand it over, which happens once per
      switch interval. At the default 5ms every one of the ~100 bridge calls
      a shelf makes as it builds could wait that long; 2ms bounds the wait
      without measurably slowing the workers (they only ever switch when
      someone is waiting).
    * Full garbage collections. The collector walks the whole heap with the
      interpreter held, and the heap here is large (the library index alone
      is hundreds of thousands of objects); each pass measured 15-60ms, and
      the default cadence ran one every few hundred milliseconds while that
      index was being built at launch, each a dropped frame or two. The young
      generations keep their defaults (short-lived cycles are still collected
      promptly); only the full pass is made rare.
    """
    sys.setswitchinterval(0.002)
    gc.set_threshold(700, 10, 100)
    _memoize_macos_proxy_lookups()


def _memoize_macos_proxy_lookups(ttl: float = 60.0) -> None:
    """Cache macOS system-proxy answers for a minute.

    On macOS, ``requests`` consults the system proxy settings for EVERY
    request through urllib's ``getproxies_macosx_sysconf`` and
    ``proxy_bypass_macosx_sysconf`` (both C calls into SystemConfiguration
    that hold the interpreter for their duration). Normally sub-millisecond,
    they were sampled taking 100-150ms during launch, with every other
    Python thread, the GUI thread included, waiting behind them; the launch
    animation stalled for exactly that long. The answers rarely change, so
    the app-wide network workers (dozens of requests at launch) now share
    one lookup per minute instead of two per request. urllib's own
    ``getproxies``/``proxy_bypass`` look these names up at call time, so
    patching the module attributes is enough for requests too."""
    if sys.platform != "darwin":
        return
    import urllib.request as ur

    orig_get = getattr(ur, "getproxies_macosx_sysconf", None)
    orig_bypass = getattr(ur, "proxy_bypass_macosx_sysconf", None)
    if orig_get is None or orig_bypass is None:
        return
    lock = threading.Lock()
    memo: dict = {}

    def getproxies_macosx_sysconf():
        now = time.monotonic()
        with lock:
            hit = memo.get("proxies")
            if hit is not None and now - hit[0] < ttl:
                return dict(hit[1])
        val = orig_get()
        with lock:
            memo["proxies"] = (now, dict(val))
        return val

    def proxy_bypass_macosx_sysconf(host):
        now = time.monotonic()
        key = ("bypass", host)
        with lock:
            hit = memo.get(key)
            if hit is not None and now - hit[0] < ttl:
                return hit[1]
        val = orig_bypass(host)
        with lock:
            memo[key] = (now, val)
            if len(memo) > 512:  # a session visits few hosts; never let this grow unbounded
                memo.clear()
                memo[key] = (now, val)
        return val

    ur.getproxies_macosx_sysconf = getproxies_macosx_sysconf
    ur.proxy_bypass_macosx_sysconf = proxy_bypass_macosx_sysconf


def _log_config_migration() -> None:
    """Breadcrumb the legacy-config migration outcome (never the path itself:
    home paths are PII). The migration ran as a side effect of the first
    config-folder resolution, inside _install_crash_diagnostics.

    Called AFTER the bridge is built, because that is what installs the
    diagnostics handlers: the outcome has to be able to reach the ring."""
    from waves.helper import path as _path_helper

    if _path_helper.CONFIG_MIGRATION == "moved":
        logging.getLogger("waves.config").info("config migrated to the platform-native folder")
    elif _path_helper.CONFIG_MIGRATION == "failed":
        logging.getLogger("waves.config").warning("config migration failed; the legacy folder stays in use")


def waves_activate(tidal: Tidal | None = None) -> int:
    _tune_interpreter_for_gui()
    _install_crash_diagnostics()
    # The freeze watchdog's stuck-event-loop tracebacks belong in the same
    # crash.log faulthandler already writes to.
    diagnostics.set_crash_file(_crash_log_file)
    _raise_fd_limit()
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

    threading.Thread(target=_warm_tls, name="waves-tls-warm", daemon=True).start()

    engine = QQmlApplicationEngine()
    bridge = WavesBridge(tidal=tidal)
    # After the bridge, because the bridge is what installs diagnostics: logged
    # any earlier, no logger had a level and no handler existed, so the "moved"
    # line was dropped at the root default and the "failed" one reached stderr
    # only, which a packaged build has nowhere to show. Either way it never
    # reached the breadcrumb ring, the disk log or an exported bundle, which is
    # the one thing this line is for.
    _log_config_migration()
    # Where the bundled ambient wave loop lives (packaged vs from-source is
    # this module's _data_dir knowledge); the bridge serves playback from a
    # local cached copy of it so boot never streams video off the install
    # volume (see WavesBridge.motionVideoUrl).
    bridge.set_motion_video_source(str(_data_dir("qml") / "assets" / "wave_loop.mp4"))
    # HTTP disk cache for artwork (must be installed before the QML loads).
    art_cache = _ArtCacheFactory(os.path.join(os.path.dirname(bridge.settings.file_path), _ART_CACHE_DIR))
    engine.setNetworkAccessManagerFactory(art_cache)
    app._waves_art_cache = art_cache  # type: ignore[attr-defined]  # keep alive
    engine.rootContext().setContextProperty("waves", bridge)
    # Monospace family for the QML layer (numeric readouts + ASCII art).
    engine.rootContext().setContextProperty("monoFont", _load_mono())
    # UI-label family for buttons/tabs (Console button spec).
    engine.rootContext().setContextProperty("uiFontFamily", _ui_font())
    # Keep a reference so it isn't garbage-collected.
    app._waves_bridge = bridge  # type: ignore[attr-defined]
    # Paced incubation while the launch overlay is up (see the class): must be
    # installed before load so the window never installs its own controller.
    incubation = _BootPacedIncubation(app)
    engine.setIncubationController(incubation)
    app._waves_incubation = incubation  # type: ignore[attr-defined]  # keep alive
    bridge.set_boot_reveal_hook(incubation.release_throttle)
    incubation.set_count_notifier(bridge.note_incubation_count)
    incubation.set_handback(lambda: _hand_incubation_back_to_window(engine))
    # Belt and braces: if the reveal hook is somehow never reached, open the
    # throttle anyway; by then every boot path has long finished.
    QTimer.singleShot(20_000, incubation.release_throttle)
    # Abort downloads and drain the worker pools before the Qt object graph is
    # torn down, otherwise quitting mid-download hangs in QThreadPool teardown.
    app.aboutToQuit.connect(bridge.shutdown)

    engine.load(QUrl.fromLocalFile(str(_QML_MAIN)))
    root_objects = engine.rootObjects()
    if not root_objects:
        print("Failed to load Waves QML UI", file=sys.stderr)
        return 1

    # Back-navigation filter (mouse back/forward buttons, the macOS back-swipe)
    # plus the activate/deactivate swallow: installed on the WINDOW, not the
    # application. Every one of those events is delivered to the QQuickWindow
    # itself, so the window's filter sees them all, and an application-wide
    # filter is a per-event tax on the whole process: each of the thousands of
    # ChildAdded / DeferredDelete / MetaCall events the QML engine raises while
    # it builds a page crossed into Python (a wrapper allocated for the target
    # object, the GIL taken, the filter run and declined), and while a scan
    # worker held the GIL the GUI thread queued behind it for each one. Sampled
    # at launch: object creation ran ~3.5x longer with the filter on the app,
    # and the launch animation dropped frames for it.
    root_objects[0].installEventFilter(bridge)

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
