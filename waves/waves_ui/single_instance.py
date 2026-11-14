"""One running copy of Waves per profile.

With the window closed to the background, opening Waves again would start a
second full copy next to the hidden one (two queues, two writers of one
settings file). Instead the second launch hands over: it asks the running
copy to show its window and leaves.

Plain Qt (a lock file and a local socket), so it is the same on macOS,
Windows and Linux. Their name is a digest of the profile folder: no user name
and no path in it, and a sandboxed profile (tests, probes) gets a name of its
own.
"""

from __future__ import annotations

import hashlib
import logging
import os
import sys
from collections.abc import Callable

from PySide6.QtCore import QCoreApplication, QDir, QElapsedTimer, QLockFile, QObject, QThread
from PySide6.QtNetwork import QLocalServer, QLocalSocket

_log = logging.getLogger("waves.instance")


def server_name(config_base: str) -> str:
    """The socket name for the profile kept under `config_base`."""
    digest = hashlib.sha256(str(config_base).encode("utf-8", "surrogatepass")).hexdigest()[:16]
    return f"waves-{digest}"


def hand_over(name: str, timeout_ms: int = 500) -> bool:
    """Ask the copy already listening on `name` to show its window. True when
    one answered, so this launch has nothing left to do."""
    sock = QLocalSocket()
    sock.connectToServer(name)
    if not sock.waitForConnected(timeout_ms):
        return False
    _let_the_running_copy_come_forward()
    sock.disconnectFromServer()
    return True


def _let_the_running_copy_come_forward() -> None:
    """Windows only: this launch is the app the user just opened, so it may
    bring a window to the front, and the running copy may not. Pass that
    right on before handing over, or the window comes back behind the app
    in front (or only flashes its taskbar button)."""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.user32.AllowSetForegroundWindow(-1)  # ASFW_ANY
    except Exception:
        _log.debug("could not pass on the foreground", exc_info=True)


def claim(name: str, parent: QObject | None = None, wait_ms: int = 3000) -> InstanceServer | None:
    """Become the one copy on `name`, or None when a running copy took this
    launch over. Called before anything heavy starts.

    The profile's lock file decides which copy that is. Listening alone
    cannot: on macOS and Linux a listen on a taken name replaces the file of
    the copy already listening, so two launches at once would both run, the
    first one unreachable. A copy that holds the lock is running or about to
    listen, so a launch that finds it held hands over, waiting a moment for
    a copy that is still starting. A crashed copy's lock (its process gone)
    is stale and taken over."""
    lock = QLockFile(os.path.join(QDir.tempPath(), f"{name}.lock"))
    lock.setStaleLockTime(0)  # only a lock whose holder is gone is stale
    if not lock.tryLock(0):
        if _hand_over_patiently(name, wait_ms):
            return None
        # The holder never answered (hung, or a process that is not Waves):
        # run anyway rather than leave the user with no window at all.
        _log.warning("the running copy did not answer, starting anyway")
        return InstanceServer(name, None, parent)
    return InstanceServer(name, None, parent, lock=lock)


def _hand_over_patiently(name: str, wait_ms: int) -> bool:
    """hand_over, retried while a copy that holds the lock gets to its
    listen (the lock and the listen are taken back to back, so this is
    rarely more than one try)."""
    clock = QElapsedTimer()
    clock.start()
    while True:
        if hand_over(name, 200):
            return True
        if clock.elapsed() >= wait_ms:
            return False
        QCoreApplication.processEvents()
        QThread.msleep(50)


class InstanceServer(QObject):
    """Listens for a later launch and calls `on_show` when one hands over."""

    def __init__(
        self,
        name: str,
        on_show: Callable[[], None] | None,
        parent: QObject | None = None,
        *,
        lock: QLockFile | None = None,
    ) -> None:
        super().__init__(parent)
        self._on_show = on_show
        # Held for the life of this copy (claim took it), released on close.
        self._lock = lock
        self._server = QLocalServer(self)
        # Only this user's own launches may connect.
        self._server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        self._server.newConnection.connect(self._accept)
        if not self._server.listen(name):
            # This copy holds the profile's lock, so no other copy is
            # running: a name still taken is the socket file a crashed copy
            # left behind. Clear it and listen again.
            QLocalServer.removeServer(name)
            self._server.listen(name)
        self.listening = self._server.isListening()
        if not self.listening:
            _log.warning("could not listen for a second launch")

    def set_on_show(self, on_show: Callable[[], None]) -> None:
        """What a later launch's hand-over does, once the window exists."""
        self._on_show = on_show

    def close(self) -> None:
        self._server.close()
        if self._lock is not None:
            self._lock.unlock()
            self._lock = None

    def _accept(self) -> None:
        # The connection is the whole message: only this user's own launch of
        # Waves on this profile knows the name, and it has one thing to ask.
        asked = False
        while True:
            conn = self._server.nextPendingConnection()
            if conn is None:
                break
            asked = True
            conn.close()
            conn.deleteLater()
        if not asked:
            return
        if self._on_show is None:
            # Still starting: the window shows on its own when it is built.
            _log.info("a second launch asked for the window while starting")
            return
        _log.info("a second launch asked for the window")
        self._on_show()
