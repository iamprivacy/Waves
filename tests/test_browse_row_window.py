"""Track shelves on the Browse landing must actually build their rows.

WHAT THIS FENCES OFF
--------------------
Long track lists are windowed: each row is a fixed-height shell and only
the rows near the viewport load their real content. The window was aimed
with the PANE's scroll offset (minus a fixed 200px for "the header above
the list"), which is only the same thing as a row index while the track
list IS the page. On the landing, a track shelf sits some thousands of
pixels down a column of twenty sections, so the aim landed far past the
end of a five-row shelf, no row ever became active, and the whole shelf
rendered as empty row cards under its heading (reported from livetesting:
"entirely blank sections on browse", "Recommended new tracks").

HOW THIS STAYS FIXED
--------------------
Each section knows where it starts in the pane's scroll space (secTop) and
measures the window from its own first row, so the aim is expressed in the
shelf's own indices. A shelf shorter than the window it would be measured
against skips windowing entirely, which is exactly the case an aiming
error can blank completely.

Runs in a SUBPROCESS for the same reason as test_boot_handover_gate:
building the bridge installs process-global handlers that must not leak
into the rest of the suite.
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

QML_MAIN = Path(__file__).resolve().parent.parent / "tidaler" / "waves_ui" / "qml" / "Main.qml"


def test_landing_track_shelves_build_their_rows():
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="waves-row-window-test-")
    proc = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), "--run-scenario"],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    tail = "\n".join((proc.stdout + proc.stderr).strip().splitlines()[-10:])
    import pytest

    if proc.returncode == _EXIT_NO_QT:
        pytest.skip("PySide6 / offscreen Qt unavailable")
    if proc.returncode == _EXIT_PRECONDITION:
        pytest.skip(f"could not set up the scenario in this environment:\n{tail}")
    assert (
        proc.returncode == _EXIT_OK
    ), f"a Browse landing track shelf came up blank. Scenario exit={proc.returncode}:\n{tail}"


def _tracks_section(title: str, n: int) -> dict:
    return {
        "title": title,
        "rowKind": "tracks",
        "data": "",
        "items": [
            {
                "id": f"t{i}",
                "kind": "track",
                "title": f"Track {i}",
                "artist": "Some Artist",
                "album": "Some Album",
                "duration": "3:20",
                "num": i + 1,
            }
            for i in range(n)
        ],
    }


def _cards_section(title: str, n: int) -> dict:
    return {
        "title": title,
        "rowKind": "cards",
        "data": "",
        "items": [{"id": f"a{i}", "kind": "album", "title": f"Album {i}", "artist": "Some Artist"} for i in range(n)],
    }


def _run_scenario() -> int:
    # THIS checkout's tidaler, not the venv's editable install.
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
        from tidaler.waves_ui.app import _load_mono
        from tidaler.waves_ui.backend import WavesBridge
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
    root.setProperty("width", 1400)
    root.setProperty("height", 900)

    def q(expr: str):
        ctx = QQmlEngine.contextForObject(root)
        e = QQmlExpression(ctx, root, expr)
        r = e.evaluate()
        if e.hasError():
            raise RuntimeError(e.error().toString())
        return r[0] if isinstance(r, tuple) else r

    def settle(ms: int = 150) -> None:
        loop = QEventLoop()
        QTimer.singleShot(ms, loop.quit)
        loop.exec()

    settle()
    q("bootOverlay.done = true")
    q("bootContentShown = 1")
    q("browseBuilding = false")
    q("_browseAsyncBuild = false")  # build synchronously so one settle is enough

    # A landing shaped like the real one (the run logs show 20 sections), with
    # a five-row track shelf buried well down the column: it has to sit deeper
    # than the window's own slack (two screens, ~1800px) or the old aim landed
    # on it by luck and the shelf built anyway.
    sections = []
    for i in range(14):
        sections.append(_cards_section(f"Shelf {i}", 8))
    sections.append(_tracks_section("Recommended new tracks", 5))
    for i in range(14, 20):
        sections.append(_cards_section(f"Shelf {i}", 8))
    sections.append(_tracks_section("More new tracks", 5))
    root.setProperty("browseSections", sections)
    settle(700)

    # Every track-row shell, with where it sits in the column and whether it
    # built. JSON, not the object: a QML object comes back as an opaque
    # QJSValue. Shells are identified by properties they had BEFORE the fix
    # too, so this probe reads the same on either version.
    probe = """
    JSON.stringify((function() {
        var out = []
        function walk(item) {
            if (!item) return
            var kids = item.children || []
            for (var i = 0; i < kids.length; i++) {
                var k = kids[i]
                if (k && k.anchorRow !== undefined && k.hiRow !== undefined) {
                    var at = k.mapToItem(browseLandingCol, 0, 0)
                    out.push({ y: at ? at.y : -1, ready: k.status === 1 })
                }
                walk(k)
            }
        }
        walk(browseLandingCol)
        return out
    })())
    """
    import json

    shells = json.loads(str(q(probe)))
    if len(shells) != 10:
        print(f"scenario did not produce the two track shelves (shells={len(shells)})", file=sys.stderr)
        return _EXIT_PRECONDITION

    # Scroll the buried shelf into view. This is the whole point: at the top of
    # the landing the old aim happened to land on row 0 and the shelf built, so
    # the bug only shows once the pane has scrolled past the shelf's own depth.
    shelf_top = min(s["y"] for s in shells)
    pane_h = float(q("browseLanding.height"))
    q(f"browseLanding.contentY = {max(0.0, shelf_top - 200)}")
    settle(500)
    top = float(q("browseLanding.contentY"))
    if top <= 0:
        print(f"the landing would not scroll (shelf_top={shelf_top}), nothing is buried", file=sys.stderr)
        return _EXIT_PRECONDITION

    shells = json.loads(str(q(probe)))
    onscreen = [s for s in shells if top - 62 <= s["y"] <= top + pane_h]
    built = [s for s in onscreen if s["ready"]]
    print(f"shells={len(shells)} scrolled_to={top:.0f} onscreen={len(onscreen)} built={len(built)}")
    if not onscreen:
        print("no track row landed in the viewport, so the scenario proves nothing", file=sys.stderr)
        return _EXIT_PRECONDITION
    if len(built) != len(onscreen):
        print(
            f"REGRESSED: {len(onscreen) - len(built)} of {len(onscreen)} track rows in view "
            "never built (blank shelf under a heading)",
            file=sys.stderr,
        )
        return _EXIT_REGRESSED
    return _EXIT_OK


if __name__ == "__main__":
    if "--run-scenario" in sys.argv:
        sys.exit(_run_scenario())
    print("run me through pytest", file=sys.stderr)
    sys.exit(_EXIT_PRECONDITION)
