"""Privacy-guarded diagnostic logging for Waves.

This module turns "a user says the app is slow/frozen/crashed" into a log the
maintainer can act on, without the log ever being unsafe to attach to a public
GitHub issue. It builds on the pieces the app already has (``faulthandler``,
the excepthooks in :mod:`.app`, the timing helpers in :mod:`.devlog`) and adds
the four missing layers:

* **Redaction at the handler layer.** Every handler that can persist or show a
  log line carries :class:`_RedactingFilter`, so no call site anywhere in the
  app (present or future) can write identity PII to disk. Identity PII is
  always scrubbed: usernames, home paths (all OS forms), hostnames, IP/MAC
  addresses, emails, tokens/keys, and any value registered via
  :func:`register_secret` (e.g. the TIDAL account id after login).
* **Breadcrumbs, always on.** A bounded in-memory ring of recent INFO events
  (a ``deque``, deliberately not ``MemoryHandler``, which flushes-and-clears
  rather than dropping oldest). When an ERROR is logged, the trail is dumped
  into the on-disk log so even a first-ever failure arrives with leading
  context. Costs memory only; nothing extra is written during normal runs.
* **Verbose mode, off by default.** The user-facing "Verbose diagnostics"
  toggle raises the on-disk level from WARNING to DEBUG and starts the freeze
  watchdog and the perf sampler. The ``WAVES_DEBUG`` env var still force
  enables it for developers.
* **A shareable export.** :func:`export_bundle` concatenates crash.log and the
  rotating app log, prepends a redacted system header and the current
  breadcrumb trail, re-scrubs every line (idempotent), optionally hashes
  content spans, and writes a single file safe for a public issue tracker.

Growing the app does not require growing this module. New subsystems get
breadcrumbs by logging INFO on any ``waves.*`` logger (or via
:func:`.devlog.event`); new secrets call :func:`register_secret` once at
acquisition; new thread pools call :func:`register_pool`. Content that a user
may prefer to hide (search text, titles) is wrapped in :func:`content` at the
call site, which the optional export checkbox reduces to an opaque hash.
"""

from __future__ import annotations

import contextlib
import faulthandler
import getpass
import hashlib
import logging
import os
import platform
import re
import socket
import sys
import threading
import time
import uuid
from collections import deque
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

logger = logging.getLogger("waves.diag")

#: One id per launch, stamped on the session divider and the export header so
#: interleaved sessions in a rotated log can be told apart.
SESSION_ID = uuid.uuid4().hex[:8]

LOG_FILENAME = "waves_dev.log"
_LOG_MAX_BYTES = 2_000_000
_LOG_BACKUPS = 3

# Content markers («…», produced by content()). Identity placeholders use ‹…›
# so the two never collide: the export content pass hashes «…» spans only.
_C_OPEN, _C_CLOSE = "«", "»"
_CONTENT_RE = re.compile(f"{_C_OPEN}([^{_C_OPEN}{_C_CLOSE}]{{0,400}}){_C_CLOSE}")

# How often the breadcrumb trail may be re-dumped into the file log. An error
# storm (one ERROR per failed track, say) should not write the same trail
# dozens of times.
_CRUMB_DUMP_INTERVAL_SEC = 30.0

# Freeze watchdog: re-arm every _WATCHDOG_TICK_MS from the GUI thread; if the
# event loop stalls past _WATCHDOG_DUMP_SEC the pending faulthandler dump
# fires (all-thread tracebacks into crash.log). Stalls that recover before the
# dump are still recorded, as a WARNING with the observed gap.
_WATCHDOG_TICK_MS = 2_000
_WATCHDOG_DUMP_SEC = 6.0
_WATCHDOG_WARN_GAP_SEC = 3.5

_SAMPLER_INTERVAL_MS = 5_000


def content(text: object) -> str:
    """Mark ``text`` as user content (search text, a title) in a log message.

    The markers survive into the on-disk log (harmless, greppable) and let the
    export's optional "also redact content" pass replace exactly these spans
    with opaque hashes, nothing else.
    """
    return f"{_C_OPEN}{text}{_C_CLOSE}"


