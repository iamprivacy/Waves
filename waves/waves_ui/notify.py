"""A best-effort desktop notification (an OS banner).

``DesktopNotify.send(title, message)`` shows one banner through the optional
``desktop-notifier`` package and reports whether the OS actually took it.
Nothing here may ever affect the caller: a missing package, a platform
without a notification service, a refused permission or a failing send are
all logged at DEBUG and answered with False.

False has to be trustworthy, because callers act on True: the update check
remembers a version as announced only once its banner went out. The package
alone cannot be believed there: its send swallows every backend failure, and
on macOS a send that the OS refused (permission denied, or an unsigned run
from source) still returns as if it were shown. So a send only counts when
the package reports it dispatched and, where the platform can say, the app
is authorized to notify.

The notifier is built once, on first use, under a lock (two workers landing
together must not each build one: every instance holds its own event loop
thread). A failed build is remembered for a while, not forever: a Linux
session whose notification daemon starts late gets its banners back on a
later send instead of staying silent until the app restarts. Sending blocks
on the notifier's own loop and, on macOS, the very first send may wait for
the user to answer the permission prompt, so callers keep it off the GUI
thread.
"""

from __future__ import annotations

import contextlib
import importlib.util
import logging
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path

logger = logging.getLogger("waves.notify")

# A failed build (no package is final, but no service may be a daemon that
# has not started yet) is tried again on the first send after this pause.
_BUILD_RETRY_S = 300.0


def notifications_supported() -> bool:
    """True when the optional package is installed (no import, no side
    effect): Settings offers the banner switch only then."""
    try:
        return importlib.util.find_spec("desktop_notifier") is not None
    except (ImportError, ValueError):
        return False


def _app_icon_path() -> Path | None:
    """The Waves icon for the banner, from the same places app.py looks:
    beside the packaged binary, beside the Nuitka launcher, or the source
    tree. Without one the package falls back to its own Python logo, which
    Windows and Linux then show on every banner (macOS uses the bundle's)."""
    roots = (
        Path(sys.executable).resolve().parent / "ui",
        Path(sys.argv[0]).resolve().parent / "ui",
        Path(__file__).resolve().parent.parent / "ui",
    )
    for root in roots:
        png = root / "icon256.png"
        if png.is_file():
            return png
    return None


def _close_quietly(notifier) -> None:
    """Close the half-open DBus connection a failed Linux probe leaves
    behind (the bus connected, then the notification interface was not
    there). The build is retried later, so each failed try must not keep
    its own bus and loop thread alive."""

    async def drop(backend) -> None:
        bus = getattr(backend, "bus", None)
        if bus is not None:
            bus.disconnect()

    # The package has no public close; its private plumbing under suppress.
    with contextlib.suppress(Exception):
        notifier._run_coro_sync(drop(notifier._async_api._backend))


# UNNotificationPresentationOptions: sound (2), list (8), banner (16).
_PRESENT_IN_FRONT = 2 | 8 | 16
_front_delegate_class = None


def _present_in_front(notifier) -> None:
    """(macOS) Show banners while Waves is the front app too. Without a
    delegate that answers willPresentNotification, the system files a
    banner the front app sends straight into Notification Center, unseen,
    while Windows and Linux show it. The package's delegate answers only
    clicks, so it is swapped for a subclass that also answers this one.
    Whether a banner goes out while Waves is in front at all is the
    bridge's call (the notify_in_front setting), not this delegate's."""
    global _front_delegate_class
    from desktop_notifier.backends.macos import NotificationCenterDelegate
    from rubicon.objc import NSUInteger, ObjCBlock, objc_id, objc_method

    if _front_delegate_class is None:

        def will_present(self, center, notification, completion_handler) -> None:
            ObjCBlock(completion_handler, None, NSUInteger)(_PRESENT_IN_FRONT)

        # Real types, not the string hints this module's future import
        # would leave: rubicon reads the ObjC signature from them.
        will_present.__annotations__ = {
            "center": objc_id,
            "notification": objc_id,
            "completion_handler": objc_id,
            "return": None,
        }

        class WavesNotificationDelegate(NotificationCenterDelegate):
            userNotificationCenter_willPresentNotification_withCompletionHandler_ = objc_method(will_present)

        _front_delegate_class = WavesNotificationDelegate

    # The package has no public hook for this; its private plumbing.
    backend = notifier._async_api._backend
    delegate = _front_delegate_class.alloc().init()
    delegate.implementation = backend
    # The center holds its delegate weakly: the backend keeps it alive.
    backend.nc_delegate = delegate
    backend.nc.delegate = delegate


def _default_factory(app_name: str):
    from desktop_notifier import DesktopNotifierSync, Icon

    png = _app_icon_path()
    notifier = DesktopNotifierSync(app_name=app_name, app_icon=Icon(path=png) if png else None)
    if sys.platform == "darwin":
        # A banner still works without it (filed in Notification Center).
        with contextlib.suppress(Exception):
            _present_in_front(notifier)
    if sys.platform.startswith("linux"):
        # A session with no notification service (a bare window manager with
        # no daemon, or one that starts late) fails inside the package's
        # send, which swallows it and opens a fresh bus connection on the
        # next one that is never closed. Asking here raises instead, so the
        # failed build is remembered until the retry pause is over.
        try:
            notifier.get_capabilities()
        except Exception:
            _close_quietly(notifier)
            raise
    return notifier


class DesktopNotify:
    def __init__(self, app_name: str = "Waves", factory: Callable[[str], object] | None = None) -> None:
        self._app_name = app_name
        self._factory = factory or _default_factory
        self._lock = threading.Lock()
        # None: not tried yet. False: tried and unavailable until _retry_at.
        self._notifier = None
        self._retry_at = 0.0

    def _ensure(self):
        with self._lock:
            if self._notifier is False and time.monotonic() >= self._retry_at:
                self._notifier = None
            if self._notifier is None:
                try:
                    self._notifier = self._factory(self._app_name)
                except Exception:
                    logger.debug("desktop notifications unavailable", exc_info=True)
                    self._notifier = False
                    self._retry_at = time.monotonic() + _BUILD_RETRY_S
            return self._notifier or None

    @staticmethod
    def _authorized(notifier) -> bool:
        """Whether the OS lets this app notify. macOS answers truthfully
        (and the send before this call already raised the permission prompt,
        so the answer is settled); platforms that cannot say, and notifiers
        without the method, count as authorized."""
        ask = getattr(notifier, "has_authorisation", None)
        if not callable(ask):
            return True
        try:
            return bool(ask())
        except Exception:
            return True

    def send(self, title: str, message: str) -> bool:
        try:
            notifier = self._ensure()
            if notifier is None:
                return False
            handed: list[bool] = []
            notifier.send(title=str(title), message=str(message), on_dispatched=lambda: handed.append(True))
            if not handed:
                # The package swallows its backends' failures (no service,
                # a scheduling error) and only fires the dispatched callback
                # past them: without it, nothing was shown.
                logger.debug("desktop notification was not dispatched")
                return False
            if not self._authorized(notifier):
                # macOS dispatches to the notification center even when the
                # user denied permission (or an unsigned run cannot get it):
                # dispatched, but never shown.
                logger.debug("desktop notification dispatched but not authorized")
                return False
        except Exception:
            logger.debug("desktop notification failed", exc_info=True)
            return False
        return True
