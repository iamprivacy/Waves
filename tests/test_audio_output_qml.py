"""The audio output pick reaches every player, on the real Main.qml.

WHAT THIS FENCES OFF
--------------------
Previews, videos and hover peeks each play through their own AudioOutput,
and all three must follow root.outDevice: the saved pick while the OS lists
it, else the system default. A pick made on the bridge moves all three at
once (mid-playback included, since it is one binding), the bar's output chip
names the live output, a pick that is unplugged falls back to the default and
turns the chip gold, and both lists (the bar's menu, Settings > Playback)
offer System default first and follow the pick. The chip leads with four
pills that go green and move only while sound plays, and sit still and
level otherwise.

The outputs are this machine's own (offscreen Qt still lists them). With
only one output there is nothing to move to, so the move step is skipped and
the fallback step still runs.

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

# A device id no machine lists: the "unplugged" pick.
_GONE = b"waves-test-gone-output".hex()


def test_the_audio_output_pick_reaches_every_player():
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    sandbox = tempfile.mkdtemp(prefix="waves-audio-output-test-")
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
    assert proc.returncode == _EXIT_OK, f"the audio output pick regressed. Scenario exit={proc.returncode}:\n{tail}"


def _run_scenario() -> int:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    try:
        from PySide6.QtCore import QEventLoop, QPoint, Qt, QTimer, QUrl
        from PySide6.QtGui import QGuiApplication
        from PySide6.QtQml import QQmlApplicationEngine, QQmlEngine, QQmlExpression
        from PySide6.QtTest import QTest
    except Exception as exc:
        print(f"Qt unavailable: {exc}", file=sys.stderr)
        return _EXIT_NO_QT

    from _qml_offline import PARK_LOGIN_QML, patch_offline

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
            raise RuntimeError(e.error().toString())
        if isinstance(r, tuple):
            r = r[0]
        return r.toVariant() if hasattr(r, "toVariant") else r

    def settle(ms: int = 120) -> None:
        loop = QEventLoop()
        QTimer.singleShot(ms, loop.quit)
        loop.exec()

    failures: list[str] = []

    def check(cond, what: str) -> None:
        if not cond:
            failures.append(what)

    players = ("previewOut", "videoOut", "peekOut")

    def heard() -> set:
        return {q(f"{p}.device.description") for p in players}

    settle(150)
    q(PARK_LOGIN_QML)
    rows = bridge.audioOutputs()
    if len(rows) < 2:
        print("this machine lists no audio output", file=sys.stderr)
        return _EXIT_PRECONDITION
    default = bridge.audioOutputLive()

    # Out of the box: every player on the system default, the idle chip names it.
    check(heard() == {default}, f"the players do not start on the system default: {heard()} vs {default!r}")
    check(bool(q("idleOut.visible")) and not bool(q("npOut.visible")), "the idle chip is not the one showing")
    check(q("idleOut.cur") == default, f"the idle chip does not name the live output: {q('idleOut.cur')!r}")
    check(not bool(q("root.outMissing")), "a fresh profile reads as an unplugged pick")

    # Both lists lead with System default and agree with the bridge.
    check(q("settingsPage.outOpts.length") == len(rows), "Settings > Playback lists other rows than the bridge")
    check(q("settingsPage.outPick") == "", "Settings > Playback does not start on System default")
    q("root.outMenuToggle(idleOut)")
    settle(450)
    check(bool(q("outMenu.visible")), "a click on the chip did not open the output menu")
    check(q("outMenu.opts.length") == len(rows), "the output menu lists other rows than the bridge")
    check(q("outMenu.opts[0].label") == "System default", "the output menu does not lead with System default")
    q("outMenu.close()")
    settle(300)

    # The menu opens above its chip and inside the window, read fresh on every
    # open: a window shrunk between two opens moves the chip, and a menu left
    # at the old spot hangs below the window's bottom edge.
    def menu_fits(when: str) -> None:
        q("root.outMenuToggle(idleOut)")
        settle(450)
        chip_top = q("idleOut.mapToItem(null, 0, 0).y")
        top, height, win = q("outMenu.y"), q("outMenu.height"), q("root.height")
        check(top + height <= chip_top, f"{when}: the menu does not sit above its chip ({top}+{height} vs {chip_top})")
        check(top >= 0 and top + height <= win, f"{when}: the menu hangs outside the window ({top}+{height} vs {win})")
        q("outMenu.close()")
        settle(300)

    q("root.height = 900")
    settle(400)
    menu_fits("a tall window")
    q("root.height = root.minimumHeight")
    settle(400)
    menu_fits("after the window shrank")

    # A row click applies at once, and the check beams over: the dot stays on
    # the row it left until the beam lands, then the menu closes. The arrow
    # keys move the one highlight; with motion off the click just closes.
    q("root.outMenuToggle(idleOut)")
    settle(450)
    check(q("outMenuList.hi") == -1, "the menu opened with a row highlighted")
    q("outMenuList.step(1)")
    check(q("outMenuList.hi") == 1, f"Down from System default did not highlight row 1: {q('outMenuList.hi')}")
    q("outMenuList.choose(1)")
    check(
        q("String(waves.wavesPref('audio_output'))") == str(rows[1]["value"]),
        "a row click did not save the pick at once",
    )
    check(bool(q("outMenuList.moving")), "the check did not set off for the new row")
    check(q("outMenuList.itemAtIndex(0).shown") is True, "the dot left the old row before the beam landed")
    settle(800)
    check(not bool(q("outMenuList.moving")), "the beam never landed")
    check(not bool(q("outMenu.visible")), "the menu did not close after the beam")
    q("root.hoverMotion = false")
    q("root.outMenuToggle(idleOut)")
    settle(450)
    q("outMenuList.choose(0)")
    check(
        not bool(q("outMenuList.moving")) and not bool(q("outMenu.visible")),
        "with motion off a pick did not close at once",
    )
    q("root.hoverMotion = Qt.binding(function() { return waves.wavesPref('hover_control_motion') !== false })")
    bridge.setWavesPref("audio_output", "")
    settle(300)

    # Return with no row highlighted keeps the pick and closes the menu.
    q("root.outMenuToggle(idleOut)")
    settle(450)
    q("outMenuList.forceActiveFocus()")
    QTest.keyClick(root, Qt.Key.Key_Return)
    settle(300)
    check(not bool(q("outMenu.visible")), "Return with nothing highlighted left the menu open")
    check(q("String(waves.wavesPref('audio_output') || '')") == "", "Return with nothing highlighted changed the pick")

    # More than ten outputs scroll inside the menu. Scrolled down, the
    # pointer still lights the row it is over, and the beam leaves from the
    # row the check sits on (not from where that row would be unscrolled).
    # (a hover needs the window shown and the boot cover gone)
    root.show()
    q("bootOverlay.done = true")
    settle(300)
    q("root.outMenuToggle(idleOut)")
    settle(450)
    q(
        "outMenu.opts = outMenu.opts.slice(0, 1).concat(Array.from({length: 13}, function(_, i) {"
        " return {value: 'waves-test-out-' + (i + 1), label: 'Test output ' + (i + 1),"
        " sub: '', 'default': false, missing: false} }))"
    )
    q("outMenu.pick = 'waves-test-out-9'")
    settle(200)
    q("outMenuList.positionViewAtIndex(13, ListView.End)")
    settle(200)
    check(q("outMenuList.contentY") > 0, "fourteen outputs did not scroll the menu")
    row = q("outMenuList.itemAtIndex(12).mapToItem(null, 60, 12)")
    QTest.mouseMove(root, QPoint(round(row.x()) - 40, round(row.y())))
    settle(80)
    QTest.mouseMove(root, QPoint(round(row.x()), round(row.y())))
    settle(150)
    check(q("outMenuList.hi") == 12, f"the pointer over row 12 of a scrolled menu lit row {q('outMenuList.hi')}")
    q("outMenuList.choose(12)")
    check(
        q("String(waves.wavesPref('audio_output'))") == "waves-test-out-12",
        "a pick in a scrolled menu saved another row",
    )
    lift = q("beamMark.mapToItem(null, 0, 0).y - outMenuList.itemAtIndex(9).mapToItem(null, 0, 0).y")
    check(abs(lift - 7) < 0.5, f"the beam left {lift - 7:+.0f} px off the row it leaves in a scrolled menu")
    settle(800)
    QTest.mouseMove(root, QPoint(4, 4))
    bridge.setWavesPref("audio_output", "")
    settle(300)

    # A pick moves every player at once, and both lists follow it.
    other = next((r for r in rows[1:] if not r["missing"] and r["label"] != default), None)
    if other is None:
        print("only one output here: the move step is skipped", file=sys.stderr)
    else:
        bridge.setWavesPref("audio_output", other["value"])
        settle(800)
        check(heard() == {other["label"]}, f"a pick did not reach every player: {heard()} vs {other['label']!r}")
        check(q("idleOut.cur") == other["label"], "the chip did not take the picked name")
        check(q("settingsPage.outPick") == other["value"], "Settings > Playback did not follow the pick")
        bridge.setWavesPref("audio_output", "")
        settle(800)
        check(heard() == {default}, "System default did not bring every player back")

    # An unplugged pick plays through the default and turns the chip gold.
    bridge.setWavesPref("audio_output", _GONE)
    # (as if it was picked by this name before it was unplugged)
    bridge._waves_prefs["audio_output_name"] = "Studio monitors (USB)"
    settle(800)
    check(bool(q("root.outMissing")), "an unplugged pick does not read as missing")
    check(heard() == {default}, f"an unplugged pick did not fall back to the default: {heard()}")
    check(q("String(idleOut.restCol) === String(root.gold)") is True, "an unplugged pick did not turn the chip gold")
    q("settingsPage.outRefresh()")
    gone = q("settingsPage.outOpts[1]")
    check(
        bool(gone) and gone.get("missing") is True and gone.get("label") == "Studio monitors (USB)",
        f"Settings does not list the unplugged pick by name under System default: {gone!r}",
    )
    bridge.setWavesPref("audio_output", "")
    settle(300)

    # The chip's sound sign: green with the pills moving while sound plays,
    # grey and still when paused or stopped. The clock stops at rest (a still
    # chip costs no repaints), the resting pills sit level on the chip's
    # midline, and the playing row's chip keeps its width through a pause.
    def pills_level(chip: str) -> bool:
        return bool(
            q(
                f"(function() {{ var p = {chip}.wave.children.filter(function(c) {{ return c.index !== undefined }});"
                f" return p.length === 4 && p.every(function(c) {{"
                f" return Math.abs(c.y + c.height / 2 - {chip}.wave.height / 2) < 0.01 }}) }})()"
            )
        )

    check(not bool(q("idleOut.wave.moving")), "the idle chip's pills move with nothing playing")
    check(pills_level("idleOut"), "the idle chip's resting pills do not sit level")
    q("root.previewKind = 'track'")
    q("root.previewPlaying = true")
    settle(600)
    check(bool(q("npOut.visible")), "the playing row's chip is not showing")
    check(q("npOut.lv") == 1, f"the chip did not ease to playing: lv {q('npOut.lv')}")
    check(q("String(npOut.restCol) === String(root.accent)") is True, "the output name is not green while sound plays")
    check(bool(q("npOut.wave.moving")), "the pills do not move while sound plays")
    t0 = q("npOut.wave.t")
    settle(200)
    check(q("npOut.wave.t") > t0, "the pills' clock does not advance while sound plays")
    playing_w = q("npOut.width")
    q("root.previewPlaying = false")
    settle(600)
    check(q("npOut.lv") == 0, f"the chip did not ease back on a pause: lv {q('npOut.lv')}")
    check(not bool(q("npOut.wave.moving")), "the pills keep moving while paused")
    check(q("String(npOut.restCol) === String(root.textLo)") is True, "the output name stays green while paused")
    check(pills_level("npOut"), "the paused chip's resting pills do not sit level")
    check(q("npOut.width") == playing_w, f"a pause changed the chip's width: {playing_w} to {q('npOut.width')}")
    q("root.hoverMotion = false")
    q("root.previewPlaying = true")
    check(q("npOut.lv") == 1 and not bool(q("npOut.wave.moving")), "with motion off the playing pills still move")
    q("root.hoverMotion = Qt.binding(function() { return waves.wavesPref('hover_control_motion') !== false })")
    q("root.previewPlaying = false")
    q("root.previewKind = ''")
    settle(300)

    if failures:
        for f in failures:
            print("REGRESSED:", f, file=sys.stderr)
        return _EXIT_REGRESSED
    print("audio output pick: OK")
    return _EXIT_OK


if __name__ == "__main__":
    if "--run-scenario" in sys.argv:
        raise SystemExit(_run_scenario())
