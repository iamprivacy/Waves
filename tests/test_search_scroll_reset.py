"""Regression: a NEW search always lands at the top of the results page.

THE BUG WE ARE FENCING OFF
--------------------------
The search results page is one Flickable (``results``) with every section
(ARTISTS, ALBUMS, TRACKS, ...) stacked inside, so it keeps a single scroll
position. ``onSearchResults`` used to render a fresh payload without touching
that position: search something, scroll down to the albums, search again, and
the new results appeared at the OLD offset, as if each section "remembered"
where you were. The horizontal ARTISTS strip kept its own sideways offset the
same way.

HOW THIS STAYS FIXED
--------------------
``onSearchResults`` resets ``results.contentY`` (and ``artistStrip.contentX``)
before filling the models. Scroll position is still deliberately KEPT in the
two places that should keep it: leaving and re-entering the Search tab
(``searchSaved`` in ``openSearch``), and Back navigation. Only a genuinely new
payload rendering is a fresh page.

This scenario boots the REAL Main.qml, renders one search, scrolls down (and
the artist strip sideways), renders a second search exactly as the backend
worker would, and asserts the page is back at the top.

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

_EXIT_OK = 0  # the second search rendered at the top
_EXIT_REGRESSED = 1  # the second search kept the first search's scroll offset
_EXIT_NO_QT = 77
_EXIT_PRECONDITION = 78

QML_MAIN = Path(__file__).resolve().parent.parent / "waves" / "waves_ui" / "qml" / "Main.qml"

# A fixed window keeps contentHeight/maxY deterministic across machines.
_WIN_W, _WIN_H = 1100, 720


def test_new_search_resets_results_scroll():
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="waves-searchscroll-test-")
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
        pytest.skip(f"could not set up a scrollable results page in this environment:\n{tail}")
    assert proc.returncode == _EXIT_OK, (
        "a new search rendered at the previous search's scroll offset "
        f"(scroll-reset regression). Scenario exit={proc.returncode}:\n{tail}"
    )


def _results(tag: str) -> dict:
    """A payload tall enough to scroll at the fixed window size: full role
    dicts so the ListModels define every role the delegates read."""

    def artist(i: int) -> dict:
        return {"id": f"{tag}ar{i}", "name": f"{tag} Artist {i}", "art": "", "roles": "", "popularity": 60 - i}

    def album(i: int) -> dict:
        return {
            "id": f"{tag}al{i}",
            "title": f"{tag} Album {i}",
            "artist": f"{tag} Artist",
            "artist_id": f"{tag}ar0",
            "art": "",
            "year": "2020",
            "date": "2020-01-01",
            "tracks": 10,
            "quality": "LOSSLESS",
            "popularity": 60 - i,
        }

    def track(i: int) -> dict:
        return {
            "id": f"{tag}tr{i}",
            "title": f"{tag} Track {i}",
            "artist": f"{tag} Artist",
            "artist_id": f"{tag}ar0",
            "album": f"{tag} Album 0",
            "album_id": f"{tag}al0",
            "art": "",
            "year": "2020",
            "date": "2020-01-01",
            "duration": "3:20",
            "quality": "LOSSLESS",
            "popularity": 60 - i,
        }

    def video(i: int) -> dict:
        return {
            "id": f"{tag}vd{i}",
            "title": f"{tag} Video {i}",
            "artist": f"{tag} Artist",
            "art": "",
            "art_big": "",
            "duration": "3:20",
            "explicit": False,
            "quality": "",
            "date": "2020-01-01",
        }

    def playlist(i: int) -> dict:
        return {"id": f"{tag}pl{i}", "title": f"{tag} Playlist {i}", "art": "", "tracks": 12, "creator": "Someone"}

    return {
        "artists": [artist(i) for i in range(12)],
        "albums": [album(i) for i in range(10)],
        "tracks": [track(i) for i in range(10)],
        "videos": [video(i) for i in range(6)],
        "playlists": [playlist(i) for i in range(5)],
        "mixes": [],
    }


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
    root.setProperty("width", _WIN_W)
    root.setProperty("height", _WIN_H)

    def q(expr: str):
        ctx = QQmlEngine.contextForObject(root)
        e = QQmlExpression(ctx, root, expr)
        r = e.evaluate()
        if e.hasError():
            raise RuntimeError(e.error().toString())
        return r[0] if isinstance(r, tuple) else r

    def pump(predicate, timeout_ms: int = 6000) -> bool:
        loop = QEventLoop()
        state = {"ok": False}

        def tick():
            try:
                if predicate():
                    state["ok"] = True
                    loop.quit()
            except Exception:
                loop.quit()

        poll = QTimer()
        poll.setInterval(25)
        poll.timeout.connect(tick)
        poll.start()
        QTimer.singleShot(timeout_ms, loop.quit)
        loop.exec()
        poll.stop()
        return state["ok"]

    def settle(ms: int = 200) -> None:
        pump(lambda: False, ms)

    settle()
    # On the search page, as if the user just typed a query and hit Enter
    # (onAccepted snapshots _navSeq into _searchSeq before calling search).
    q("openSearch()")
    settle()

    # 1. First search renders and builds a page tall enough to scroll.
    q("_searchSeq = _navSeq")
    bridge.searchResults.emit(_results("one"))
    if not pump(lambda: not q("searchBuilding") and q("results.contentHeight") > q("results.height") + 350):
        print("first search never became scrollable", file=sys.stderr)
        return _EXIT_PRECONDITION
    settle()

    # 2. Scroll down the page and sideways along the artist strip.
    q("results.contentY = 300")
    q("artistStrip.contentX = 150")
    if q("results.contentY") < 250 or q("artistStrip.contentX") < 100:
        print("could not establish non-top offsets", file=sys.stderr)
        return _EXIT_PRECONDITION

    # 3. A second search renders: the page must be back at the very top.
    q("_searchSeq = _navSeq")
    bridge.searchResults.emit(_results("two"))
    if not pump(lambda: not q("searchBuilding")):
        print("second search never finished building", file=sys.stderr)
        return _EXIT_PRECONDITION
    settle()

    y = q("results.contentY")
    x = q("artistStrip.contentX")
    reset = y <= 2 and x <= 2
    print(f"finalY={y:.0f} finalStripX={x:.0f} reset={reset}", flush=True)
    return _EXIT_OK if reset else _EXIT_REGRESSED


if __name__ == "__main__":
    raise SystemExit(_run_scenario())
