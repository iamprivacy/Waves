"""HoverSwell must fade IN fast and OUT slow, not the other way round.

The swell is what hands the light between the PREVIEW and DOWNLOAD halves of a
two-up pill: the arriving half snaps bright (90ms, flat) while the leaving half
eases away (260ms, OutQuad), so the pointer never crosses a dark gap.

Written as a Behavior, both the animated property (``opacity: on ? 1 : 0``) and
the animation config (``duration: hs.on ? 90 : 260``) read the same flag, and
the Behavior captured the flag's OLD value when the flip triggered it. The two
durations came out exactly reversed: ~262ms to light up, ~98ms to go dark, so
crossing the divider read as a quarter-second dead spot. States and transitions
pick by direction instead, which cannot race the flag.

This samples a real HoverSwell out of Main.qml, so it measures whatever the app
actually ships rather than a copy of it. Runs in a SUBPROCESS for the same
reason as the other Main.qml scenarios: building the bridge installs
process-global handlers that must not leak into the rest of the suite.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

_EXIT_OK = 0
_EXIT_REGRESSED = 1
_EXIT_NO_QT = 77
_EXIT_PRECONDITION = 78

# The designed durations, and the slack the measurement is allowed. Sampling is
# coarse (a 5ms event-loop tick) and an offscreen frame clock is not exact, so
# the assertions only pin each fade to its own half of the range.
_IN_MS = 90
_OUT_MS = 260
_IN_CEILING = 170
_OUT_FLOOR = 200
_OUT_CEILING = 420
_SAMPLE_MS = 5
_GIVE_UP_MS = 1500

QML_MAIN = Path(__file__).resolve().parent.parent / "waves" / "waves_ui" / "qml" / "Main.qml"


def test_hover_swell_fades_in_fast_and_out_slow():
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="waves-swell-test-")
    proc = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), "--run-scenario"],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    out = (proc.stdout + proc.stderr).strip()
    measured = "\n".join(line for line in out.splitlines() if line.startswith("swell"))
    import pytest

    if proc.returncode == _EXIT_NO_QT:
        pytest.skip("PySide6 / offscreen Qt unavailable")
    if proc.returncode == _EXIT_PRECONDITION:
        pytest.skip(f"could not set up the scenario in this environment:\n{out.splitlines()[-8:]}")
    assert (
        proc.returncode == _EXIT_OK
    ), f"the hover swell's fade durations are inverted (in should be ~{_IN_MS}ms, out ~{_OUT_MS}ms):\n{measured}"


def _run_scenario() -> int:
    # THIS checkout's waves, not the venv's editable install.
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    try:
        from PySide6.QtCore import QEventLoop, QTimer, QUrl
        from PySide6.QtGui import QGuiApplication
        from PySide6.QtQml import QQmlApplicationEngine, QQmlEngine, QQmlExpression
    except Exception as exc:
        print(f"Qt unavailable: {exc}", file=sys.stderr)
        return _EXIT_NO_QT

    app = QGuiApplication.instance() or QGuiApplication([])
    try:
        from waves.waves_ui.app import _load_mono
        from waves.waves_ui.backend import WavesBridge
    except Exception as exc:
        print(f"Qt platform/backend unavailable: {exc}", file=sys.stderr)
        return _EXIT_NO_QT

    engine = QQmlApplicationEngine()
    bridge = WavesBridge(tidal=None)
    engine.rootContext().setContextProperty("waves", bridge)
    engine.rootContext().setContextProperty("monoFont", _load_mono())
    engine.rootContext().setContextProperty("uiFontFamily", app.font().family())
    engine.load(QUrl.fromLocalFile(str(QML_MAIN)))
    roots = engine.rootObjects()
    if not roots:
        print("Main.qml failed to load", file=sys.stderr)
        return _EXIT_PRECONDITION
    root = roots[0]

    def q(expr: str):
        ctx = QQmlEngine.contextForObject(root)
        e = QQmlExpression(ctx, root, expr)
        r = e.evaluate()
        if e.hasError():
            raise RuntimeError(e.error().toString())
        return r[0] if isinstance(r, tuple) else r

    def settle(ms: int) -> None:
        loop = QEventLoop()
        QTimer.singleShot(ms, loop.quit)
        loop.exec()

    settle(120)
    # A real HoverSwell from Main.qml. Inline components only resolve through
    # their containing type, hence the Main.HoverSwell reference and the
    # directory import (which has to be relative: absolute paths are rejected).
    try:
        swell = q(
            'Qt.createQmlObject(\'import QtQuick; import "."; '
            'Main.HoverSwell { width: 40; height: 20 }\', this, "swellProbe")'
        )
    except RuntimeError as exc:
        print(f"could not instantiate HoverSwell: {exc}", file=sys.stderr)
        return _EXIT_PRECONDITION
    if swell is None:
        print("HoverSwell probe came back null", file=sys.stderr)
        return _EXIT_PRECONDITION

    def fade_ms(to_on: bool) -> float:
        """Wall-clock time for the opacity to finish travelling."""
        target = 1.0 if to_on else 0.0
        swell.setProperty("on", to_on)
        started = time.monotonic()
        while (time.monotonic() - started) * 1000 < _GIVE_UP_MS:
            settle(_SAMPLE_MS)
            if abs(float(swell.property("opacity")) - target) < 0.01:
                return (time.monotonic() - started) * 1000
        return float("inf")

    settle(60)
    in_ms = fade_ms(True)
    settle(300)  # fully lit and idle before timing the way back
    out_ms = fade_ms(False)

    ok_in = in_ms <= _IN_CEILING
    ok_out = _OUT_FLOOR <= out_ms <= _OUT_CEILING
    print(
        f"swell in={in_ms:.0f}ms (designed {_IN_MS}, must be <= {_IN_CEILING})"
        f" out={out_ms:.0f}ms (designed {_OUT_MS}, must be {_OUT_FLOOR}..{_OUT_CEILING})",
        flush=True,
    )
    return _EXIT_OK if ok_in and ok_out else _EXIT_REGRESSED


if __name__ == "__main__":
    raise SystemExit(_run_scenario())
