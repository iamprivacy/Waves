"""Search results are up, finished, in the frame after they arrive.

WHAT THIS FENCES OFF
--------------------
The search page built every row of every section, including the ones hidden
behind each section's SHOW ALL (231 rows for a full result set, 8 of them on
screen), and held the whole page at opacity 0 behind the loading hint until
the last one existed, then faded it in: measured offscreen, 650 ms before a
big result set appeared at all, the same again when the backend's cache
served the search a second time. The chips, the sort control and the
artists' SHOW ALL then rebuilt whole sections synchronously (110 to 220 ms of
frozen window per click).

The artist page's rules now hold here too. Pinned on the real Main.qml,
offscreen:

1. A row past its section's cap is never built until SHOW ALL or the
   section's own chip shows it, and an artist card is never built until the
   strip comes within a screen of it.
2. The rows the page opens on are built in the handler (no veil, no fade);
   the rest incubate below the fold with heights reserved, and a reserved
   height is the built row's height, so nothing on screen moves as they land.
3. SHOW ALL and a section chip build the rows they reveal on the screen in
   the click; SHOW LESS and the chip back to All keep them built, so the next
   SHOW ALL rebuilds nothing. The artists' grid is kept the same way.
4. A refresh, the sort control and a second search build nothing past a cap,
   and a refill builds nothing on its way out: a removed delegate reads index
   -1 before it dies, which must read as neither shown nor inline. The
   scenario asserts the engine reported no warning at all.
5. The one wait left is the library's first answer: a search made before it
   holds the page (the opening rows built all the same) until one event-loop
   pass after the publish, or the guard; a warm library never holds it.

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

_WIN_W, _WIN_H = 1100, 720


def test_search_results_open_finished_and_build_only_what_they_show():
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="waves-searchbuild-test-")
    proc = subprocess.run(  # (fixed argv: this file, one flag)
        [sys.executable, str(Path(__file__).resolve()), "--run-scenario"],
        env=env,
        capture_output=True,
        text=True,
        timeout=240,
    )
    tail = "\n".join((proc.stdout + proc.stderr).strip().splitlines()[-16:])
    import pytest

    if proc.returncode == _EXIT_NO_QT:
        pytest.skip("PySide6 / offscreen Qt unavailable")
    if proc.returncode == _EXIT_PRECONDITION:
        pytest.skip(f"could not set up the search page in this environment:\n{tail}")
    assert proc.returncode == _EXIT_OK, (
        "the search page built rows it does not show, held its opening screen back, or rebuilt rows it "
        f"already had. Scenario exit={proc.returncode}:\n{tail}"
    )


def _credit(k: int) -> list:
    return [{"id": f"ar{k % 3}", "name": f"Artist {k % 3}"}]


def _album(tag: str, k: int, title: str = "") -> dict:
    return {
        "id": f"{tag}al{k}",
        "title": title or f"Album {k}",
        "artist": f"Artist {k % 3}",
        "artist_id": f"ar{k % 3}",
        "artists": _credit(k),
        "art": "",
        "year": "2020",
        "date": "2020-01-01",
        "listed": "",
        "tracks": 10,
        "duration_sec": 2400,
        "quality": "LOSSLESS",
        "popularity": 50,
        "explicit": False,
    }


def _track(tag: str, k: int) -> dict:
    return {
        "id": f"{tag}tr{k}",
        "title": f"Track {k}",
        "artist": f"Artist {k % 3}",
        "artist_id": f"ar{k % 3}",
        "artists": _credit(k),
        "album": "One",
        "album_id": f"{tag}al0",
        "art": "",
        "year": "2020",
        "date": "2020-01-01",
        "duration": "3:20",
        "duration_sec": 200,
        "quality": "LOSSLESS",
        "popularity": 50,
        "explicit": False,
    }


def _results(tag: str, *, artists=40, albums=40, tracks=30, videos=12, playlists=8, mixes=8, top=True) -> dict:
    return {
        "artists": [
            # Half the artists arrive with their popularity, half without it
            # (the enrichment lands later): the card is the same height both
            # ways, so the page's reservations hold.
            {"id": f"{tag}ar{k}", "name": f"Artist {k}", "art": "", "roles": [], "popularity": 50 if k % 2 else -1}
            for k in range(artists)
        ],
        "albums": [_album(tag, k) for k in range(albums)],
        "tracks": [_track(tag, k) for k in range(tracks)],
        "videos": [
            {
                "id": f"{tag}v{k}",
                "title": f"Video {k}",
                "artist": "Artist 0",
                "artists": _credit(0),
                "art": "",
                "art_big": "",
                "duration": "4:00",
                "explicit": False,
                "date": "2001-01-01",
                "quality": "1080p",
            }
            for k in range(videos)
        ],
        "playlists": [
            {"id": f"{tag}pl{k}", "title": f"Playlist {k}", "art": "", "tracks": 20, "creator": "Someone"}
            for k in range(playlists)
        ],
        "mixes": [{"id": f"{tag}mx{k}", "title": f"Mix {k}", "art": "", "subtitle": "Mix"} for k in range(mixes)],
        "top": {"kind": "album", **_album(tag, 0)} if top else None,
    }


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
        from _qml_offline import PARK_LOGIN_QML, patch_offline

        from waves.waves_ui.app import _load_mono
        from waves.waves_ui.backend import WavesBridge
    except Exception as exc:
        print(f"Qt platform/backend unavailable: {exc}", file=sys.stderr)
        return _EXIT_NO_QT

    patch_offline()
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
    # Every warning the engine reports from here on: a delegate built on its
    # way out reads its row as undefined and says so (fence 4).
    warns: list[str] = []
    engine.warnings.connect(lambda ws: warns.extend(w.toString() for w in ws))

    def q(expr: str):
        ctx = QQmlEngine.contextForObject(root)
        e = QQmlExpression(ctx, root, expr)
        r = e.evaluate()
        if e.hasError():
            raise RuntimeError(e.error().toString())
        return r[0] if isinstance(r, tuple) else r

    def settle(ms: int = 250) -> None:
        loop = QEventLoop()
        QTimer.singleShot(ms, loop.quit)
        loop.exec()

    def pump(cond, limit_ms: int = 5000) -> bool:
        waited = 0
        while waited < limit_ms:
            if cond():
                return True
            settle(50)
            waited += 50
        return cond()

    failures: list[str] = []

    def check(cond, what: str) -> None:
        if not cond:
            failures.append(what)

    def built(rep: str, i: int) -> bool:
        return q(f"{rep}.itemAt({i}).item !== null") is True

    def obj(rep: str, i: int) -> str:
        return q(f"String({rep}.itemAt({i}).item)")

    def emit(payload: dict) -> None:
        q("root._searchSeq = root._navSeq")
        bridge.searchResults.emit(payload)

    q(PARK_LOGIN_QML)
    settle(200)
    q("root.browseOpen = false; root.libraryOpen = false; root.settingsOpen = false; root.artistOpen = false")
    q("root.navOrigin = 'search'")
    # Relevance order and every section folded to its cap, as a fresh install
    # has them; the sections' own prefs decide this in the app.
    q("sortBox.currentIndex = 0; root.sortAsc = false")
    q("root.searchArtistsExpanded = false; root.searchAlbumsExpanded = false; root.searchTracksExpanded = false")
    q("root.searchVideosExpanded = false; root.searchPlaylistsExpanded = false; root.searchMixesExpanded = false")
    if not q("waves.libraryIndexReady()"):
        print("the library reads as unanswered with none configured", file=sys.stderr)
        return _EXIT_PRECONDITION
    settle(50)

    # ---- Fences 1 and 2: the opening screen, in the handler ----------------
    emit(_results("a"))
    # Before the event loop turns. At 1100x720 the page opens on the top
    # result, the strip's first five cards and the first albums.
    if q("albumsModel.count") != 40 or not q("results.visible"):
        print("the search page did not fill", file=sys.stderr)
        return _EXIT_PRECONDITION
    check(built("searchTopRep", 0), "the top result is on the opening screen but was not built in the handler")
    for i in range(4):
        check(built("artistStripRep", i), f"artist card {i} is on the opening screen but was not built in the handler")
    check(built("albumsRep", 0), "the first album is on the opening screen but was not built in the handler")
    check(
        q("tracksRep.itemAt(0).asynchronous") is True,
        "a track far below the fold was built inline instead of incubating",
    )
    check(not q("root.searchBuilding"), "a warm library raised the veil")
    check(q("root.searchReveal") == 1 and q("albumsRep.itemAt(0).opacity") == 1, "the rows are held behind a veil")
    check(not q("searchBuildHint.active"), "the loading hint is up over finished results")
    # Fence 1: past every cap, nothing; past the strip's reach, nothing.
    for rep, i in (("albumsRep", 5), ("tracksRep", 5), ("videosRep", 6), ("playlistsRep", 5), ("mixesRep", 5)):
        check(q(f"{rep}.itemAt({i}).active") is False, f"{rep} row {i} (behind SHOW ALL) is active")
    check(q("artistStripRep.itemAt(30).active") is False, "an artist card far past the strip's edge is active")

    # The rest of the shown rows land below the fold; still nothing past a cap.
    landed = pump(
        lambda: all(built("albumsRep", i) for i in range(5))
        and all(built("tracksRep", i) for i in range(5))
        and all(built("videosRep", i) for i in range(6))
        and built("playlistsRep", 4)
        and built("mixesRep", 4)
        and built("artistStripRep", 9)
    )
    check(landed, "the shown rows below the fold, or the strip's next screen, never landed")
    for rep, i in (("albumsRep", 7), ("tracksRep", 7), ("videosRep", 9), ("playlistsRep", 7), ("mixesRep", 7)):
        check(not built(rep, i), f"{rep} row {i} (behind SHOW ALL) was built in the background")
    check(not built("artistStripRep", 25), "an artist card far past the strip's edge was built")
    check(q("albumsRep.itemAt(2).item.title") == "Album 2", "album row 2 is not the third album")

    # Fence 2: a reserved height is the built row's height, so nothing on
    # screen moves when a row lands (or never lands, past the strip's reach).
    for rep, built_i, unbuilt_i in (
        ("albumsRep", 0, 7),
        ("tracksRep", 0, 7),
        ("videosRep", 0, 9),
        ("playlistsRep", 0, 7),
        ("mixesRep", 0, 7),
        ("artistStripRep", 0, 30),
    ):
        got, want = q(f"{rep}.itemAt({unbuilt_i}).height"), q(f"{rep}.itemAt({built_i}).height")
        check(got == want, f"{rep}: an unbuilt row reserves {got}px, a built one is {want}px")

    # ---- Fence 3: SHOW ALL builds what it reveals, LESS keeps it ----------
    # Clicked where it is: the albums' SHOW ALL line on screen.
    q("results.contentY = 400")
    settle(100)
    q("root.toggleSearchSection('albums')")
    for i in (5, 6):
        check(built("albumsRep", i), f"SHOW ALL revealed album {i} on screen without building it in the click")
    check(not built("albumsRep", 39), "SHOW ALL built the last album, far below the screen, in the click")
    pump(lambda: built("albumsRep", 39))
    seventh = obj("albumsRep", 7)
    q("root.toggleSearchSection('albums')")
    settle(100)
    check(built("albumsRep", 7), "SHOW LESS threw away the rows SHOW ALL built")
    check(q("albumsRep.itemAt(7).visible") is False, "SHOW LESS left a row past the cap on screen")
    q("root.toggleSearchSection('albums')")
    check(obj("albumsRep", 7) == seventh, "a second SHOW ALL rebuilt the row")
    q("root.toggleSearchSection('albums')")
    q("results.contentY = 0")
    settle(100)

    # ---- Fence 3: a chip shows a section whole, and keeps it --------------
    q("root.setSearchFilter('tracks')")
    check(q("root.filterType") == "tracks", "the Tracks chip did not filter")
    for i in (5, 6):
        check(built("tracksRep", i), f"the Tracks chip showed track {i} on screen without building it in the click")
    check(not built("tracksRep", 29), "the Tracks chip built the last track, far below the screen, in the click")
    check(built("artistStripRep", 0), "the Tracks chip threw away the artist strip it hides")
    pump(lambda: built("tracksRep", 29))
    sixth = obj("tracksRep", 6)
    q("root.setSearchFilter('all')")
    settle(100)
    check(obj("tracksRep", 6) == sixth, "the chip back to All threw away the tracks the Tracks chip built")
    check(q("tracksRep.itemAt(6).visible") is False, "the chip back to All left a track past the cap on screen")
    check(built("artistStripRep", 0), "the chip back to All rebuilt the artist strip")

    # ---- the strip builds as it scrolls -----------------------------------
    q("artistStrip.contentX = 1500")
    check(q("artistStripRep.itemAt(14).active") is True, "scrolling the strip did not extend what it builds")
    check(pump(lambda: built("artistStripRep", 14)), "the cards the strip scrolled to never landed")
    check(not built("artistStripRep", 30), "scrolling the strip built cards a screen past where it stopped")
    q("artistStrip.contentX = 0")

    # ---- the artists' SHOW ALL grid: its screen in the click, then kept ---
    q("root.toggleSearchSection('artists')")
    check(q("artistFlow.visible") is True, "SHOW ALL did not put the artists' grid up")
    check(built("artistGridRep", 0), "the grid's first card is on screen but was not built in the click")
    check(not built("artistGridRep", 39), "the grid built its last card, far below the screen, in the click")
    check(
        q("artistGridRep.itemAt(0).height") == q("artistFlow.cardW") + 119,
        "the grid's reserved card height is not the built card's",
    )
    # Card 0 has no popularity yet, card 1 has: the same height either way,
    # or the enrichment landing steps every row of the page down.
    check(
        q("artistGridRep.itemAt(1).height") == q("artistGridRep.itemAt(0).height"),
        "a card with its popularity is taller than one still waiting for it",
    )
    pump(lambda: built("artistGridRep", 39))
    gridcard = obj("artistGridRep", 0)
    stripcard = obj("artistStripRep", 0)
    q("root.toggleSearchSection('artists')")
    settle(100)
    check(q("artistStrip.visible") is True, "SHOW LESS did not put the strip back")
    check(obj("artistStripRep", 0) == stripcard, "SHOW LESS rebuilt the strip it had")
    check(obj("artistGridRep", 0) == gridcard, "SHOW LESS threw away the grid SHOW ALL built")
    q("root.toggleSearchSection('artists')")
    check(obj("artistGridRep", 0) == gridcard, "a second SHOW ALL rebuilt the grid")
    q("root.toggleSearchSection('artists')")
    settle(100)

    # ---- Fence 4: a second search; then a chip from deep down -------------
    emit(_results("b"))
    settle(400)
    check(q("albumsModel.get(0).id") == "bal0", "the second search did not fill")
    check(not built("albumsRep", 5), "the second search built a row behind SHOW ALL")
    check(not built("artistGridRep", 0), "the second search built the grid nobody asked for")
    check(q("results.contentY") == 0, "the second search did not open at the top")
    q("results.contentY = 1200")
    settle(100)
    q("root.setSearchFilter('albums')")
    # The Albums chip lands at 1200 px: albums ~16 to ~27 are on that screen.
    for i in (17, 20, 24):
        check(built("albumsRep", i), f"album {i}, on the screen the chip landed on, was not built in the click")
    check(not built("albumsRep", 39), "the chip built the last album, below its screen, in the click")
    check(q("albumsRep.itemAt(8).asynchronous") is True, "an album above the chip's screen was built inline")
    q("root.setSearchFilter('all')")
    q("results.contentY = 0")
    settle(200)

    # ---- Fence 4: the sort control and a refresh build nothing past a cap --
    emit(_results("c"))
    settle(400)
    q("sortBox.currentIndex = 2; root.resortSearch()")
    # By name, the arrow down (the setting this scenario opened with).
    check(q("albumsModel.get(0).title") == "Album 9", "the name sort did not apply")
    check(built("albumsRep", 0), "the sort control rebuilt the first album without building it in the click")
    check(not built("albumsRep", 5), "the sort control built a row behind SHOW ALL")
    q("sortBox.currentIndex = 0; root.resortSearch()")
    settle(300)
    # The wire's correction: a new album first, the last ten gone. The rows
    # it removes were never built (past the cap) and read index -1 on their
    # way out with the section still holding rows, so this is where a dying
    # row read as shown would be built inline, and warn.
    corrected = _results("c")
    corrected["albums"] = [_album("c", 99, "A new album")] + corrected["albums"][:30]
    corrected["refresh"] = True
    first = obj("albumsRep", 0)
    emit(corrected)
    check(q("albumsModel.get(0).id") == "cal99", "the refresh did not land")
    check(q("albumsModel.count") == 31, "the refresh did not drop the albums the wire no longer has")
    check(built("albumsRep", 0), "the refresh's new first album was not built in place")
    check(obj("albumsRep", 1) == first, "the refresh rebuilt the rows it kept")
    settle(300)
    check(not built("albumsRep", 5), "the refresh built the row it pushed behind SHOW ALL")
    check(not built("albumsRep", 6), "the refresh built a row behind SHOW ALL")

    # ---- Fence 5: a search before the library has answered ----------------
    bridge._library_index = None
    bridge._library_root = lambda: "/library"  # configured, so "not yet" is a real state
    if q("waves.libraryIndexReady()"):
        print("could not make the index read as unanswered", file=sys.stderr)
        return _EXIT_PRECONDITION
    emit(_results("d"))
    check(q("root.searchBuilding") is True, "a search before the library's answer did not wait for it")
    check(q("root.searchReveal") == 0, "the waiting page is on screen without its badges")
    check(built("albumsRep", 0), "the waiting page did not build its opening screen in the handler")
    settle(150)
    check(q("root.searchBuilding") is True, "the page stopped waiting before the library answered")
    bridge._library_root = lambda: ""  # no library: an answer
    bridge.libraryPresenceChanged.emit()
    check(q("root.searchBuilding") is True, "the veil dropped before the badges re-resolved")
    check(pump(lambda: not q("root.searchBuilding"), 500), "the library's answer did not end the wait")
    check(pump(lambda: q("root.searchReveal") == 1, 1000), "the page did not fade in after the wait")
    # And the guard: a library that never answers holds the page 800 ms at most.
    bridge._library_root = lambda: "/library"
    emit(_results("e"))
    check(q("root.searchBuilding") is True, "the second cold search did not wait")
    settle(1100)
    check(q("root.searchBuilding") is False, "the wait outlived its guard with no answer coming")
    bridge._library_root = lambda: ""
    settle(200)

    noisy = [w for w in warns if "Unable to assign" in w or "Binding loop" in w or "TypeError" in w]
    check(not noisy, "the engine warned: " + " | ".join(noisy[:4]))

    if failures:
        for f in failures:
            print("REGRESSED:", f, file=sys.stderr)
        return _EXIT_REGRESSED
    print("search results open finished and build only what they show: OK")
    return _EXIT_OK


if __name__ == "__main__":
    if "--run-scenario" in sys.argv:
        raise SystemExit(_run_scenario())