# --------------------------------------------------------------------------
# Redaction
# --------------------------------------------------------------------------


class _Redactor:
    """Recall-first identity scrubber applied to every persisted log line.

    Denylist shapes (paths/IPs/emails/tokens) plus literal values learned at
    runtime (this machine's username/hostname/home, registered secrets). Both
    passes are idempotent so the export can safely re-scrub already-scrubbed
    lines. Over-redaction is accepted by design: a version string that looks
    like an IP is a smaller loss than one leaked address.
    """

    _EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
    _IPV4 = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
    _MAC = re.compile(r"\b(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}\b")
    # Colon-hex runs, including the compressed "::" form; the callback keeps
    # timestamps (12:34:56) by requiring a hex letter, a "::", or 4+ groups
    # before treating a match as an address.
    _IPV6 = re.compile(r"(?<![\w.:-])[0-9A-Fa-f]{0,4}(?::[0-9A-Fa-f]{0,4}){2,7}(?:%\w+)?(?![\w.:-])")
    # "Bearer <token>" first, so the token itself (not the word Bearer) is the
    # value the key/value pattern below would otherwise consume.
    _BEARER = re.compile(r"(?i)\bbearer\s+[^\s'\"&;,]+")
    # "key: value" secrets, whatever the surrounding syntax (JSON, URLs, repr).
    _KV_SECRET = re.compile(
        r"(?i)\b(bearer|authorization|auth|token|api[_-]?key|apikey|secret|password|passwd|"
        r"cookie|set-cookie|session[_-]?id|access[_-]?token|refresh[_-]?token|client[_-]?secret)\b"
        r"(['\"]?\s*[:=]\s*|\s+)(['\"]?)[^\s'\"&;,]+"
    )
    # Bare high-entropy blobs: long hex (ids, digests) and long base64ish runs.
    _LONG_HEX = re.compile(r"\b[0-9a-fA-F]{32,}\b")
    _B64ISH = re.compile(
        r"\b(?=[A-Za-z0-9+/_-]*[A-Z])(?=[A-Za-z0-9+/_-]*[a-z])(?=[A-Za-z0-9+/_-]*\d)[A-Za-z0-9+/_-]{24,}={0,2}\b"
    )
    _UUID = re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b")
    # Any OS's user-directory form, including ones from *other* machines that
    # arrive in server messages: /Users/<x>, /home/<x>, C:\Users\<x>,
    # \\host\Users\<x>, and the %USERPROFILE% expansion style.
    _USER_PATH = re.compile(r"(?i)((?:[A-Z]:)?[\\/](?:Users|home)[\\/]+)([^\\/\s\"';]+)")

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._secrets: list[tuple[str, str]] = []  # (value, placeholder), longest first
        home = os.path.expanduser("~")
        self._homes = [h for h in {home, os.path.realpath(home)} if h and h != "/"]
        try:
            user = getpass.getuser()
        except Exception:
            user = ""
        self._user_re = re.compile(rf"(?i)\b{re.escape(user)}\b") if len(user) >= 3 else None
        try:
            host = socket.gethostname()
        except Exception:
            host = ""
        names = {host, host.split(".", 1)[0]}
        self._host_res = [re.compile(rf"(?i)\b{re.escape(n)}\b") for n in names if len(n) >= 3]

    def register_secret(self, value: str, placeholder: str = "‹secret›") -> None:
        value = str(value or "")
        if len(value) < 4:  # too short to redact without shredding the text
            return
        with self._lock:
            if all(value != v for v, _ in self._secrets):
                self._secrets.append((value, placeholder))
                self._secrets.sort(key=lambda p: len(p[0]), reverse=True)

    @staticmethod
    def _ipv6_sub(m: re.Match) -> str:
        s = m.group(0)
        hexish = any(c in "abcdefABCDEF" for c in s)
        if hexish or "::" in s or s.count(":") >= 4:
            return "‹ip›"
        return s  # a clock time such as 12:34:56

    def scrub(self, text: str) -> str:
        with self._lock:
            secrets = list(self._secrets)
        for value, placeholder in secrets:
            text = text.replace(value, placeholder)
        text = self._BEARER.sub("Bearer ‹redacted›", text)
        text = self._KV_SECRET.sub(lambda m: f"{m.group(1)}{m.group(2)}{m.group(3)}‹redacted›", text)
        text = self._MAC.sub("‹mac›", text)
        text = self._IPV6.sub(self._ipv6_sub, text)
        text = self._IPV4.sub("‹ip›", text)
        text = self._EMAIL.sub("‹email›", text)
        text = self._UUID.sub("‹uuid›", text)
        text = self._LONG_HEX.sub("‹hex›", text)
        text = self._B64ISH.sub("‹b64›", text)
        for h in self._homes:
            text = text.replace(h, "~")
        text = self._USER_PATH.sub(r"\1‹user›", text)
        if self._user_re is not None:
            text = self._user_re.sub("‹user›", text)
        for host_re in self._host_res:
            text = host_re.sub("‹host›", text)
        return text

    @staticmethod
    def scrub_content(text: str) -> str:
        """The optional second tier: content spans become short stable hashes,
        so distinct values stay distinguishable without being readable."""

        def _hash(m: re.Match) -> str:
            digest = hashlib.sha1(m.group(1).encode("utf-8", "replace"), usedforsecurity=False).hexdigest()[:8]
            return f"{_C_OPEN}#{digest}{_C_CLOSE}"

        return _CONTENT_RE.sub(_hash, text)


