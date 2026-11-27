"""Lyrics through an installed Lavender.

Lavender (a separate desktop app for LRCLIB lyrics) can answer lyrics
requests headlessly: started with ``--waves-lyrics-worker`` it reads its own
settings (online or local LRCLIB dump, tolerant matching, extra sources, word
timestamps) and answers one JSON line per request on stdout. Waves never reads
Lavender's database or any token it keeps; it only runs the installed binary.

One worker process is shared by every download. Requests are serialized
through a lock, each one has a deadline, a crashed worker is restarted once
per request, and the process is closed when Waves exits. Nothing here raises:
any trouble reads as "Lavender has no answer" and the caller carries on with
its usual sources.
"""

from __future__ import annotations

import atexit
import contextlib
import json
import logging
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from waves.waves_ui.diagnostics import content as log_content

logger = logging.getLogger("waves.lyrics.lavender")

FLAG: str = "--waves-lyrics-worker"
PROTOCOL: int = 1
APP_ID: str = "io.github.iamprivacy.lrclocalget"
# A local dump answers in milliseconds, online LRCLIB plus the extra sources
# within a few seconds; anything slower must not hold a download back.
_REQUEST_TIMEOUT_SEC: float = 25.0
_HELLO_TIMEOUT_SEC: float = 10.0
# After a failed start or a worker that stopped answering, wait this long
# before trying again, so a broken install does not cost every track a wait.
_RETRY_AFTER_SEC: float = 300.0
# Lavender's error codes (`ErrorKind` in its waves_worker.rs). Only these are
# logged: any other text from another app could name a path.
_CODES = frozenset(
    {"local_db_not_configured", "local_db_unavailable", "source_trouble", "lookup_failed", "bad_request"}
)
_VERSION = re.compile(r"\d+(\.\d+){1,3}")


@dataclass
class LavenderAnswer:
    """What Lavender said about one track.

    ``status`` is ``found``, ``not_found`` or ``unavailable`` (no worker, a
    timeout, an error reply). Only ``found`` carries lyrics; an instrumental
    is ``found`` with empty text and ``instrumental`` set.
    """

    status: str
    synced: str = ""
    plain: str = ""
    instrumental: bool = False
    note: str = ""
    warning: str = ""

    @property
    def found(self) -> bool:
        return self.status == "found"


UNAVAILABLE = LavenderAnswer("unavailable")


def _candidates() -> list[list[str]]:
    """Commands that start an installed Lavender, most likely first."""
    found: list[list[str]] = []

    def add(path: Path | str | None) -> None:
        if path and Path(path).is_file() and os.access(path, os.X_OK):
            command = [str(path)]
            if command not in found:
                found.append(command)

    if sys.platform == "darwin":
        for root in (Path("/Applications"), Path.home() / "Applications"):
            add(root / "Lavender.app" / "Contents" / "MacOS" / "Lavender")
    elif sys.platform == "win32":
        for base in (os.environ.get("LOCALAPPDATA"), os.environ.get("PROGRAMFILES")):
            if base:
                add(Path(base) / "Lavender" / "Lavender.exe")
                add(Path(base) / "Programs" / "Lavender" / "Lavender.exe")
        add(_windows_registry_install())
    else:
        add("/usr/bin/Lavender")
        add(shutil.which("Lavender"))
        flatpak = shutil.which("flatpak")
        if flatpak and _flatpak_dir():
            found.append([flatpak, "run", "--command=Lavender", APP_ID])
    return found


def _windows_registry_install() -> Path | None:
    """The install folder from Lavender's uninstall entry, when there is one."""
    try:
        import winreg  # (Windows only)
    except ImportError:
        return None
    key_path = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\Lavender"
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(hive, key_path) as key:
                location, _ = winreg.QueryValueEx(key, "InstallLocation")
        except OSError:
            continue
        if location:
            return Path(str(location).strip('"')) / "Lavender.exe"
    return None


def _flatpak_dir() -> Path | None:
    """Lavender's Flatpak install folder (system or user), when there is one."""
    # shortcut: custom Flatpak installations are not seen, ask `flatpak info` if one is ever reported
    data = os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share"
    for root in (Path("/var/lib/flatpak"), Path(data) / "flatpak"):
        folder = root / "app" / APP_ID
        if folder.is_dir():
            return folder
    return None


def _stamp(command: list[str]) -> float:
    """When the Lavender behind ``command`` last changed (0 when unreadable)."""
    target = Path(command[0])
    if target.name == "flatpak":
        # The deployed build; an update points it at a new folder.
        target = (_flatpak_dir() or target) / "current" / "active"
    try:
        return target.stat().st_mtime
    except OSError:
        return 0.0


def _code(value: object) -> str:
    return value if isinstance(value, str) and value in _CODES else "other"


@dataclass
class _Worker:
    process: subprocess.Popen
    lines: queue.Queue = field(default_factory=queue.Queue)
    version: str = ""


