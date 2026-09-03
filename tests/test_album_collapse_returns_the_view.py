"""Collapsing an expanded album row brings the page back to where it was.

WHAT THIS FENCES OFF
--------------------
Expanding an album row pulls the row up to the expand anchor line so the
panel has room, which scrolls the rows above it off the top of the view.
Collapsing the row used to leave the view there: the albums the user had
been looking at stayed off screen and a scroll back up was owed every time
(livetest report). The expand now keeps the spot the view left, and the
collapse returns to it with the same motion. A view the user has since
scrolled back above that spot is left alone, and an expand that did not
move the view (the row was already high enough) owes nothing.

The panel also has to FOLD over that same beat: a Column drops an invisible
child from its layout in the frame it goes invisible, so a panel hidden on
the press shut the gap at once and left the scroll gliding after it, which
is what made the collapse feel abrupt.

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


def test_collapsing_an_album_row_returns_the_view_to_where_it_was():
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="waves-collapse-test-")
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
    assert proc.returncode == _EXIT_OK, f"the collapse scroll regressed. Scenario exit={proc.returncode}:\n{tail}"


_ROW = {
    "artist": "Lab Artist",
    "artist_id": "",
    "artists": [],
    "art": "",
    "year": "2024",
    "date": "2024-03-11",
    "explicit": False,
    "added": "",
    "popularity": 50,
}

_ALBUMS = 30


def _payload() -> dict:
    albums = [
        dict(_ROW, id=f"a{i}", title=f"Album {i}", tracks=10, duration_sec=2400, quality="HI-RES")
        for i in range(_ALBUMS)
    ]
    return {"artists": [], "albums": albums, "tracks": [], "videos": [], "playlists": [], "mixes": []}


_FIND_BLOCKS = """
(function() {
    var out = {};
    function walk(o) {
        if (!o) return;
        var kids = o.children;
        if (!kids) return;
        for (var i = 0; i < kids.length; ++i) {
            var c = kids[i];
            if (c && typeof c.albumId !== "undefined" && typeof c.toggle === "function"
                && typeof c.trackList !== "undefined" && c.visible)
                out[c.albumId] = c;
            walk(c);
        }
    }
    walk(root.contentItem);
    return out;
})()
"""


def _run_scenario() -> int:  # noqa: C901 (one straight scenario)
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    try:
        from PySide6.QtCore import QEventLoop, QTimer, QUrl
        from PySide6.QtGui import QGuiApplication
        from PySide6.QtQml import QQmlApplicationEngine, QQmlEngine, QQmlExpression
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
    WavesBridge.loadAlbumTracks = lambda self, album_id: None  # type: ignore[method-assign]

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
        expr = expr.replace("root._ab", "(" + _FIND_BLOCKS + ")")
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

    settle(150)
    q(PARK_LOGIN_QML)
    bridge._logged_in = True
    bridge.loggedInChanged.emit()
    q("openSearch()")
    settle()
    q("_searchSeq = _navSeq")
    bridge.searchResults.emit(_payload())
    settle(600)
    # The albums chip lifts the five-row cap, so the page is long enough to
    # scroll; the panels have their (empty) track lists so no fetch is owed.
    q("root.filterType = 'albums'")
    q(
        "root.trackCache = (function(){ var m = {}; for (var i = 0; i < %d; ++i) m['a' + i] = []; return m })()"
        % _ALBUMS
    )
    settle(400)
    if not q("results.contentHeight > results.height * 2"):
        print("the results page is not long enough to scroll", file=sys.stderr)
        return _EXIT_PRECONDITION

    # Scroll so a row sits low in the view, the way a row you reach by
    # scrolling does, then expand it: the view moves up to the anchor line.
    q("results.contentY = Math.floor(results.contentHeight * 0.35)")
    settle(200)
    y0 = q("results.contentY")
    keys = q("Object.keys(root._ab)")
    low = None
    for k in keys:
        rel = q(f"root._ab['{k}'].mapToItem(results, 0, 0).y")
        if rel is not None and results_h(q) * 0.55 < rel < results_h(q) * 0.85:
            low = k
            break
    if low is None:
        print("no album row sits low in the view:", keys, file=sys.stderr)
        return _EXIT_PRECONDITION
    q(f"root._ab['{low}'].toggle()")
    settle(700)
    check(bool(q(f"root.expandedAlbums['{low}'] === true")), "the row did not expand")
    y1 = q("results.contentY")
    check(y1 > y0 + 20, f"expanding did not bring the row up (contentY {y0} -> {y1})")

    # Collapse: the panel folds shut over the same beat as the scroll back.
    # A Column drops an invisible child from its layout outright, so a panel
    # that went invisible on the press closed the whole gap in one frame while
    # the view was still gliding: the page snapped and the scroll trailed it.
    # children[1] is the expanded panel (children[0] is the 64px row).
    panel = f"root._ab['{low}'].children[1]"
    full = q(f"{panel}.height")
    check(full > 40, f"the expanded panel has no height to fold ({full})")
    # The gap between the row and the panel has to fold with it. A fixed
    # spacing survives to the frame the folded panel leaves the layout and
    # then goes in one step: a small extra collapse after the motion has
    # visibly ended (livetest: "an additional collapse at the end").
    check(q(f"root._ab['{low}'].spacing") == 6, "an expanded row lost the gap under it")
    q(f"root._ab['{low}'].toggle()")
    settle(60)
    mid = q(f"{panel}.height")
    check(bool(q(f"{panel}.visible")), "the panel left the layout the instant it was collapsed")
    check(mid is not None and 0 < mid < full, f"the panel snapped shut instead of folding ({full} -> {mid})")
    settle(700)
    check(q(f"{panel}.height") <= 0.5, f"the panel never finished folding ({q(f'{panel}.height')})")
    check(not bool(q(f"{panel}.visible")), "the folded panel stayed in the layout")
    check(
        q(f"root._ab['{low}'].spacing") == 0,
        f"the gap under the row outlived the fold, a step left over ({q(f'root._ab[{low!r}].spacing')})",
    )
    check(not bool(q(f"root.expandedAlbums['{low}'] === true")), "the row did not collapse")
    y2 = q("results.contentY")
    check(abs(y2 - y0) <= 1.5, f"collapsing left the view pushed down (contentY {y0} -> {y1} -> {y2})")
    check(q(f"root.expandReturnY['{low}'] === undefined"), "the spot outlived the collapse")

    # Scrolled back above the spot while expanded: the collapse leaves the
    # view alone (there is nothing to return to).
    q(f"root._ab['{low}'].toggle()")
    settle(700)
    above = max(0, y0 - 150)
    q(f"results.contentY = {above}")
    settle(200)
    q(f"root._ab['{low}'].toggle()")
    settle(700)
    check(abs(q("results.contentY") - above) <= 1.5, "a collapse moved a view the user had already scrolled back")

    # A row already high in the view expands without moving it, and its
    # collapse owes nothing.
    q("results.contentY = 0")
    settle(200)
    top = keys[0]
    q(f"root._ab['{top}'].toggle()")
    settle(700)
    check(q("results.contentY") == 0, "expanding the first row moved the view")
    check(q(f"root.expandReturnY['{top}'] === undefined"), "an expand that did not move the view kept a spot")
    q(f"root._ab['{top}'].toggle()")
    settle(700)
    check(q("results.contentY") == 0, "collapsing the first row moved the view")

    if failures:
        for f in failures:
            print("REGRESSED:", f, file=sys.stderr)
        return _EXIT_REGRESSED
    print("album collapse returns the view: OK")
    return _EXIT_OK


def results_h(q) -> float:
    return float(q("results.height"))


if __name__ == "__main__":
    if "--run-scenario" in sys.argv:
        raise SystemExit(_run_scenario())
