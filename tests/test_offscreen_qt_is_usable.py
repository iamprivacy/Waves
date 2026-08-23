"""The canary for every offscreen QML gate in this suite.

Forty-odd scenario tests drive the real Main.qml on Qt's offscreen platform,
and each of them SKIPS rather than fails when Qt cannot start, so a runner
missing a Qt system library (libEGL, libGL, fontconfig, xkbcommon) turns
the whole QML half of the suite into a green summary that tested nothing.
This test is the one that fails in that state: PySide6 is installed, so the
gates were meant to run, and the platform did not come up.

It skips only when PySide6 itself is absent, the contributor-without-the-gui-
extra case, where not running the QML gates is a choice rather than a
surprise.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

_EXIT_OK = 0
_EXIT_NO_PLATFORM = 3


def test_the_offscreen_qt_platform_can_start():
    if importlib.util.find_spec("PySide6") is None:
        pytest.skip("PySide6 not installed (the gui extra): the QML gates are not expected to run here")
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="waves-qt-canary-")
    proc = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), "--run-probe"],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    report = (proc.stdout + proc.stderr).strip()
    assert proc.returncode == _EXIT_OK, (
        "PySide6 is installed but Qt's offscreen platform did not start, so every "
        "QML scenario in this suite is SKIPPING and the run is green for nothing. "
        "On a Linux runner this is usually a missing system library (libegl1, "
        "libgl1, libfontconfig1, libxkbcommon0):\n" + report
    )


def _probe() -> int:
    try:
        from PySide6.QtGui import QGuiApplication
        from PySide6.QtQml import QQmlEngine
    except Exception as exc:
        print(f"PySide6 import failed: {exc}", file=sys.stderr)
        return _EXIT_NO_PLATFORM
    try:
        app = QGuiApplication.instance() or QGuiApplication([])
        engine = QQmlEngine()
        ok = app.platformName() == "offscreen" and engine is not None
    except Exception as exc:
        print(f"Qt platform failed to start: {exc}", file=sys.stderr)
        return _EXIT_NO_PLATFORM
    if not ok:
        print(f"unexpected platform: {app.platformName()!r}", file=sys.stderr)
        return _EXIT_NO_PLATFORM
    return _EXIT_OK


if __name__ == "__main__" and "--run-probe" in sys.argv:
    sys.exit(_probe())
