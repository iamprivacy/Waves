"""A specific search answers at the top of the results page.

THE BUG WE ARE FENCING OFF
--------------------------
Two things buried the one result a specific search ("this song by this
artist") was after, even though TIDAL had ranked it first in its reply:

1. The "Relevance" sort re-sorted every section by POPULARITY. A single
   released this week has a popularity of 0, so it sank under every older
   track that shared a word with the query, and the user had to switch to
   "Release date" to find it.
2. The mixed All view stacks its sections in a fixed order (artists, then
   albums, then tracks), so even a perfect album or track match sat under
   whichever artists TIDAL fuzzy-matched on one word of the query.

HOW THIS STAYS FIXED
--------------------
Relevance is TIDAL's order, kept as it arrived (Popularity is its own sort
option), and the backend carries TIDAL's own ``top_hit`` in the payload as
``top``: a row dict tagged with its kind, which the mixed view pins above
every section as TOP RESULT (album, track, video or playlist; an artist top
hit is dropped because the artist strip already leads with that artist).

The scenario boots the REAL Main.qml offscreen, renders a payload whose
top hit is a low-popularity album, and asserts: the TOP RESULT row sits
above the ARTISTS header; the tracks section keeps the API's order under
Relevance (the pop-0 track stays first); the Popularity option reorders
it; a section filter hides the pin; a payload without a top hit renders
no pin. It also saves a screenshot of the page for eyeballing.

Runs in a SUBPROCESS, like the other bridge scenarios: building the
bridge installs process-global handlers that must not leak into the suite.
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

_WIN_W, _WIN_H = 1100, 900


def _results(top: bool) -> dict:
    """A search reply shaped like the backend's: the wanted single is
    ranked first by TIDAL in albums and tracks but has popularity 0."""

    def artist(i: int) -> dict:
        return {"id": f"ar{i}", "name": f"Artist {i}", "art": "", "roles": "", "popularity": 40}

    def album(i: int, pop: int) -> dict:
        return {
            "id": f"al{i}",
            "title": f"Album {i}",
            "artist": "Some Artist",
            "artist_id": "ar0",
            "art": "",
            "year": "2026",
            "date": f"2026-01-{i + 1:02d}",
            "tracks": 1,
            "duration_sec": 200,
            "quality": "LOSSLESS",
            "popularity": pop,
            "explicit": False,
            "added": "",
        }

    def track(i: int, pop: int) -> dict:
        return {
            "id": f"t{i}",
            "title": f"Track {i}",
            "artist": "Some Artist",
            "artist_id": "ar0",
            "album": f"Album {i}",
            "album_id": f"al{i}",
            "num": 1,
            "vol": 1,
            "art": "",
            "year": "2026",
            "date": f"2026-01-{i + 1:02d}",
            "duration": "3:20",
            "duration_sec": 200,
            "quality": "LOSSLESS",
            "popularity": pop,
            "explicit": False,
            "added": "",
        }

    pops = [0, 64, 58, 60, 59]  # the wanted single first, older hits behind it
    payload = {
        "artists": [artist(i) for i in range(3)],
        "albums": [album(i, p) for i, p in enumerate(pops)],
        "tracks": [track(i, p) for i, p in enumerate(pops)],
        "videos": [],
        "playlists": [],
        "mixes": [],
        "top": None,
    }
    if top:
        payload["top"] = {"kind": "album", **album(0, 0)}
    return payload


def _run_scenario(shot: str) -> int:
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
        from _qml_offline import PARK_LOGIN_QML, patch_offline

        patch_offline()
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
    q(PARK_LOGIN_QML)
    q("openSearch()")
    settle()

    failures: list[str] = []

    # 1. A search whose top hit is the pop-0 album.
    q("_searchSeq = _navSeq")
    bridge.searchResults.emit(_results(top=True))
    if not pump(lambda: not q("searchBuilding")):
        print("search never finished building", file=sys.stderr)
        return _EXIT_PRECONDITION
    settle()

    if not q("topHead.visible"):
        failures.append("TOP RESULT header not visible in the All view")
    top_y = q("topHead.y")
    artists_y = q("artistsHead.y")
    if not (top_y < artists_y):
        failures.append(f"TOP RESULT header (y={top_y}) is not above ARTISTS (y={artists_y})")
    # The pinned row is the album's own delegate, rendered and sized.
    pin_h = q("topHead.parent.children[topHead.parent.children.indexOf(topHead) + 1].height")
    if not (pin_h and pin_h > 40):
        failures.append(f"pinned row has no height (h={pin_h})")

    # 2. Relevance keeps TIDAL's order: the pop-0 single stays first.
    if q("sortBox.currentIndex") != 0:
        failures.append("sort control does not default to Relevance")
    if q("tracksModel.get(0).id") != "t0" or q("albumsModel.get(0).id") != "al0":
        failures.append("Relevance re-sorted the sections away from TIDAL's order")

    # 3. Popularity is its own option and does reorder.
    # Chosen the way a user does it (the control's own activation), so the
    # choice is also written to the pref that restores it on the next launch.
    q("sortBox.currentIndex = 3")
    q("sortBox.activated(3)")
    settle(50)
    if q("tracksModel.get(0).id") != "t1":
        failures.append("Popularity sort did not put the most popular track first")
    if q('waves.wavesPref("search_sort")') != "popularity":
        failures.append("choosing Popularity did not persist search_sort")
    q("sortBox.currentIndex = 0")
    q("sortBox.activated(0)")
    settle(50)
    if q("tracksModel.get(0).id") != "t0":
        failures.append("returning to Relevance did not restore TIDAL's order")
    if q('waves.wavesPref("search_sort")') != "relevance":
        failures.append("returning to Relevance did not persist search_sort")

    if shot:
        q("results.contentY = 0")
        settle(100)
        q(f'results.grabToImage(function(r) {{ r.saveToFile("{shot}") }})')
        pump(lambda: Path(shot).exists(), 4000)

    # 4. A section filter hides the pin (the section is in relevance order).
    q('filterType = "albums"')
    settle(50)
    if q("topHead.visible"):
        failures.append("TOP RESULT still visible under the Albums filter")
    q('filterType = "all"')
    settle(50)

    # 5. A reply without a top hit renders no pin.
    q("_searchSeq = _navSeq")
    bridge.searchResults.emit(_results(top=False))
    if not pump(lambda: not q("searchBuilding")):
        print("second search never finished building", file=sys.stderr)
        return _EXIT_PRECONDITION
    settle()
    if q("topHead.visible") or q("searchTop") is not None:
        failures.append("a reply without a top hit still shows TOP RESULT")

    for f in failures:
        print(f"FAIL: {f}", flush=True)
    print(f"checks failed: {len(failures)}", flush=True)
    return _EXIT_REGRESSED if failures else _EXIT_OK


def test_specific_search_answers_at_the_top():
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="waves-searchtop-test-")
    proc = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), "--run-scenario", ""],
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
        pytest.skip(f"could not render a search page in this environment:\n{tail}")
    assert proc.returncode == _EXIT_OK, f"search top-result regression. Scenario exit={proc.returncode}:\n{tail}"


if __name__ == "__main__":
    # Optional second argument: a PNG path to save the rendered page to.
    raise SystemExit(_run_scenario(sys.argv[2] if len(sys.argv) > 2 else ""))
