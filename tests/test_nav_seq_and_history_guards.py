"""Navigation bookkeeping must react to NAVIGATIONS, not to arrivals.

THREE BUGS FENCED OFF HERE
-------------------------
1. ``onBrowseLoaded`` called ``markNav``, which bumps ``_navSeq``. The Browse
   landing re-emits on every background revalidate (near enough every launch,
   now that the landing embeds the home-feed rows), so a search issued right
   after launch had its results discarded by ``onSearchResults``' staleness
   guard: the status bar read "n results" while the pane still showed the
   empty-state hint. Payload arrivals now go through ``markRender``, which
   stamps the perf timer without touching the sequence.

2. ``navBack`` marked the navigation and recorded a forward entry BEFORE its
   empty-history fallback, so a Back press that changed nothing (the search
   root, nothing recorded, no surface open) still bumped ``_navSeq``, killing
   an in-flight search, and left one dead Forward press per over-press.

3. ``crumbTrimRevisit``'s ``_navRestoring`` guard never fired: the trim runs
   off a 0ms timer, by which time ``_navRestore`` has already cleared the flag.
   A Back or Forward landing on a section root could therefore trim away the
   very history it had just walked into.

Runs in a SUBPROCESS for the same reason as test_search_results_surface_steal:
building the bridge installs process-global handlers.
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

_EMPTY_RESULTS = {"artists": [], "albums": [], "tracks": [], "videos": [], "playlists": [], "mixes": []}


def test_nav_sequence_and_history_guards():
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="waves-navguard-test-")
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
    assert proc.returncode == _EXIT_OK, f"navigation bookkeeping regressed. Scenario exit={proc.returncode}:\n{tail}"


def _run_scenario() -> int:
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

    # 1. A BACKGROUND BROWSE REVALIDATE IS NOT A NAVIGATION. The user is on the
    #    search page waiting for results; the boot revalidate lands first.
    q("openSearch()")
    settle()
    q("navHistory = []")
    q("_searchSeq = _navSeq")
    seq_before = q("_navSeq")
    bridge.browseLoaded.emit({"sections": [], "error": False})
    settle()
    seq_held = q("_navSeq") == seq_before
    bridge.searchResults.emit(dict(_EMPTY_RESULTS))
    settle()
    search_survived = q("_navLabel") == "search render" and q("navOrigin") == "search"

    # 2. A BACK PRESS THAT LANDS NOWHERE IS A COMPLETE NO-OP. Nothing recorded,
    #    no surface open: the mouse side button (or the macOS back gesture)
    #    must not bump the sequence or record a forward entry.
    q("openSearch()")
    settle()
    q("navHistory = []")
    q("navForwardHistory = []")
    q("_searchSeq = _navSeq")
    seq_before = q("_navSeq")
    q("navBack()")
    settle()
    noop_back = q("_navSeq") == seq_before and q("navForwardHistory.length") == 0
    bridge.searchResults.emit(dict(_EMPTY_RESULTS))
    settle()
    noop_back = noop_back and q("_navLabel") == "search render"

    # 3. BACK MUST NOT TRIM THE HISTORY IT JUST WALKED INTO. Landing on a
    #    section root that also sits earlier in the trail used to collapse the
    #    whole trail, so the next Back fell through to the level-up fallback.
    q("openSearch()")
    settle()
    q(
        "navHistory = [{v:'search',label:'Search'},"
        " {v:'library',cat:'home',label:'My Tidal'},"
        " {v:'search',label:'Search'}]"
    )
    # No settle here on purpose: the 0ms trim timer must not run between
    # seeding the history and the Back press, or it would collapse the seeded
    # trail itself (arriving at a root already in the trail is the trim's
    # intended job). What is under test is the trim that fires AFTER the Back.
    q("navBack()")
    settle()
    kept_history = q("navHistory.length") == 2

    print(
        f"seqHeld={seq_held} searchSurvived={search_survived} noopBack={noop_back} keptHistory={kept_history}",
        flush=True,
    )
    ok = seq_held and search_survived and noop_back and kept_history
    return _EXIT_OK if ok else _EXIT_REGRESSED


if __name__ == "__main__":
    raise SystemExit(_run_scenario())
