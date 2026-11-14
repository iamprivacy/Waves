"""On Linux, Waves asks Qt for its PulseAudio backend when a server answers.

Qt 6.11's PipeWire audio backend can crash in Qt's own audio thread while it
lists devices; its PulseAudio backend does not, and on a PipeWire desktop the
sound still reaches PipeWire through pipewire-pulse. The choice is made
before Qt Multimedia starts, only on Linux, never over the user's own
QT_AUDIO_BACKEND, and never on a machine with no PulseAudio server.
"""

from __future__ import annotations

import logging
import os
import socket
import sys
import tempfile

import pytest

from waves.waves_ui import audio_backend as ab


def _never(_path):
    raise AssertionError("no socket may be probed here")


def test_linux_with_a_server_answering_asks_for_pulse():
    env = {"XDG_RUNTIME_DIR": "/run/user/1000"}
    seen = []
    verdict = ab.prefer_pulse(env, "linux", lambda p: seen.append(p) or True)
    assert verdict == ab.CHOSE
    assert env[ab.ENV] == "pulseaudio"
    assert seen == ["/run/user/1000/pulse/native"]


def test_the_user_s_own_backend_stands():
    env = {ab.ENV: "pipewire", "XDG_RUNTIME_DIR": "/run/user/1000"}
    assert ab.prefer_pulse(env, "linux", _never) == ab.KEPT
    assert env[ab.ENV] == "pipewire"


def test_no_server_leaves_qt_to_choose():
    env = {"XDG_RUNTIME_DIR": "/run/user/1000"}
    assert ab.prefer_pulse(env, "linux", lambda _p: False) == ab.NO_SERVER
    assert ab.ENV not in env


def test_no_runtime_folder_at_all_leaves_qt_to_choose():
    env: dict[str, str] = {}
    assert ab.prefer_pulse(env, "linux", _never) == ab.NO_SERVER
    assert ab.ENV not in env


@pytest.mark.parametrize("platform", ["darwin", "win32", "cygwin"])
def test_other_platforms_are_untouched(platform):
    env = {"XDG_RUNTIME_DIR": "/run/user/1000"}
    assert ab.prefer_pulse(env, platform, _never) == ab.NOT_LINUX
    assert ab.ENV not in env


def test_sockets_are_tried_most_specific_first():
    env = {
        "PULSE_SERVER": "unix:/custom/native",
        "PULSE_RUNTIME_PATH": "/run/pulse-rt",
        "XDG_RUNTIME_DIR": "/run/user/1000",
    }
    assert ab.pulse_sockets(env) == [
        "/custom/native",
        os.path.join("/run/pulse-rt", "native"),
        os.path.join("/run/user/1000", "pulse", "native"),
    ]
    seen = []
    assert ab.prefer_pulse(env, "linux", lambda p: seen.append(p) or p.startswith("/run/pulse-rt")) == ab.CHOSE
    assert seen == ["/custom/native", os.path.join("/run/pulse-rt", "native")]


def test_a_network_server_the_user_named_asks_for_pulse_without_probing():
    env = {"PULSE_SERVER": "tcp:speakers.local"}
    assert ab.prefer_pulse(env, "linux", _never) == ab.CHOSE
    assert env[ab.ENV] == "pulseaudio"


def test_the_verdict_logs_one_line_without_a_path(caplog):
    with caplog.at_level(logging.INFO, logger="waves.audio"):
        for verdict in (ab.CHOSE, ab.KEPT, ab.NO_SERVER, ab.NOT_LINUX):
            ab.log_choice(verdict)
    lines = [r.getMessage() for r in caplog.records]
    assert len(lines) == 3, lines  # nothing on other platforms
    assert not any("/" in line for line in lines)


@pytest.mark.skipif(not hasattr(socket, "AF_UNIX") or sys.platform == "win32", reason="unix sockets")
def test_the_probe_reads_a_real_socket():
    # A short folder: a unix socket path has a small length limit.
    folder = tempfile.mkdtemp(prefix="wpa-", dir="/tmp" if os.path.isdir("/tmp") else None)
    path = os.path.join(folder, "native")
    assert ab.socket_answers(path) is False, "nothing listens yet"
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        server.bind(path)
        server.listen(1)
        assert ab.socket_answers(path) is True
    finally:
        server.close()
        os.unlink(path)
        os.rmdir(folder)


def test_the_app_chooses_before_the_application_object_exists():
    """The variable is read when Qt Multimedia starts, so it must be set
    before QGuiApplication is made, and logged once handlers exist."""
    import inspect

    from waves.waves_ui import app

    src = inspect.getsource(app.waves_activate)
    choose = src.index("audio_backend.prefer_pulse(os.environ, sys.platform)")
    assert choose < src.index("owns_app, app = _make_app()")
    assert "_WavesApp(sys.argv)" in inspect.getsource(app._make_app)
    assert src.index("audio_backend.log_choice(") > src.index("WavesBridge(tidal=tidal)")
