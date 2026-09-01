"""Regression: late search results must not steal the active surface.

THE BUG WE ARE FENCING OFF
--------------------------
A search resolves over seconds (paginated fan-out), and a pasted TIDAL link
adds a ~0.6s decode before even issuing. ``onSearchResults`` used to switch
the whole app to the search page unconditionally when the payload landed, so
clicking an artist or album name while a search was still in flight opened
the page, then the late results yanked the user to the search surface: to
them, the click "navigated to search and searched" instead of opening the
page (reported from livetesting, hard to reproduce because it needs the
timing).

HOW THIS STAYS FIXED
--------------------
``markNav`` (the chokepoint every user navigation passes through) bumps a
``_navSeq`` counter, the search call sites snapshot it into ``_searchSeq``,
and ``onSearchResults`` drops any payload whose snapshot no longer matches
(or that arrives while Browse / My Tidal / Settings took over via paths that
skip markNav). This scenario boots the REAL Main.qml, emits searchResults
from the bridge exactly as the backend worker would, and asserts both sides:
a payload with no navigation in between still renders, a payload landing
after an openLibrary() is dropped.

Runs in a SUBPROCESS for the same reason as test_browse_back_scroll: building
the bridge installs process-global handlers that must not leak into the rest
of the suite.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

_EXIT_OK = 0  # fresh results rendered, late results were dropped
_EXIT_REGRESSED = 1  # late results stole the surface again (or fresh ones stopped rendering)
_EXIT_NO_QT = 77
_EXIT_PRECONDITION = 78

QML_MAIN = Path(__file__).resolve().parent.parent / "waves" / "waves_ui" / "qml" / "Main.qml"

_EMPTY_RESULTS = {"artists": [], "albums": [], "tracks": [], "videos": [], "playlists": [], "mixes": []}


def test_late_search_results_do_not_steal_surface():
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="waves-searchsteal-test-")
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
    assert (
        proc.returncode == _EXIT_OK
    ), f"late search results stole the surface again. Scenario exit={proc.returncode}:\n{tail}"


def _run_scenario() -> int:
    # THIS checkout's waves, not the venv's editable install: the scenario
    # drives this tree's Main.qml against this tree's bridge.
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

    def settle(ms: int = 120) -> None:
        loop = QEventLoop()
        QTimer.singleShot(ms, loop.quit)
        loop.exec()

    settle()
    # On the search page, as if the user just typed a query and hit Enter
    # (onAccepted snapshots _navSeq into _searchSeq before calling search).
    q("openSearch()")
    settle()
    q("_searchSeq = _navSeq")

    # 1. Results with no navigation in between must still render. The render
    #    marker is markNav("search render"); the history is NOT asserted on:
    #    a search issued from the search page pushes a Search snapshot that
    #    trim-on-revisit immediately collapses (Search > Search never stacks).
    bridge.searchResults.emit(dict(_EMPTY_RESULTS))
    settle()
    fresh_rendered = q("_navLabel") == "search render" and q("navOrigin") == "search"

    # 2. Issue another search, then navigate away BEFORE the payload lands
    #    (openLibrary bumps _navSeq through markNav). The late payload must
    #    be dropped whole: surface, origin and history all untouched.
    q("_searchSeq = _navSeq")
    q("openLibrary()")
    settle()
    before = q("navHistory.length")
    bridge.searchResults.emit(dict(_EMPTY_RESULTS))
    settle()
    stayed = bool(q("libraryOpen")) and q("navOrigin") == "library" and q("navHistory.length") == before

    print(f"freshRendered={fresh_rendered} stayedInLibrary={stayed}", flush=True)
    return _EXIT_OK if fresh_rendered and stayed else _EXIT_REGRESSED


if __name__ == "__main__":
    raise SystemExit(_run_scenario())