_redactor = _Redactor()


def register_secret(value: str, placeholder: str = "‹secret›") -> None:
    """Register a runtime secret (token, account id) for literal redaction.

    Call once whenever a new sensitive value is acquired; every handler scrubs
    it from that moment on. Values shorter than 4 characters are ignored.
    """
    _redactor.register_secret(value, placeholder)


def scrub(text: str, redact_content: bool = False) -> str:
    """Scrub identity PII from ``text`` (and content spans when asked)."""
    text = _redactor.scrub(text)
    if redact_content:
        text = _redactor.scrub_content(text)
    return text


class _RedactingFilter(logging.Filter):
    """Collapses each record to a pre-scrubbed message before any handler
    formats it. Attached to every handler; running twice is harmless because
    the scrub is idempotent."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:
            message = str(record.msg)
        scrubbed = _redactor.scrub(message)
        if scrubbed != message or record.args:
            record.msg = scrubbed
            record.args = None
        if record.exc_info and not record.exc_text:
            # Pre-format the traceback so its file paths pass through the
            # scrubber; the formatter then reuses exc_text as-is.
            import traceback as _tb

            record.exc_text = _redactor.scrub("".join(_tb.format_exception(*record.exc_info)))
            record.exc_info = None
        elif record.exc_text:
            record.exc_text = _redactor.scrub(record.exc_text)
        return True


# --------------------------------------------------------------------------
# Breadcrumbs
# --------------------------------------------------------------------------


class _BreadcrumbHandler(logging.Handler):
    """Bounded drop-oldest ring of recent formatted records. Memory only."""

    def __init__(self, capacity: int = 250) -> None:
        super().__init__(level=logging.INFO)
        self.ring: deque[str] = deque(maxlen=capacity)

    def emit(self, record: logging.LogRecord) -> None:
        # A breadcrumb must never take the app down.
        with contextlib.suppress(Exception):
            self.ring.append(self.format(record))


class _CrumbDumpHandler(logging.Handler):
    """On any ERROR+, writes the breadcrumb trail into the file log (rate
    limited), so the error arrives with the events that led up to it."""

    def __init__(self, crumbs: _BreadcrumbHandler, target: logging.Handler) -> None:
        super().__init__(level=logging.ERROR)
        self._crumbs = crumbs
        self._target = target
        self._last_dump = 0.0

    def emit(self, record: logging.LogRecord) -> None:
        if getattr(record, "_crumb_dump", False):
            return
        now = time.monotonic()
        if now - self._last_dump < _CRUMB_DUMP_INTERVAL_SEC:
            return
        self._last_dump = now
        with contextlib.suppress(Exception):  # a failed dump must not cascade
            trail = list(self._crumbs.ring)
            lines = [f"---- breadcrumb trail (last {len(trail)} events) ----", *trail, "---- end trail ----"]
            for line in lines:
                rec = logging.LogRecord("waves.crumbs", logging.WARNING, "", 0, line, None, None)
                rec._crumb_dump = True  # type: ignore[attr-defined]
                self._target.handle(rec)


# --------------------------------------------------------------------------
# Qt plumbing (message handler, freeze watchdog, perf sampler)
# --------------------------------------------------------------------------

_qt_log = logging.getLogger("waves.qt")


def _install_qt_handler() -> None:
    """Route QML/Qt warnings through the (redacted) app log. One handler per
    process; QML errors are the messages users can never relay verbally."""
    try:
        from PySide6.QtCore import QtMsgType, qInstallMessageHandler
    except Exception:
        return

    level_map = {
        QtMsgType.QtDebugMsg: logging.DEBUG,
        QtMsgType.QtInfoMsg: logging.INFO,
        QtMsgType.QtWarningMsg: logging.WARNING,
        QtMsgType.QtCriticalMsg: logging.ERROR,
        QtMsgType.QtFatalMsg: logging.CRITICAL,
    }

    def _handler(mode, _context, message):
        # Release builds carry no file/line context (QT_MESSAGELOGCONTEXT is
        # not defined), so only the message text is usable. Never raise out of
        # a Qt callback.
        with contextlib.suppress(Exception):
            _qt_log.log(level_map.get(mode, logging.WARNING), "%s", message)

    qInstallMessageHandler(_handler)


class _Watchdog:
    """GUI-thread freeze detector, active only in verbose mode.

    A QTimer on the GUI thread re-arms ``faulthandler.dump_traceback_later``
    on every tick; each re-arm resets the countdown, so the dump (all-thread
    tracebacks into crash.log) fires only when the event loop is genuinely
    stuck past the timeout. Stalls that recover before the dump still show as
    a WARNING with the observed gap.
    """

    def __init__(self) -> None:
        self._timer = None
        self._last_tick = 0.0

    def start(self, crash_file) -> None:
        if self._timer is not None:
            return
        try:
            from PySide6.QtCore import QTimer
        except Exception:
            return
        self._crash_file = crash_file
        self._timer = QTimer()
        self._timer.setInterval(_WATCHDOG_TICK_MS)
        self._timer.timeout.connect(self._tick)
        self._last_tick = time.monotonic()
        self._timer.start()
        self._tick()

    def stop(self) -> None:
        if self._timer is None:
            return
        self._timer.stop()
        self._timer = None
        with contextlib.suppress(Exception):
            faulthandler.cancel_dump_traceback_later()

    def _tick(self) -> None:
        now = time.monotonic()
        gap = now - self._last_tick
        self._last_tick = now
        if gap > _WATCHDOG_WARN_GAP_SEC:
            logger.warning("[freeze] event loop blocked ~%.1fs (recovered)", gap)
        with contextlib.suppress(Exception):  # a failed re-arm only skips one tick
            kwargs = {"file": self._crash_file} if self._crash_file else {}
            faulthandler.dump_traceback_later(_WATCHDOG_DUMP_SEC, repeat=False, **kwargs)


class _PerfSampler:
    """Low-rate resource snapshot (verbose only): RSS plus per-pool activity.

    One DEBUG line per interval; the numbers that make a saturation or leak
    visible in a user's log without a reproduction on the maintainer's side.
    """

    def __init__(self) -> None:
        self._timer = None
        self._pools: list[tuple[str, object]] = []

    def register_pool(self, name: str, pool) -> None:
        self._pools.append((name, pool))

    def start(self) -> None:
        if self._timer is not None:
            return
        try:
            from PySide6.QtCore import QTimer
        except Exception:
            return
        self._timer = QTimer()
        self._timer.setInterval(_SAMPLER_INTERVAL_MS)
        self._timer.timeout.connect(self._sample)
        self._timer.start()

    def stop(self) -> None:
        if self._timer is not None:
            self._timer.stop()
            self._timer = None

    @staticmethod
    def _rss_mb() -> float | None:
        try:
            import resource

            peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            # ru_maxrss is bytes on macOS/BSD, kilobytes on Linux.
            return peak / (1024 * 1024) if sys.platform == "darwin" else peak / 1024
        except Exception:
            return None  # Windows: no resource module; skip rather than dep on psutil

    def _sample(self) -> None:
        # Sampling is best-effort; a probe failure must never surface.
        with contextlib.suppress(Exception):
            parts = []
            rss = self._rss_mb()
            if rss is not None:
                parts.append(f"peak_rss={rss:.0f}MB")
            for name, pool in self._pools:
                with contextlib.suppress(Exception):
                    parts.append(f"{name}={pool.activeThreadCount()}/{pool.maxThreadCount()}")
            if parts:
                logger.debug("[sys] %s threads=%d", " ".join(parts), threading.active_count())


# --------------------------------------------------------------------------
# Installation and the verbose switch
# --------------------------------------------------------------------------

_installed = False
_log_dir: Path | None = None
_file_handler: RotatingFileHandler | None = None
_stream_handler: logging.StreamHandler | None = None
_crumbs = _BreadcrumbHandler()
_watchdog = _Watchdog()
_sampler = _PerfSampler()
_crash_file = None
_verbose = False

#: Developer override: WAVES_DEBUG=1 forces verbose regardless of the setting.
FORCED_VERBOSE = os.environ.get("WAVES_DEBUG", "0") != "0"


def set_crash_file(handle) -> None:
    """Give the watchdog the open crash.log handle faulthandler already uses,
    so freeze tracebacks land next to crash tracebacks."""
    global _crash_file
    _crash_file = handle


def register_pool(name: str, pool) -> None:
    """Register a QThreadPool for the perf sampler. One line per new pool."""
    _sampler.register_pool(name, pool)


def log_path() -> Path | None:
    return (_log_dir / LOG_FILENAME) if _log_dir else None


def install(log_dir: str) -> Path | None:
    """Wire redaction, breadcrumbs and the on-disk log. Idempotent.

    Called once at startup (before verbose state is known); the default disk
    level is WARNING plus breadcrumb dumps, per the privacy model. Returns the
    log-file path, or None if the directory is unwritable.
    """
    global _installed, _log_dir, _file_handler, _stream_handler
    if _installed:
        return log_path()
    _installed = True

    fmt = logging.Formatter("%(asctime)s.%(msecs)03d  %(levelname)-5s %(name)s %(message)s", datefmt="%H:%M:%S")
    logging.addLevelName(logging.WARNING, "WARN")
    redact = _RedactingFilter()

    waves = logging.getLogger("waves")
    waves.propagate = False
    waves.setLevel(logging.DEBUG)
    root = logging.getLogger()
    root.setLevel(logging.INFO)

    # stderr: genuine problems always surface on a from-source run.
    _stream_handler = logging.StreamHandler(sys.stderr)
    _stream_handler.setFormatter(fmt)
    _stream_handler.setLevel(logging.DEBUG if FORCED_VERBOSE else logging.WARNING)
    _stream_handler.addFilter(redact)

    # Disk: bounded rotating file next to settings.json and crash.log.
    try:
        path = Path(log_dir)
        path.mkdir(parents=True, exist_ok=True)
        _log_dir = path
        _file_handler = RotatingFileHandler(
            path / LOG_FILENAME, maxBytes=_LOG_MAX_BYTES, backupCount=_LOG_BACKUPS, encoding="utf-8"
        )
        _file_handler.setFormatter(fmt)
        _file_handler.setLevel(logging.WARNING)
        _file_handler.addFilter(redact)
    except OSError:
        _log_dir = None
        _file_handler = None

    _crumbs.setFormatter(fmt)
    _crumbs.addFilter(redact)

    for target in (waves, root):
        target.addHandler(_stream_handler)
        target.addHandler(_crumbs)
        if _file_handler is not None:
            target.addHandler(_file_handler)
            target.addHandler(_CrumbDumpHandler(_crumbs, _file_handler))

    _install_qt_handler()
    logger.info("[init] session=%s waves diagnostics installed", SESSION_ID)
    return log_path()


def set_verbose(on: bool) -> None:
    """Flip verbose diagnostics at runtime (the Advanced Settings toggle).

    Verbose raises the disk level to DEBUG and runs the freeze watchdog and
    perf sampler; off returns to WARNING-plus-breadcrumb-dumps. Must be called
    from the GUI thread (it owns QTimers).
    """
    global _verbose
    on = bool(on) or FORCED_VERBOSE
    if on == _verbose:
        return
    _verbose = on
    if _file_handler is not None:
        _file_handler.setLevel(logging.DEBUG if on else logging.WARNING)
    if on:
        logger.warning("[init] verbose diagnostics ON (session=%s, v%s)", SESSION_ID, _app_version())
        _watchdog.start(_crash_file)
        _sampler.start()
    else:
        _watchdog.stop()
        _sampler.stop()
        logger.warning("[init] verbose diagnostics OFF")


def is_verbose() -> bool:
    return _verbose


# --------------------------------------------------------------------------
# Export
# --------------------------------------------------------------------------


def _app_version() -> str:
    try:
        from tidaler.waves_ui import __version__
    except Exception:
        return "?"
    else:
        return __version__


def _qt_version() -> str:
    try:
        from PySide6.QtCore import qVersion

        return qVersion()
    except Exception:
        return "?"


def _read_tail(path: Path, max_bytes: int) -> str:
    try:
        size = path.stat().st_size
        with open(path, encoding="utf-8", errors="replace") as fh:
            if size > max_bytes:
                fh.seek(size - max_bytes)
                fh.readline()  # drop the partial first line
            return fh.read()
    except OSError:
        return ""


def export_bundle(redact_content: bool = False) -> str:
    """Build one redacted, shareable diagnostic file; returns its path ("" on
    failure). Every line is re-scrubbed on the way out (the final pass), and
    content spans are hashed when the user asked for that too."""
    if _log_dir is None:
        return ""
    # Sub-second suffix: two exports in the same second (double-click, or one
    # with and one without content redaction) must not overwrite each other.
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")[:-3]
    out_path = _log_dir / f"waves-diagnostics-{stamp}.txt"

    sections: list[tuple[str, str]] = []
    header = "\n".join(
        [
            f"Waves diagnostic bundle  ·  {datetime.now().isoformat(timespec='seconds')}",
            f"app={_app_version()} qt={_qt_version()} python={platform.python_version()}",
            f"os={platform.system()} {platform.release()} ({platform.machine()})",
            f"session={SESSION_ID} verbose={_verbose} content_redacted={bool(redact_content)}",
            "identity PII (usernames, paths, addresses, tokens, account ids) is always scrubbed",
        ]
    )
    sections.append(("SYSTEM", header))
    sections.append(("RECENT ACTIVITY (this session, newest last)", "\n".join(_crumbs.ring) or "(none)"))

    crash = _log_dir / "crash.log"
    for path, cap, title in [
        (crash.with_suffix(".log.1"), 256_000, "CRASH LOG (previous)"),
        (crash, 512_000, "CRASH LOG"),
        *[
            (_log_dir / f"{LOG_FILENAME}.{i}", _LOG_MAX_BYTES, f"APP LOG (older .{i})")
            for i in range(_LOG_BACKUPS, 0, -1)
        ],
        (_log_dir / LOG_FILENAME, _LOG_MAX_BYTES, "APP LOG (current)"),
    ]:
        if path.is_file():
            body = _read_tail(path, cap)
            if body.strip():
                sections.append((title, body))

    try:
        with open(out_path, "w", encoding="utf-8") as out:
            for title, body in sections:
                out.write(f"\n======== {title} ========\n")
                for line in body.splitlines():
                    out.write(scrub(line, redact_content=redact_content) + "\n")
    except OSError:
        logger.exception("diagnostic export failed")
        return ""
    else:
        logger.info("[export] diagnostic bundle written (%d sections)", len(sections))
        return str(out_path)
