"""After the reveal the boot pacer must pace like Qt's own window controller,
without the QtQuick Python binding.

THE FAILURES THIS FENCES OFF
----------------------------
1. The pacer's open slice once stayed 50 ms on a 16 ms tick for the whole
   session (its handback to the window's controller never found the
   window), so a 145-album artist page landed in 60 to 100 ms stalls, one
   per tick, and Search and Browse builds paid the same: scrolling
   stuttered while a page filled.
2. The first fix imported PySide6.QtQuick to reach the window's controller.
   tools/trim_qt_bundle.sh removes that binding from the packaged app (it
   drags the 21 MB QtOpenGL binding in), so the built app died at launch
   with "Qt dependencies missing". The app module must never import a
   binding the trim deletes, and the pacer must carry the open pacing by
   itself: a short slice right after each frame the window presents while
   frames flow, timer slices between frames otherwise, an idle beat when
   nothing incubates.

The pacing scenario runs against a REAL ApplicationWindow offscreen, in a
subprocess so the Qt boot cannot leak into the suite.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_EXIT_OK = 0
_EXIT_REGRESSED = 1
_EXIT_NO_QT = 77


def _trimmed_bindings() -> list[str]:
    trim = (_ROOT / "tools" / "trim_qt_bundle.sh").read_text(encoding="utf-8")
    match = re.search(r"^PYSIDE_BINDINGS=\(([^)]*)\)", trim, re.MULTILINE)
    assert match, "tools/trim_qt_bundle.sh no longer lists the PySide6 bindings it removes"
    return match.group(1).split()


def test_the_app_never_imports_a_binding_the_bundle_trim_deletes():
    bindings = _trimmed_bindings()
    assert "QtQuick" in bindings, "the trim list changed shape; re-check what the packaged app keeps"
    pattern = re.compile(r"^\s*(?:from\s+PySide6\.(\w+)\s+import|import\s+PySide6\.(\w+))", re.MULTILINE)
    offenders = []
    for path in sorted((_ROOT / "waves").rglob("*.py")):
        for m in pattern.finditer(path.read_text(encoding="utf-8")):
            module = m.group(1) or m.group(2)
            if module in bindings:
                offenders.append(f"{path.relative_to(_ROOT)}: PySide6.{module}")
    assert not offenders, (
        "these imports name a PySide6 binding tools/trim_qt_bundle.sh deletes from the packaged app, "
        "so the built app would die at launch with 'Qt dependencies missing':\n" + "\n".join(offenders)
    )


def test_the_open_slices_are_short():
    sys.path.insert(0, str(_ROOT))
    try:
        from waves.waves_ui.app import _BootPacedIncubation
    except Exception as exc:  # pragma: no cover - environment without Qt
        import pytest

        pytest.skip(f"PySide6 unavailable: {exc}")
    assert (
        _BootPacedIncubation._OPEN_SLICE_MS <= 12
    ), "a long open slice blocks the render loop's sync and drops a frame per tick"
    assert _BootPacedIncubation._FRAME_SLICE_MS <= 6, "the after-frame slice must fit beside the next frame"
    assert (
        _BootPacedIncubation._IDLE_TICK_MS > _BootPacedIncubation._TICK_MS
    ), "an idle pacer must beat slower than a busy one"


def test_the_slices_follow_the_screen_refresh_rate():
    """Qt's own window controller incubates for a third of a frame after each
    frame and twice that between frames. A fixed 5 ms after every frame of a
    120 Hz display was 60% of the GUI thread while a page filled in."""
    sys.path.insert(0, str(_ROOT))
    try:
        from waves.waves_ui.app import _BootPacedIncubation
    except Exception as exc:  # pragma: no cover - environment without Qt
        import pytest

        pytest.skip(f"PySide6 unavailable: {exc}")
    assert _BootPacedIncubation._slices_for(60.0)[:2] == (5, 10)
    assert _BootPacedIncubation._slices_for(120.0)[:2] == (2, 4)
    assert _BootPacedIncubation._slices_for(0.0)[:2] == (5, 10), "an unknown rate reads as 60 Hz"
    assert _BootPacedIncubation._slices_for(144.0)[:2] == (2, 4)
    assert abs(_BootPacedIncubation._slices_for(120.0)[2] - 1 / 120) < 1e-9


def test_queued_frame_slots_are_coalesced():
    """frameSwapped reaches the pacer over a queued connection, so a backlog
    of them lands in one turn after any pause (and an animation keeps frames
    flowing with nothing to show). One slice per cluster, not one per slot."""
    sys.path.insert(0, str(_ROOT))
    try:
        from waves.waves_ui.app import _BootPacedIncubation
    except Exception as exc:  # pragma: no cover - environment without Qt
        import pytest

        pytest.skip(f"PySide6 unavailable: {exc}")

    clock = {"now": 100.0}

    class Stub(_BootPacedIncubation):
        def __init__(self):
            # No Qt object behind it: the frame hook's bookkeeping only.
            self.slices = []
            self._boot = False
            self._last_frame = 0.0
            self._frame_slice_ms, self._open_slice_ms, self._frame_period_s = self._slices_for(120.0)

        def incubatingObjectCount(self):
            return 3

        def _set_tick(self, ms):
            pass

        def incubateFor(self, ms):
            self.slices.append(ms)

    import waves.waves_ui.app as app_module

    real = app_module.time.monotonic
    app_module.time.monotonic = lambda: clock["now"]
    try:
        pacer = Stub()
        pacer._frame()
        for _ in range(4):  # a backlog: four slots within one millisecond
            clock["now"] += 0.0002
            pacer._frame()
        clock["now"] += 1 / 120  # the next real frame
        pacer._frame()
    finally:
        app_module.time.monotonic = real
    assert pacer.slices == [2, 2]


def test_open_pacing_against_a_real_window():
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    proc = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), "--run-scenario"],
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    tail = "\n".join((proc.stdout + proc.stderr).strip().splitlines()[-10:])
    import pytest

    if proc.returncode == _EXIT_NO_QT:
        pytest.skip("PySide6 / offscreen Qt unavailable")
    assert proc.returncode == _EXIT_OK, f"the open pacing regressed. Scenario exit={proc.returncode}:\n{tail}"


_QML = """
import QtQuick
import QtQuick.Controls.Basic
ApplicationWindow {
    id: w
    visible: true
    width: 300
    height: 200
    property int built: 0
    property int wave: 0
    Column {
        Repeater {
            model: 200
            delegate: Loader {
                asynchronous: true
                width: 100
                height: 1
                sourceComponent: Rectangle { width: 100; height: 1; color: "red"; Repeater { model: 40; Text { text: "row" } } Component.onCompleted: w.built++ }
            }
        }
    }
    Column {
        x: 120
        Repeater {
            model: w.wave * 100
            delegate: Loader {
                asynchronous: true
                width: 100
                height: 1
                sourceComponent: Rectangle { width: 100; height: 1; color: "green"; Repeater { model: 40; Text { text: "row" } } Component.onCompleted: w.built++ }
            }
        }
    }
    Rectangle {
        width: 10; height: 10; color: "blue"
        NumberAnimation on x { id: motion; from: 0; to: 200; duration: 1200 }
    }
}
"""


def _run_scenario() -> int:
    sys.path.insert(0, str(_ROOT))
    try:
        from PySide6.QtCore import QTimer, QUrl
        from PySide6.QtGui import QGuiApplication
        from PySide6.QtQml import QQmlApplicationEngine
    except Exception as exc:
        print(f"Qt unavailable: {exc}", file=sys.stderr)
        return _EXIT_NO_QT

    app = QGuiApplication.instance() or QGuiApplication([])
    try:
        from waves.waves_ui.app import _BootPacedIncubation
    except Exception as exc:
        print(f"app module unavailable: {exc}", file=sys.stderr)
        return _EXIT_NO_QT
    if "PySide6.QtQuick" in sys.modules:
        print(
            "REGRESSED: importing the app module loaded PySide6.QtQuick, which the bundle trim deletes", file=sys.stderr
        )
        return _EXIT_REGRESSED

    class Counting(_BootPacedIncubation):
        frame_slices = 0
        timer_slices = 0

        def _frame(self):
            busy = bool(self.incubatingObjectCount()) and not self._boot
            super()._frame()
            if busy:
                self.frame_slices += 1

        def _tick(self):
            busy = bool(self.incubatingObjectCount()) and not self._boot
            fresh = self._last_frame and (__import__("time").monotonic() - self._last_frame) < self._FRAME_FRESH_S
            super()._tick()
            if busy and not fresh:
                self.timer_slices += 1

    folder = tempfile.mkdtemp(prefix="waves-open-pacing-")
    qml = Path(folder) / "Win.qml"
    qml.write_text(_QML)
    engine = QQmlApplicationEngine()
    pacer = Counting(app)
    engine.setIncubationController(pacer)
    engine.load(QUrl.fromLocalFile(str(qml)))
    roots = engine.rootObjects()
    if not roots:
        print("the window did not load", file=sys.stderr)
        return _EXIT_NO_QT
    win = roots[0]
    failures: list[str] = []
    if type(win).__name__ != "QWindow":
        failures.append(f"the root wraps as {type(win).__name__}: the scenario no longer mirrors the packaged app")
    pacer.attach_window(win)
    if pacer._relay is not None:
        failures.append("the frame hook was connected before the reveal: a crossing per launch-water frame")
    # The reveal: from here the pacer paces the open session by itself.
    pacer.release_throttle()
    if pacer._relay is None:
        failures.append("the frame hook did not attach to the window at the reveal")

    state = {"phase": 0}

    def phase_one():
        built = win.property("built")
        if built != 200:
            failures.append(f"only {built} of 200 rows built within 4.5 s after the reveal")
        if pacer.frame_slices == 0:
            failures.append(
                f"no incubation slice ran after a frame while the animation flowed (last frame at {pacer._last_frame:.3f}, timer slices {pacer.timer_slices})"
            )
        state["phase"] = 1
        QTimer.singleShot(400, phase_two)

    def phase_two():
        # The animation ended and nothing incubates: the timer must be idling.
        if pacer.incubatingObjectCount():
            failures.append("rows still incubating long after the build")
        if pacer._timer.interval() != pacer._IDLE_TICK_MS:
            failures.append(f"an idle pacer ticks every {pacer._timer.interval()} ms instead of {pacer._IDLE_TICK_MS}")
        before = pacer.timer_slices
        win.setProperty("wave", 1)
        QTimer.singleShot(3500, lambda: phase_three(before))

    def phase_three(before):
        built = win.property("built")
        if built != 300:
            failures.append(f"a build started while idle ended with {built} of 300 rows")
        if pacer._timer.interval() != pacer._IDLE_TICK_MS:
            failures.append("the pacer did not return to its idle beat after the second build")
        if pacer.timer_slices == before and pacer.frame_slices == 0:
            failures.append("the second build incubated through neither the timer nor the frame hook")
        app.quit()

    QTimer.singleShot(4500, phase_one)
    app.exec()
    if failures:
        for f in failures:
            print("REGRESSED:", f, file=sys.stderr)
        return _EXIT_REGRESSED
    print(f"open pacing OK: frame slices {pacer.frame_slices}, timer slices {pacer.timer_slices}")
    return _EXIT_OK


if __name__ == "__main__":
    if "--run-scenario" in sys.argv:
        raise SystemExit(_run_scenario())
