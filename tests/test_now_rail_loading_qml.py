"""The now-playing rail's waveform loads in, on the real Main.qml.

WHAT THIS FENCES OFF
--------------------
The rail used to draw a plain line until the clip's loudness arrived, then
pop into the waveform. Now, while the bridge reads the peaks, a ghost of
made-up bars breathes with a glint passing over it, and the real bars sweep
in left to right, each overshooting a touch and settling. When the bridge
answers with no waveform (a stream, a failed decode) the ghost stops at once
and the plain line stays; a wait that runs long calms back to the line too.
With hover motion off the bars land at once. No binding loops.

Runs in a SUBPROCESS like the other Main.qml scenarios: building the bridge
installs process-global handlers that must not leak into the suite.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

_EXIT_OK = 0
_EXIT_REGRESSED = 1
_EXIT_NO_QT = 77
_EXIT_PRECONDITION = 78

QML_MAIN = Path(__file__).resolve().parent.parent / "waves" / "waves_ui" / "qml" / "Main.qml"


def test_the_rail_breathes_then_the_waveform_sweeps_in():
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    sandbox = tempfile.mkdtemp(prefix="waves-now-rail-test-")
    env["HOME"] = sandbox
    env["XDG_CONFIG_HOME"] = str(Path(sandbox) / ".config")
    proc = subprocess.run(  # (fixed argv: this file, one flag)
        [sys.executable, str(Path(__file__).resolve()), "--run-scenario"],
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    tail = "\n".join((proc.stdout + proc.stderr).strip().splitlines()[-16:])
    import pytest

    if proc.returncode == _EXIT_NO_QT:
        pytest.skip("PySide6 / offscreen Qt unavailable")
    if proc.returncode == _EXIT_PRECONDITION:
        pytest.skip(f"could not set up the scenario in this environment:\n{tail}")
    assert (
        proc.returncode == _EXIT_OK
    ), f"the rail's waveform loading regressed. Scenario exit={proc.returncode}:\n{tail}"


def _run_scenario() -> int:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    try:
        from PySide6.QtCore import QEventLoop, QTimer, QUrl, qInstallMessageHandler
        from PySide6.QtGui import QGuiApplication
        from PySide6.QtQml import QQmlApplicationEngine, QQmlEngine, QQmlExpression
    except Exception as exc:
        print(f"Qt unavailable: {exc}", file=sys.stderr)
        return _EXIT_NO_QT

    from _qml_offline import PARK_LOGIN_QML, patch_offline

    loops: list[str] = []

    def on_message(_mode, _ctx, msg) -> None:
        if "Binding loop" in msg or "binding loop" in msg:
            loops.append(msg)

    qInstallMessageHandler(on_message)
    patch_offline()
    app = QGuiApplication.instance() or QGuiApplication([])
    try:
        from waves.waves_ui.app import _load_mono
        from waves.waves_ui.backend import WavesBridge
    except Exception as exc:
        print(f"Qt platform/backend unavailable: {exc}", file=sys.stderr)
        return _EXIT_NO_QT

    WavesBridge._library_root = lambda self: ""  # type: ignore[method-assign]
    WavesBridge.loadBrowse = lambda self: None  # type: ignore[method-assign]
    WavesBridge.refreshBrowse = lambda self: None  # type: ignore[method-assign]

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
        e = QQmlExpression(QQmlEngine.contextForObject(root), root, expr)
        r = e.evaluate()
        if e.hasError():
            raise RuntimeError(f"{expr}: {e.error().toString()}")
        if isinstance(r, tuple):
            r = r[0]
        return r.toVariant() if hasattr(r, "toVariant") else r

    def settle(ms: int = 150) -> None:
        loop = QEventLoop()
        QTimer.singleShot(ms, loop.quit)
        loop.exec()

    failures: list[str] = []

    def check(cond, what: str) -> None:
        if not cond:
            failures.append(what)

    def begin(track_id: str) -> None:
        # What startPreview leaves behind, without a real resolve.
        q(f'root.previewKind = "track"; root.previewId = "{track_id}"')
        q("root.previewPeaks = []; root.previewPeaksPending = true")
        q("root.previewPlaying = true; root.previewDuration = 213000; root.previewPosition = 77000")

    root.setVisible(True)
    settle(150)
    q("bootOverlay.done = true")
    q("bootContentShown = 1")
    q(PARK_LOGIN_QML)
    q("termsGate.visible = false")
    q("ffmpegGate.visible = false")
    q("root.width = 1280")
    q("root.hoverMotion = true")
    q('root.previewNowTitle = "Sample track title"')
    q('root.previewNowArtists = [{name:"Base Notion",id:"11"}]')
    begin("t1")
    settle(300)
    if not q("nowPlaying.visible") or not q("nowRail.visible"):
        print("the now-playing rail did not show", file=sys.stderr)
        return _EXIT_PRECONDITION

    # Waiting: the ghost shows (not the line) and breathes.
    check(bool(q("nowRail.waiting")) and bool(q("nowRail.bars")), "no ghost while the peaks are read")
    t0 = q("nowRail.t")
    settle(300)
    check(q("nowRail.t") > t0, "the ghost does not move")
    tallest = q("Math.max.apply(null, [0, 6, 12, 18, 24].map(function (c) { return nowRail.waitH(c, c * 3)[0] }))")
    check(tallest > 4, f"the ghost has no bars to speak of: {tallest}")

    # The peaks land: they sweep in and settle at their real height.
    bridge.previewPeaks.emit("track", "t1", [0.2 + 0.8 * ((i * 7) % 10) / 10 for i in range(600)])
    settle(60)
    check(not q("root.previewPeaksPending") and bool(q("nowRail.wave")), "the peaks did not land")
    g = q("nowRail.grow")
    check(0 < g < 1, f"the bars did not sweep in: grow {g} right after the peaks")
    settle(900)
    check(q("nowRail.grow") == 1, "the sweep did not finish")
    ends = q("[nowRail.arrive(0, 40), nowRail.arrive(39, 40)]")
    check(all(abs(v - 1) < 1e-6 for v in ends), f"the bars did not settle at their height: {ends}")
    # Mid-sweep: the left has arrived, the right waits, a bar in between overshoots.
    q("nowRail.grow = 0.5")
    mid = q("[nowRail.arrive(0, 40), nowRail.arrive(39, 40)]")
    peak = q("Math.max.apply(null, Array.from({length: 40}, function (_, c) { return nowRail.arrive(c, 40) }))")
    check(abs(mid[0] - 1) < 1e-6 and mid[1] == 0, f"the sweep does not run left to right: {mid}")
    check(peak > 1.02, f"no bar overshoots on the way in: {peak}")
    q("nowRail.grow = 1")

    # No waveform coming: the ghost stops at once, the plain line stays.
    begin("t2")
    settle(80)
    check(bool(q("nowRail.waiting")), "a new track did not wait for its peaks")
    bridge.previewPeaks.emit("track", "t2", [])
    settle(80)
    check(not q("nowRail.waiting") and not q("nowRail.bars"), "an empty answer left the ghost up")

    # A wait that runs long calms back to the line.
    begin("t3")
    settle(80)
    q("nowRail.calm = 0")
    check(not q("nowRail.bars"), "a calmed wait still draws the ghost")

    # No cover (previewNowArt is empty here): the rail wears the app's own
    # pair, the accent for the played part and the plain gray for the rest,
    # never the gray tinted toward the accent.
    check(q("nowRail.pal") is None, "a track with no cover still found a palette")
    acc = q("[Math.round(root.accent.r * 255), Math.round(root.accent.g * 255), Math.round(root.accent.b * 255)]")
    played = q("nowRail.fillRgb(true, 0).map(Math.round)")
    rest = q("nowRail.fillRgb(false, 0).map(Math.round)")
    check(played == acc, f"the played part is not the accent: {played} vs {acc}")
    check(rest == [70, 76, 87], f"the rest is not the plain gray: {rest}")

    # A stale answer for another track is ignored.
    begin("t4")
    settle(80)
    bridge.previewPeaks.emit("track", "t3", [0.5] * 600)
    settle(80)
    check(bool(q("nowRail.waiting")) and not q("nowRail.wave"), "another track's peaks landed on this one")

    # Hover motion off: the bars land at once.
    q("root.hoverMotion = false")
    bridge.previewPeaks.emit("track", "t4", [0.5] * 600)
    settle(60)
    check(q("nowRail.grow") == 1, "with motion off the bars still animate in")
    q("root.stopPreview()")
    check(not q("root.previewPeaksPending"), "stopping the preview left the peaks pending")

    check(not loops, f"binding loops: {loops[:3]}")
    if failures:
        for f in failures:
            print("FAIL:", f, file=sys.stderr)
        return _EXIT_REGRESSED
    print("ok")
    return _EXIT_OK


if __name__ == "__main__" and "--run-scenario" in sys.argv:
    sys.exit(_run_scenario())
