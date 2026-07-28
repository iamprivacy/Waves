"""App-wide breadcrumb trail: section scoping, trim-on-revisit, crumb jumps.

WHAT THIS FENCES OFF
--------------------
The artist and browse sub-page back bars render the navHistory as a crumb
trail. Four behaviours make that trail livable and must not regress:

1. SECTION-SCOPED: the trail is the drill-in you are inside, never the tabs
   you crossed to get here. Switching tab starts a fresh trail; drilling
   extends it. Back and Forward still walk the WHOLE history across
   sections, they just stop spelling the crossing out in crumbs. This is
   what bounds the trail's depth.
2. TRIM-ON-REVISIT: arriving at a SECTION ROOT (Search, My Tidal, Browse
   home, Settings) already in the history cuts the history back to just
   before it. Without this, flipping between two tabs stacks Search >
   My Tidal > Search > ... twenty deep (reported from livetesting) and
   every crumb and Back press replays the oscillation. Deep pages are NOT
   trimmed (test_folder_back_navigation covers why).
3. The trim may only discard section roots. Anything real in the way (an
   artist page, a browse sub-page) means this was a journey, not a tab flip,
   and collapsing it made Back skip a page the user had actually opened.
4. navTo(i): clicking a crumb pops the history to that entry and restores
   it through the navBack path, so the trail truncates to the click. The
   pills pass navTo a whole-history index (crumbBase + ord), not a trail one.

The trim is debounced through a 0ms timer (it must observe the settled
state, not a half-switched one), so assertions settle the event loop first.

Runs in a SUBPROCESS for the same reason as test_search_results_surface_steal:
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

QML_MAIN = Path(__file__).resolve().parent.parent / "tidaler" / "waves_ui" / "qml" / "Main.qml"

# Every live crumb pill in the window as "label:lit" / "label:plain". The pills
# are Repeater delegates, which are visual children only (no QObject parent), so
# they have to be walked from the QML side rather than with findChildren.
_LIVE_PILLS = """(function () {
    var out = []
    function walk(o) {
        if (!o) return
        if (o.tag !== undefined && o.isLast !== undefined && !o.dying)
            out.push(o.tag + (o.isLast ? ":lit" : ":plain"))
        var kids = o.children || []
        for (var i = 0; i < kids.length; i++) walk(kids[i])
    }
    walk(contentItem)
    return out.join(",")
})()"""


def test_breadcrumb_trim_and_jump():
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="waves-breadcrumb-test-")
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
    assert proc.returncode == _EXIT_OK, f"breadcrumb navigation regressed. Scenario exit={proc.returncode}:\n{tail}"


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

    def q(expr: str):
        ctx = QQmlEngine.contextForObject(root)
        e = QQmlExpression(ctx, root, expr)
        r = e.evaluate()
        if e.hasError():
            raise RuntimeError(e.error().toString())
        return r[0] if isinstance(r, tuple) else r

    def settle(ms: int = 120) -> None:
        loop = QEventLoop()
        QTimer.singleShot(ms, loop.quit)
        loop.exec()

    settle()
    # Establish a known starting point: the search page.
    q("openSearch()")
    settle()
    q("navHistory = []")
    settle()

    # Journey: Search -> My Tidal -> Settings. Two history entries.
    q("openLibrary()")
    settle()
    # The settings gear's click path, one statement per expression (QML
    # expressions, not statement blocks).
    q("navPush()")
    q("markNav('settings')")
    q("settingsOpen = true")
    q("artistOpen = false")
    q("libraryOpen = false")
    settle()
    # Both entries are recorded, but the trail only shows the section the user
    # is in: the Search crumb belongs to the tab they left behind.
    grew = q("navHistory.length") == 2 and q("crumbLabels.join('|')") == "My Tidal|Settings"

    # 1. TRIM-ON-REVISIT: going back to Search (already crumb 0) must trim
    #    the whole oscillation away, not stack a third and fourth entry.
    q("openSearch()")
    settle()
    q("openLibrary()")
    settle()
    q("openSearch()")
    settle()
    # At Search again: Search is the trail's first entry, so the history
    # trims to empty and the trail is just the lit Search pill.
    trimmed = q("navHistory.length") == 0 and q("crumbLabels.join('|')") == "Search"

    # 2. navTo: rebuild Search -> My Tidal -> Settings, then jump straight
    #    to crumb 0 (Search). The history must pop whole, not one step.
    q("openLibrary()")
    settle()
    # The settings gear's click path, one statement per expression (QML
    # expressions, not statement blocks).
    q("navPush()")
    q("markNav('settings')")
    q("settingsOpen = true")
    q("artistOpen = false")
    q("libraryOpen = false")
    settle()
    q("navTo(0)")
    settle()
    jumped = (
        not bool(q("settingsOpen"))
        and not bool(q("libraryOpen"))
        and q("navOrigin") == "search"
        and q("navHistory.length") == 0
    )

    # 3. NAMED FROM THE FIRST FRAME: a keyed browse page's crumb must never
    #    flash the "Browse" fallback and then swap in the real title (reported
    #    from livetesting). Opening with the clicked card's title at hand names
    #    the crumb immediately; a hint-less open HOLDS the crumb back until the
    #    payload arrives with the name.
    q("openAlbumPage('9001', '', 'First Album')")
    settle()
    named = q("crumbLabels.join('|')") == "Search|First Album"
    q("openBrowseItem('album', '9002', '')")
    settle()
    # No hint: the trail shows only the history (whose last entry carries the
    # first page's hinted label), never a placeholder crumb.
    held = q("crumbLabels.join('|')") == "Search|First Album"
    # Holding the crumb back leaves the PARENT as the tail pill. It must not be
    # dressed as the page you are on: lit, bold and click-disabled is exactly
    # backwards while it is the one crumb that can get you out (and the wait is
    # indefinite if the payload errors). Read the real pills, so this covers the
    # delegate wiring and not just the flag feeding it.
    pills = q(_LIVE_PILLS)
    unlit_parent = bool(q("crumbTailPending")) and "First Album:lit" not in pills and pills != ""

    q("browsePage = ({key: 'item:album:9002', title: 'Second Album'})")
    settle()
    landed = q("crumbLabels.join('|')") == "Search|First Album|Second Album"
    # Named at last: the page you are on lights up again.
    lit = {p.split(":")[0] for p in q(_LIVE_PILLS).split(",") if p.endswith(":lit")}
    relit = not bool(q("crumbTailPending")) and lit == {"Second Album"}

    # 4. SECTION-SCOPED TRAIL. Crossing a tab boundary starts the trail over,
    #    and drilling from there extends it. The Search page left behind must
    #    not appear as a crumb in the Browse drill-in.
    q("openSearch()")
    settle()
    q("navHistory = []")
    # Scenario 3 left an album page open, and the Browse tab RETURNS to the page
    # it was left on. Put Browse back to its landing so this starts at the root.
    q("browsePageKey = ''")
    q("browsePage = null")
    q("browseStack = []")
    settle()
    q("openBrowse()")
    settle()
    q("openAlbumPage('9101', '', 'Deep Album')")
    settle()
    scoped = q("crumbLabels.join('|')") == "Browse|Deep Album" and q("navHistory.length") == 2

    # ... while Back still walks the whole history, across the tab boundary the
    # trail stopped drawing. Two presses: Browse landing, then Search.
    q("navBack()")
    settle()
    q("navBack()")
    settle()
    crossed = q("navOrigin") == "search" and not bool(q("browseOpen")) and q("navHistory.length") == 0

    # 5. THE TRIM MAY ONLY EAT SECTION ROOTS. The report's repro: Browse
    #    landing, open an artist, click Search, click Browse. The Browse press
    #    lands on the landing, which is already crumb 0, and the trim used to
    #    slice the history to nothing, taking the artist page and the search
    #    with it (unreachable by Back, by Forward and from the trail).
    q("openBrowse()")
    settle()
    q(
        "navHistory = [{v:'browse',key:'',label:'Browse',o:'browse'},"
        " {v:'artist',id:'55',label:'Some Artist',o:'browse'},"
        " {v:'search',label:'Search',o:'search'}]"
    )
    settle()  # the 0ms trim fires here, on the Browse landing
    kept_deep = q("navHistory.length") == 3

    print(
        f"grew={grew} trimmed={trimmed} jumped={jumped} named={named} held={held} landed={landed}"
        f" unlitParent={unlit_parent} relit={relit} scoped={scoped} crossed={crossed} keptDeep={kept_deep}",
        flush=True,
    )
    ok = grew and trimmed and jumped and named and held and landed and unlit_parent and relit
    ok = ok and scoped and crossed and kept_deep
    return _EXIT_OK if ok else _EXIT_REGRESSED


if __name__ == "__main__":
    raise SystemExit(_run_scenario())
