"""One running copy per profile (waves/waves_ui/single_instance.py).

With the window closed to the background, a second launch must bring the
running copy's window back instead of starting a second copy. These drive the
real local socket under a name of their own, never the machine's profile.
"""

from __future__ import annotations

import os
import uuid

import pytest

pytest.importorskip("PySide6.QtNetwork")

from PySide6.QtCore import QCoreApplication, QElapsedTimer  # noqa: E402

from waves.waves_ui import single_instance  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QCoreApplication.instance() or QCoreApplication([])


def _name() -> str:
    return f"waves-test-{os.getpid()}-{uuid.uuid4().hex[:8]}"


def _spin(app, until, ms=2000) -> bool:
    clock = QElapsedTimer()
    clock.start()
    while not until() and clock.elapsed() < ms:
        app.processEvents()
    return until()


def test_name_is_a_digest_never_the_path():
    name = single_instance.server_name("/some/home/folder/Waves")
    assert name.startswith("waves-") and len(name) == len("waves-") + 16
    assert "home" not in name and "/" not in name
    assert name == single_instance.server_name("/some/home/folder/Waves")
    assert name != single_instance.server_name("/another/profile")


def test_nobody_listening_means_this_launch_carries_on(app):
    assert single_instance.hand_over(_name(), timeout_ms=200) is False


def test_second_launch_asks_the_running_copy_for_its_window(app):
    name = _name()
    shown = []
    server = single_instance.InstanceServer(name, lambda: shown.append(True))
    try:
        assert server.listening
        assert single_instance.hand_over(name) is True
        assert _spin(app, lambda: bool(shown)), "the running copy never heard the second launch"
        assert len(shown) == 1
    finally:
        server.close()


@pytest.mark.skipif(os.name == "nt", reason="a named pipe leaves nothing behind")
def test_a_name_left_by_a_crashed_copy_is_taken_over(app):
    import socket

    from PySide6.QtCore import QDir
    from PySide6.QtNetwork import QLocalServer

    name = _name()
    # A socket file nobody listens on, as a crashed copy leaves it.
    path = os.path.join(QDir.tempPath(), name)
    left = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    left.bind(path)
    left.close()
    assert os.path.exists(path)
    try:
        assert single_instance.hand_over(name, timeout_ms=200) is False
        shown = []
        server = single_instance.InstanceServer(name, lambda: shown.append(True))
        try:
            assert server.listening
            assert single_instance.hand_over(name) is True
            assert _spin(app, lambda: bool(shown))
        finally:
            server.close()
    finally:
        QLocalServer.removeServer(name)


def test_two_launches_at_once_make_one_copy_and_it_stays_reachable(app):
    """Audit 2026-11-14b: the second of two launches found nobody on
    hand_over, then listened on the same name; on macOS and Linux that
    replaced the first copy's socket file, so two copies ran and the first
    could no longer be reached. The profile's lock decides now."""
    name = _name()
    first = single_instance.claim(name)
    try:
        assert first is not None and first.listening
        assert single_instance.claim(name, wait_ms=500) is None, "a second copy started"
        shown = []
        first.set_on_show(lambda: shown.append(True))
        assert single_instance.hand_over(name) is True, "the first copy's name was taken over"
        assert _spin(app, lambda: bool(shown))
    finally:
        first.close()
    # Closed (quit), the next launch is the copy.
    again = single_instance.claim(name)
    try:
        assert again is not None and again.listening
    finally:
        again.close()


def test_a_launch_during_the_first_copys_startup_waits_for_it_to_listen(app):
    from PySide6.QtCore import QDir, QLockFile, QTimer

    name = _name()
    # A copy that has the lock and is a moment away from its listen.
    held = QLockFile(os.path.join(QDir.tempPath(), f"{name}.lock"))
    assert held.tryLock(0)
    shown = []
    late = []
    QTimer.singleShot(300, lambda: late.append(single_instance.InstanceServer(name, lambda: shown.append(True))))
    try:
        assert single_instance.claim(name, wait_ms=3000) is None, "the launch started a second copy"
        assert _spin(app, lambda: bool(shown))
    finally:
        for s in late:
            s.close()
        held.unlock()


def test_a_crashed_copys_lock_is_taken_over(app, tmp_path):
    import subprocess
    import sys

    name = _name()
    script = tmp_path / "crash.py"
    script.write_text(
        "import os, sys\n"
        "from PySide6.QtCore import QCoreApplication, QDir, QLockFile\n"
        "app = QCoreApplication([])\n"
        f"lock = QLockFile(os.path.join(QDir.tempPath(), {name + '.lock'!r}))\n"
        "assert lock.tryLock(0)\n"
        "os._exit(0)  # gone without unlocking\n"
    )
    subprocess.run([sys.executable, str(script)], check=True, timeout=60)  # noqa: S603
    mine = single_instance.claim(name, wait_ms=300)
    try:
        assert mine is not None and mine.listening
    finally:
        mine.close()


def test_claim_lets_a_request_go_until_the_window_exists(app):
    name = _name()
    mine = single_instance.claim(name)
    try:
        assert mine is not None and mine.listening
        assert single_instance.claim(name, wait_ms=300) is None
        app.processEvents()  # no window yet: the request is let go
        shown = []
        mine.set_on_show(lambda: shown.append(True))
        assert single_instance.claim(name, wait_ms=300) is None
        assert _spin(app, lambda: bool(shown)) and len(shown) == 1
    finally:
        mine.close()


def test_windows_hand_over_lets_the_running_copy_take_the_foreground(app, monkeypatch):
    """The launch the user just opened may bring a window forward; the
    running copy may not, unless the launch passes the right on."""
    import sys
    import types

    calls = []
    fake = types.SimpleNamespace(
        windll=types.SimpleNamespace(user32=types.SimpleNamespace(AllowSetForegroundWindow=calls.append))
    )
    monkeypatch.setitem(sys.modules, "ctypes", fake)
    monkeypatch.setattr(single_instance.sys, "platform", "win32")
    assert single_instance.hand_over(_name(), timeout_ms=200) is False
    assert calls == [], "a first launch grants nothing"
    name = _name()
    server = single_instance.InstanceServer(name, lambda: None)
    try:
        assert single_instance.hand_over(name) is True
        assert calls == [-1]
    finally:
        server.close()