class LavenderClient:
    """Runs one Lavender worker and asks it for lyrics.

    Args:
        command: The command that starts Lavender (without the worker flag).
            ``None`` finds the installed app.
    """

    def __init__(self, command: list[str] | None = None) -> None:
        self._command = command
        self._lock = threading.Lock()
        self._worker: _Worker | None = None
        self._next_id = 0
        self._failed_at: float | None = None
        # The stamp of an install whose start said no usable hello, and
        # whether it said nothing at all (an older Lavender without the worker).
        self._refused: tuple[float, bool] | None = None
        # The stamp of an install that said nothing once. A first start can
        # just be slow (macOS checking a freshly downloaded app), so only a
        # second silent start of the same install calls it old.
        self._silent_once: float | None = None
        self._closed = False

    # Finding and starting

    def command(self) -> list[str] | None:
        """The command that starts Lavender, or None when it is not installed."""
        # Looked up afresh every time, so a Lavender installed or removed
        # while Waves runs is noticed without a restart.
        return self._command or next(iter(_candidates()), None)

    def installed(self) -> bool:
        return self.command() is not None

    def _is_refused(self, command: list[str]) -> bool:
        return self._refused is not None and self._refused[0] == _stamp(command)

    def state(self) -> str:
        """``ok``, ``missing`` (not installed) or ``old`` (installed, without the lyrics worker)."""
        command = self.command()
        if command is None:
            return "missing"
        refused = self._refused
        return "old" if refused and refused[1] and refused[0] == _stamp(command) else "ok"

    def version(self) -> str:
        """The running worker's Lavender version, empty before it has started."""
        worker = self._worker
        return worker.version if worker else ""

    def _start(self) -> _Worker | None:
        command = self.command()
        if command is None or self._closed:
            return None
        if self._is_refused(command):
            # Not started again until its files change: an older Lavender
            # opens its window on every start.
            return None
        if self._failed_at is not None and time.monotonic() - self._failed_at < _RETRY_AFTER_SEC:
            return None
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
        spawn = {"stdin": subprocess.PIPE, "stdout": subprocess.PIPE, "stderr": subprocess.DEVNULL}
        spawn |= {"text": True, "encoding": "utf-8", "bufsize": 1, "creationflags": creationflags}
        try:
            process = subprocess.Popen([*command, FLAG], **spawn)  # noqa: S603 (the installed app, no shell)
        except OSError:
            logger.info("Lavender could not be started; using Waves' own lyrics sources.")
            self._failed_at = time.monotonic()
            return None

        worker = _Worker(process)
        threading.Thread(target=_pump, args=(process, worker.lines), name="lavender-lyrics", daemon=True).start()
        hello = _read(worker, _HELLO_TIMEOUT_SEC)
        if not isinstance(hello, dict) or hello.get("protocol") != PROTOCOL:
            # An older Lavender opens its window instead of answering: close it.
            _stop(worker)
            stamp = _stamp(command)
            if not isinstance(hello, dict) and self._silent_once != stamp:
                # Silent once: maybe only slow. Try again after the usual pause.
                logger.info("Lavender did not say hello in time; trying again later.")
                self._silent_once = stamp
                self._failed_at = time.monotonic()
                return None
            logger.info("The installed Lavender has no lyrics worker Waves can use (update Lavender to use it).")
            self._refused = (stamp, not isinstance(hello, dict))
            return None
        if self._closed:
            # Waves quit while this one was saying hello: close() never saw it.
            _stop(worker)
            return None
        self._refused = None
        self._silent_once = None
        version = str(hello.get("version") or "")
        worker.version = version if _VERSION.fullmatch(version) else ""
        self._failed_at = None
        logger.info("Lavender %s lyrics worker started.", worker.version or "(unknown version)")
        return worker

    def close(self) -> None:
        """Stop the worker for good. Never waits for a lookup in flight: that
        lookup sees its worker gone and reads as unavailable."""
        self._closed = True
        worker = self._worker
        if worker is not None:
            _stop(worker)

    # Asking

    def lookup(self, artist: str, title: str, album: str, duration: float) -> LavenderAnswer:
        """Ask Lavender for one track's lyrics. Never raises."""
        if not artist or not title or not duration:
            return UNAVAILABLE
        label = log_content(f"{artist} - {title}")
        # Parallel downloads take turns; waiting longer than one request
        # would hand this track over to Waves' own LRCLIB lookup anyway.
        if not self._lock.acquire(timeout=_REQUEST_TIMEOUT_SEC):
            logger.info("Lavender is busy with other tracks; using Waves' own lyrics sources.")
            return UNAVAILABLE
        try:
            for attempt in range(2):
                if self._worker is None or self._worker.process.poll() is not None:
                    if self._worker is not None:
                        _stop(self._worker)
                    self._worker = self._start()
                    if self._worker is None:
                        return UNAVAILABLE
                self._next_id += 1
                request = {
                    "id": self._next_id,
                    "title": title,
                    "artist": artist,
                    "album": album,
                    "duration": float(duration),
                }
                reply = self._ask(self._worker, request)
                if reply is not None:
                    return _answer(reply, label)
                # A crashed worker gets one fresh start. One that is alive but
                # silent (its own sources are slow or unreachable) would only
                # make this track wait twice.
                crashed = self._worker.process.poll() is not None
                _stop(self._worker)
                self._worker = None
                if not crashed:
                    logger.info("Lavender did not answer in time.")
                    break
                if attempt == 0:
                    logger.info("Lavender stopped answering; restarting it once.")
            # Leave it alone for a while instead of making every track wait.
            self._failed_at = time.monotonic()
        finally:
            self._lock.release()
        return UNAVAILABLE

    def _ask(self, worker: _Worker, request: dict) -> dict | None:
        stdin = worker.process.stdin
        if stdin is None:
            return None
        try:
            stdin.write(json.dumps(request) + "\n")
            stdin.flush()
        except (OSError, ValueError):
            return None
        deadline = time.monotonic() + _REQUEST_TIMEOUT_SEC
        while True:
            reply = _read(worker, max(0.0, deadline - time.monotonic()))
            if not isinstance(reply, dict):
                return None
            # A reply to an earlier, timed-out request can still arrive; skip it.
            if reply.get("id") == request["id"]:
                return reply


