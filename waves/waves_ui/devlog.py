"""Lightweight development timing/diagnostics logger for the Waves UI.

This is a development aid. It records how long each user-facing operation takes
(section switches, searches, artist/album/library loads, downloads, settings
saves) to a rotating log file (and stderr), so performance regressions and UI
hitches can be diagnosed *after the fact* from the log instead of relying on how
the app subjectively "feels".

Design goals:

* **Structured & greppable.** Every line is ``HH:MM:SS.mmm  LEVEL  [category] …``
  so a whole category can be pulled with ``grep '\\[search]' waves_dev.log``.
* **Self-flagging.** Each category has a duration *budget*; an operation that
  overruns its budget also emits a ``[slow]`` WARNING line, so the things worth
  looking at stand out without reading every line.
* **Cheap & off by default.** Controlled by the ``WAVES_DEBUG`` environment
  variable (``1`` = on, ``0`` = off). It defaults OFF everywhere, both packaged
  builds and from-source runs, so the app never persists user activity to disk
  unless explicitly asked (a developer sets ``WAVES_DEBUG=1``). When off, the
  logging calls are near-no-ops and only real warnings/errors are emitted.
* **Privacy-safe.** Only timings, counts and opaque ids are recorded, never
  credentials or personal data. Keep it that way when adding new call sites.

Typical lines::

    14:23:01.123  INFO   [nav]      results -> settings (8ms)
    14:23:05.456  INFO   [search]   needle=daft punk api=1.10s proc=0.12s n=137 (1.23s)
    14:23:05.456  WARN   [slow]     [search] needle=daft punk took 1.23s (budget 2.00s)
"""

from __future__ import annotations

import logging
import os
import time
from contextlib import contextmanager
from pathlib import Path

# Back-compat alias: WAVES_DEBUG=1 forces verbose diagnostics on. Runtime
# control (the Settings toggle) lives in diagnostics.set_verbose; the calls
# below always emit and the handlers installed by diagnostics.install decide
# what persists (breadcrumb ring always, disk only per the privacy model).
ENABLED = os.environ.get("WAVES_DEBUG", "0") != "0"

LOG_FILENAME = "waves_dev.log"

# Per-category "this is taking too long" budgets, in seconds. An operation that
# exceeds its budget gets an extra [slow] WARNING line. Tune as the app evolves.
_BUDGETS = {
    "nav": 0.10,  # a section switch should be effectively instant
    "search": 2.0,
    "artist": 1.5,
    "album": 1.0,
    "library": 1.5,
    "save": 0.30,
    "url": 2.0,
    "login": 5.0,
    "download": 120.0,
}

_log = logging.getLogger("waves.perf")  # child of "waves"; inherits its handlers


def init(log_dir: str | None = None) -> Path | None:
    """Install the diagnostics stack (handlers, redaction, breadcrumbs) on the
    ``waves`` logger tree. Idempotent; returns the on-disk log path or None."""
    from . import diagnostics

    return diagnostics.install(log_dir or "")


def fmt_dur(seconds: float) -> str:
    """Human-friendly duration: sub-second in ms, otherwise seconds."""
    return f"{seconds * 1000:.0f}ms" if seconds < 1 else f"{seconds:.2f}s"


def _compose(category: str, message: str, fields: dict, suffix: str = "") -> str:
    """Build one clean, space-separated log line (no awkward empty gaps)."""
    parts = [f"[{category}]"]
    if message:
        parts.append(message)
    extra = " ".join(f"{k}={v}" for k, v in fields.items() if v is not None)
    if extra:
        parts.append(extra)
    if suffix:
        parts.append(suffix)
    return " ".join(parts)


def event(category: str, message: str = "", **fields) -> None:
    """Log a point-in-time event with no duration.

    Always emits: these INFO lines feed the in-memory breadcrumb ring that a
    crash report is stitched from. They reach *disk* only in verbose mode (the
    file handler's level gates them), so the privacy default is unchanged.
    """
    _log.info("%s", _compose(category, message, fields))


def done(category: str, message: str = "", duration: float = 0.0, **fields) -> None:
    """Log a completed operation with its duration; flag it if over budget."""
    _log.info("%s", _compose(category, message, fields, f"({fmt_dur(duration)})"))
    budget = _BUDGETS.get(category)
    if budget is not None and duration > budget:
        _log.warning(
            "%s",
            _compose("slow", f"{category}: {message}", {}, f"took {fmt_dur(duration)} (budget {fmt_dur(budget)})"),
        )


class _Span:
    """Mutable handle yielded by :func:`span` so the timed block can attach
    fields it only learns mid-flight (result counts, sub-durations, …)."""

    __slots__ = ("_t0", "fields")

    def __init__(self, fields: dict) -> None:
        self.fields = fields
        self._t0 = time.perf_counter()

    def set(self, **fields) -> _Span:
        self.fields.update(fields)
        return self

    def lap(self) -> float:
        """Seconds elapsed since the span (or the last :meth:`reset`) started."""
        return time.perf_counter() - self._t0

    def reset(self) -> None:
        self._t0 = time.perf_counter()


@contextmanager
def span(category: str, message: str = "", **fields):
    """Time a block and log a :func:`done` line when it exits (even on error)."""
    handle = _Span(dict(fields))
    start = time.perf_counter()
    try:
        yield handle
    finally:
        done(category, message, time.perf_counter() - start, **handle.fields)


def clock() -> float:
    """Monotonic high-resolution timestamp for manual sub-timing."""
    return time.perf_counter()
