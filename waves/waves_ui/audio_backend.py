"""Which Qt Multimedia audio backend Waves asks for on Linux.

Qt 6.11 on Linux talks to PipeWire directly when it can, and its PipeWire
backend can crash in Qt's own audio thread while it lists devices (a
use-after-free inside libpipewire's protocol code). A five-line PySide6
script that only asks for the default output reproduces it with no Waves
code at all, and asking Qt for its PulseAudio backend instead ran clean in
every one of hundreds of starts. On a PipeWire desktop the sound still goes
through PipeWire (pipewire-pulse answers the PulseAudio protocol), so
nothing is lost.

So, on Linux only, when the user has not chosen a backend themselves and a
PulseAudio server answers, Waves sets ``QT_AUDIO_BACKEND=pulseaudio`` before
Qt Multimedia starts. A machine with no PulseAudio server (plain ALSA) is
left to Qt's own choice, and so is anyone who set the variable.

No Qt import: this runs before the application object exists.
"""

from __future__ import annotations

import contextlib
import logging
import os
import socket
from collections.abc import Callable, Mapping, MutableMapping

log = logging.getLogger("waves.audio")

ENV = "QT_AUDIO_BACKEND"
PULSE = "pulseaudio"

# What prefer_pulse decided, for the one line logged once logging is set up.
CHOSE = "chose"  # set it to pulseaudio
KEPT = "kept"  # the user's own setting stands
NO_SERVER = "no-server"  # nothing answered, Qt picks
NOT_LINUX = "not-linux"


def pulse_sockets(environ: Mapping[str, str]) -> list[str]:
    """Where a PulseAudio server's native socket would be, most specific
    first: an explicit unix: server, the runtime path, the session's
    runtime folder."""
    out: list[str] = []
    server = environ.get("PULSE_SERVER", "")
    for part in server.split():
        # "unix:/path" or a bare path; a tcp: server cannot be probed here.
        path = part[5:] if part.startswith("unix:") else part
        if path.startswith("/"):
            out.append(path)
    if environ.get("PULSE_RUNTIME_PATH"):
        out.append(os.path.join(environ["PULSE_RUNTIME_PATH"], "native"))
    if environ.get("XDG_RUNTIME_DIR"):
        out.append(os.path.join(environ["XDG_RUNTIME_DIR"], "pulse", "native"))
    return out


def socket_answers(path: str, timeout: float = 0.25) -> bool:
    """True when something accepts a connection on the unix socket at
    ``path``. A short connect and close, nothing is sent."""
    family = getattr(socket, "AF_UNIX", None)
    if family is None:
        return False
    s = socket.socket(family, socket.SOCK_STREAM)
    try:
        s.settimeout(timeout)
        s.connect(path)
    except OSError:
        return False
    else:
        return True
    finally:
        with contextlib.suppress(OSError):
            s.close()


def prefer_pulse(
    environ: MutableMapping[str, str],
    platform: str,
    probe: Callable[[str], bool] = socket_answers,
) -> str:
    """Set ``QT_AUDIO_BACKEND=pulseaudio`` in ``environ`` on Linux when it is
    unset and a PulseAudio server answers. Returns which way it went."""
    if not platform.startswith("linux"):
        return NOT_LINUX
    if environ.get(ENV):
        return KEPT
    if environ.get("PULSE_SERVER", "").strip() and not any(
        p.startswith("unix:") or p.startswith("/") for p in environ["PULSE_SERVER"].split()
    ):
        # A network server the user pointed PulseAudio at: it is in use on
        # purpose, so ask for the backend that reaches it.
        environ[ENV] = PULSE
        return CHOSE
    for path in pulse_sockets(environ):
        if probe(path):
            environ[ENV] = PULSE
            return CHOSE
    return NO_SERVER


def log_choice(verdict: str) -> None:
    """One line for the breadcrumb ring, once handlers exist. Never a path."""
    if verdict == CHOSE:
        log.info("audio backend: PulseAudio (a server answered)")
    elif verdict == KEPT:
        log.info("audio backend: the one set in %s", ENV)
    elif verdict == NO_SERVER:
        log.info("audio backend: Qt's own choice (no PulseAudio server)")
