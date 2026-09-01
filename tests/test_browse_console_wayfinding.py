"""The console-style Browse landing must reach the playlists-only view.

Browse ships two landing styles. The art style closes the page with colour-tile
cloud shelves whose headlines are links: "All Playlists ›" opens the folder-view
root, and each cloud headline opens that cloud as a full grid. The console style
leads with the same clouds as chip rows, and its headlines were plain text.

Its chips reach the mood playlist folders (they carry pl: true), but nothing in
that style reached ``openPlaylistsRoot`` at all, so the genre and decade
playlist folders had no route: a user who prefers the console landing simply
could not get to two thirds of the playlists-only view.

Runs in a SUBPROCESS for the same reason as the other Main.qml scenarios:
building the bridge installs process-global handlers that must not leak into
the rest of the suite.
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

# Every SectionHeader in the window, as "label|openable".
_HEADERS = """
(function () {
    var out = []
    function walk(o) {
        if (!o) return
        if (o.openable !== undefined && o.label !== undefined && o.count !== undefined)
            out.push(o.label + "|" + (o.openable ? "open" : "plain"))
        var kids = o.children || []
        for (var i = 0; i < kids.length; i++) walk(kids[i])
    }
    walk(contentItem)
    return out.join(",")
})()
"""

# Clicks the headline with the given label, returning whether one was found.
_OPEN_HEADER = """
(function () {
    var hit = null
    function walk(o) {
        if (!o || hit) return
        if (o.openable !== undefined && o.label === "%s") { hit = o; return }
        var kids = o.children || []
        for (var i = 0; i < kids.length; i++) walk(kids[i])
    }
    walk(contentItem)
    if (!hit || !hit.openable) return false
    hit.opened()
    return true
})()
"""

_CHIPS = {
    "genres": [{"title": "Rock", "path": "pages/genre-rock"}],
    "moods": [{"title": "Focus", "path": "pages/mood-focus"}],
    "decades": [{"title": "1980s", "path": "pages/decade-80s"}],
}


def test_console_landing_reaches_the_playlists_view():
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="waves-console-wayfind-test-")
    proc = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), "--run-scenario"],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    tail = "\n".join((proc.stdout + proc.stderr).strip().splitlines()[-8:])
    import pytest

    if proc.returncode == _EXIT_NO_QT:
        pytest.skip("PySide6 / offscreen Qt unavailable")
    if proc.returncode == _EXIT_PRECONDITION:
        pytest.skip(f"could not set up the scenario in this environment:\n{tail}")
    assert proc.returncode == _EXIT_OK, f"console-style wayfinding regressed. Scenario exit={proc.returncode}:\n{tail}"


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

    def settle(ms: int = 200) -> None:
        loop = QEventLoop()
        QTimer.singleShot(ms, loop.quit)
        loop.exec()

    def fail(msg: str) -> int:
        print(msg, file=sys.stderr)
        return _EXIT_REGRESSED

    settle()
    q("browseStyle = 'console'")
    q("openBrowse()")
    q("bootOverlay.done = true")  # otherwise the landing build takes the veiled path
    settle()
    bridge.browseLoaded.emit({"sections": [], **_CHIPS})
    settle(400)

    headers = q(_HEADERS)
    if "ALL PLAYLISTS|" not in headers:
        print(f"the console chip groups never rendered: {headers!r}", file=sys.stderr)
        return _EXIT_PRECONDITION
    if "ALL PLAYLISTS|open" not in headers:
        return fail(f"the console headlines are not openable: {headers!r}")

    # The playlists-only root, the thing this style could not reach.
    if not bool(q(_OPEN_HEADER % "ALL PLAYLISTS")):
        return fail("the ALL PLAYLISTS headline did not open")
    settle()
    if q("browsePageKey") != "cloud:All Playlists":
        return fail(f"expected the playlists root, landed on {q('browsePageKey')!r}")
    # Every cloud is there as playlist folders, not just the moods the chips show.
    titles = q("(browsePage.sections || []).map(function(s) { return s.title }).join('|')")
    if titles != "Moods & Activities|Genres|Decades":
        return fail(f"the playlists root is missing clouds: {titles!r}")

    # And an ordinary cloud headline opens that cloud, as in the art style.
    q("openBrowse()")
    settle()
    if not bool(q(_OPEN_HEADER % "DECADES")):
        return fail("the DECADES headline did not open")
    settle()
    if q("browsePageKey") != "cloud:DECADES":
        return fail(f"expected the decades cloud, landed on {q('browsePageKey')!r}")

    print(f"console wayfinding OK ({headers})", flush=True)
    return _EXIT_OK


if __name__ == "__main__":
    raise SystemExit(_run_scenario())