def _pump(process: subprocess.Popen, lines: queue.Queue) -> None:
    """Moves the worker's stdout lines onto a queue, then a final None."""
    if process.stdout is None:
        lines.put(None)
        return
    try:
        for line in process.stdout:
            lines.put(line)
    except (OSError, ValueError):
        pass
    lines.put(None)


def _read(worker: _Worker, timeout: float) -> dict | None:
    """The next JSON object from the worker, None on timeout, exit or garbage."""
    deadline = time.monotonic() + timeout
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None
        try:
            line = worker.lines.get(timeout=remaining)
        except queue.Empty:
            return None
        if line is None:
            # Keep the end marker for any later read.
            worker.lines.put(None)
            return None
        line = line.strip()
        if not line:
            continue
        try:
            value = json.loads(line)
        except ValueError:
            continue
        if isinstance(value, dict):
            return value


def _stop(worker: _Worker) -> None:
    process = worker.process
    try:
        if process.stdin is not None:
            process.stdin.close()
    except OSError:
        pass
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        process.kill()
        with contextlib.suppress(subprocess.TimeoutExpired):
            process.wait(timeout=2)
    except OSError:
        pass


def _answer(reply: dict, label: str) -> LavenderAnswer:
    status = reply.get("status")
    warning = _code(reply["warning"]) if reply.get("warning") else ""
    if warning:
        logger.info("Lavender: its local database could not be used (%s); it asked online LRCLIB.", warning)
    if status == "not_found":
        logger.info("Lavender has no lyrics for `%s`.", label)
        return LavenderAnswer("not_found", warning=warning)
    if status != "found":
        logger.info("Lavender could not look up `%s` (%s).", label, _code(reply.get("error")))
        return UNAVAILABLE
    synced = str(reply.get("synced_enhanced") or reply.get("synced") or "")
    plain = str(reply.get("plain") or "")
    instrumental = bool(reply.get("instrumental"))
    kind = "instrumental" if instrumental else "synced" if synced else "plain"
    logger.info("Lavender lyrics found for `%s` (%s).", label, kind)
    return LavenderAnswer(
        "found",
        synced=synced,
        plain=plain,
        instrumental=instrumental,
        note=str(reply.get("note") or ""),
        warning=warning,
    )


_shared: LavenderClient | None = None
_shared_lock = threading.Lock()


def shared_client() -> LavenderClient:
    """The one Lavender client every download uses, closed at exit."""
    global _shared
    with _shared_lock:
        if _shared is None:
            _shared = LavenderClient()
            atexit.register(_shared.close)
        return _shared


def close_shared() -> None:
    """Close the shared client for good (the app's quit).

    The closed client stays shared, so a download still finishing its
    lyrics after the quit finds it closed instead of starting a new Lavender.
    """
    global _shared
    with _shared_lock:
        if _shared is None:
            _shared = LavenderClient()
        client = _shared
    client.close()


def fetch_lavender_lyrics(
    artist: str,
    title: str,
    album: str,
    duration: float,
    title_bare: str | None = None,
    client: LavenderClient | None = None,
) -> LavenderAnswer:
    """Ask the installed Lavender for lyrics, retrying with the bare title.

    Returns:
        The answer for ``title``; when that is a miss and ``title_bare`` is a
        different string, the answer for ``title_bare``. ``unavailable`` when
        Lavender is not installed or could not answer.
    """
    client = client or shared_client()
    answer = client.lookup(artist, title, album, duration)
    if answer.status == "not_found" and title_bare and title_bare != title:
        bare = client.lookup(artist, title_bare, album, duration)
        if bare.status != "unavailable":
            return bare
    return answer
