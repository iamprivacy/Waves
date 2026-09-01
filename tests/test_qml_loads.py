"""The QML actually parses, and a QML error FAILS the suite.

THE HOLE THIS FILLS
-------------------
Twenty-five scenario tests boot Main.qml in a subprocess, and every one of them
answers a QML load failure with ``return _EXIT_PRECONDITION``, which the parent
turns into ``pytest.skip``. That is right for them: they cannot tell a broken
Main.qml from a machine with no working offscreen Qt, and a scenario that
cannot run must not masquerade as a scenario that passed.

The consequence was that NOTHING failed. A syntax error in Main.qml took the
whole suite green, twenty-five skips deep, because no test anywhere asserted
that the QML loads at all. This file is that assertion, and it is the only one
allowed to be strict: if Qt itself is missing it skips like the others, but
once an engine exists, a QML file that will not load is a failure and its
errors are printed.

Engine warnings count too. A QML warning is a binding that silently did
nothing at runtime (an undefined property, a type error in a handler), which is
exactly the class of defect a headless load can catch and a human clicking
around usually cannot.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

_EXIT_OK = 0
_EXIT_BROKEN = 1
_EXIT_NO_QT = 77

QML_DIR = Path(__file__).resolve().parent.parent / "waves" / "waves_ui" / "qml"


def test_main_qml_loads_without_errors_or_warnings():
    """Runs in a SUBPROCESS like the other Main.qml scenarios: building the
    bridge installs process-global handlers that must not leak into the suite."""
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="waves-qml-load-test-")
    proc = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), "--run-scenario"],
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    report = (proc.stdout + proc.stderr).strip()
    if proc.returncode == _EXIT_NO_QT:
        pytest.skip("PySide6 / offscreen Qt unavailable")
    assert proc.returncode == _EXIT_OK, (
        "the QML did not load cleanly. Every scenario test SKIPS on this, so "
        "nothing else in the suite will tell you:\n" + report
    )


def _run_scenario() -> int:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    try:
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QGuiApplication
        from PySide6.QtQml import QQmlApplicationEngine
    except Exception as exc:
        print(f"Qt unavailable: {exc}", file=sys.stderr)
        return _EXIT_NO_QT

    from _qml_offline import patch_offline

    patch_offline()

    app = QGuiApplication.instance() or QGuiApplication([])
    try:
        from waves.waves_ui.backend import WavesBridge
    except Exception as exc:
        print(f"Qt platform/backend unavailable: {exc}", file=sys.stderr)
        return _EXIT_NO_QT

    warnings: list[str] = []
    engine = QQmlApplicationEngine()
    # The bridge must be held in a local: let it be collected and every `waves`
    # read in the QML resolves against null, which reports as a wall of
    # warnings that look like a regression and are not.
    bridge = WavesBridge(tidal=None)
    engine.rootContext().setContextProperty("waves", bridge)
    engine.rootContext().setContextProperty("monoFont", "JetBrains Mono")
    engine.rootContext().setContextProperty("uiFontFamily", app.font().family())
    # engine.warnings, NOT qInstallMessageHandler: the message handler misses
    # what the engine reports through this signal, and a gate that collects
    # nothing reports zero warnings forever.
    engine.warnings.connect(lambda ws: warnings.extend(w.toString() for w in ws))
    engine.load(QUrl.fromLocalFile(str(QML_DIR / "Main.qml")))

    if not engine.rootObjects():
        print("Main.qml FAILED to load", file=sys.stderr)
        for w in warnings:
            print(f"  {w}", file=sys.stderr)
        return _EXIT_BROKEN
    if warnings:
        print(f"Main.qml loaded with {len(warnings)} engine warning(s):", file=sys.stderr)
        for w in warnings:
            print(f"  {w}", file=sys.stderr)
        return _EXIT_BROKEN
    print("Main.qml loaded, 0 warnings", flush=True)
    return _EXIT_OK


if __name__ == "__main__":
    raise SystemExit(_run_scenario())
