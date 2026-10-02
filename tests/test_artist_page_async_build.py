"""An artist page is up, finished, in the frame after the click.

WHAT THIS FENCES OFF
--------------------
The artist page's four sections (Top tracks, Albums, EPs & singles, Videos)
once built every row inline in ``onArtistLoaded``: 457 ms of frozen window
on a 113-album artist. The fix that followed incubated every row behind a
build veil and faded the finished page in, which ended the freeze but built
every row of every section, including the ones hidden behind each section's
SHOW ALL (156 rows for an 80-album artist, 21 on screen), and held the whole
page invisible until the last one existed: measured offscreen, 645 ms before
a big artist's page appeared, the same on a revisit served instantly from
the cache, and 285 ms to rebuild the page already open when its own artist
was clicked again. Reported as the page popping into existence on every
visit.

Pinned here, on the real Main.qml offscreen:

1. A row past its section's cap is never built until SHOW ALL shows it.
2. The rows the page opens on are built in the handler (the first frame is
   the finished screen, no veil, no fade); the rest incubate below the fold.
3. SHOW ALL builds what it reveals; SHOW LESS keeps those rows built (a
   second SHOW ALL rebuilds nothing).
4. A revalidate's refresh, and the page already open opened again, keep the
   delegates (the same objects, asked by identity) while landing every edit.
5. A Back restore builds the rows at the spot it lands on in the handler.
6. Refilling for another artist builds nothing on the way out: a removed
   delegate reads index -1 before it dies, which once read as "shown" and
   built every hidden row inline with undefined data (300 ms and a screen of
   "Unable to assign [undefined]" warnings). The scenario asserts the engine
   reported no warning at all.

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


def test_the_artist_page_opens_finished_and_builds_only_what_it_shows():
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="waves-artistbuild-test-")
    proc = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), "--run-scenario"],
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    tail = "\n".join((proc.stdout + proc.stderr).strip().splitlines()[-12:])
    import pytest

    if proc.returncode == _EXIT_NO_QT:
        pytest.skip("PySide6 / offscreen Qt unavailable")
    if proc.returncode == _EXIT_PRECONDITION:
        pytest.skip(f"could not set up the artist page in this environment:\n{tail}")
    assert proc.returncode == _EXIT_OK, (
        "the artist page built rows it does not show, held its opening screen back, or rebuilt rows it "
        "already had. "
        f"Scenario exit={proc.returncode}:\n{tail}"
    )


def _album(ident: str, title: str, *, quality: str = "LOSSLESS") -> dict:
    return {
        "id": ident,
        "title": title,
        "artist": "Artist One",
        "artist_id": "art1",
        "art": "",
        "year": "2020",
        "date": "2020-01-01",
        "tracks": 10,
        "quality": quality,
        "popularity": 50,
    }


def _track(ident: str, title: str) -> dict:
    return {
        "id": ident,
        "title": title,
        "artist": "Artist One",
        "artist_id": "art1",
        "album": "One",
        "album_id": "al1",
        "art": "",
        "year": "2020",
        "date": "2020-01-01",
        "duration": "3:20",
        "quality": "LOSSLESS",
        "popularity": 50,
    }


def _page(albums: list, eps: list, tracks: list, *, refresh: bool = False, ident: str = "art1") -> dict:
    payload = {
        "id": ident,
        "name": "Artist One",
        "art": "",
        "bio": "",
        "albums": albums,
        "eps": eps,
        "tracks": tracks,
        "videos": [],
    }
    if refresh:
        payload["refresh"] = True
    return payload


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
    # way out reads its rows as undefined and says so (fence 6).
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

    def ids(model: str) -> list:
        return [q(f"{model}.get({i}).id") for i in range(int(q(f"{model}.count")))]

    def built(rep: str, i: int) -> bool:
        return q(f"{rep}.itemAt({i}).item !== null") is True

    q(PARK_LOGIN_QML)
    settle(200)
    q("root.browseOpen = false; root.libraryOpen = false; root.settingsOpen = false")
    # Every section open and folded to its first 5, as a fresh install has
    # them; the sections' own prefs decide this in the app.
    q("root.artistTracksCollapsed = false; root.artistAlbumsCollapsed = false")
    q("root.artistEpsCollapsed = false; root.artistVideosCollapsed = false")
    q("root.topTracksExpanded = false; root.artistAlbumsExpanded = false")
    q("root.artistEpsExpanded = false; root.artistVideosExpanded = false")
    settle(50)

    albums = [
        _album(f"al{i}", t) for i, t in enumerate(("One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight"), 1)
    ]
    eps = [_album("ep1", "EP One"), _album("ep2", "EP Two")]
    tracks = [_track(f"tr{i}", f"Track {i}") for i in range(1, 7)]
    bridge.artistLoaded.emit(_page(albums, eps, tracks))

    # Before the event loop turns: the opening screen exists. At 720 px the
    # page opens on the header, the five top tracks and the first albums.
    if not q("root.artistOpen"):
        print("the artist page did not open", file=sys.stderr)
        return _EXIT_PRECONDITION
    for i in range(5):
        check(built("artistTracksRep", i), f"top track {i} is on the opening screen but was not built in the handler")
    check(built("artistAlbumsRep", 0), "the first album is on the opening screen but was not built in the handler")
    check(
        q("artistAlbumsRep.itemAt(4).asynchronous") is True,
        "an album below the fold was built inline instead of incubating",
    )
    # Fence 1: past the cap nothing is built, shown or not.
    check(not built("artistTracksRep", 5), "the 6th top track (behind SHOW ALL) was built")
    check(q("artistTracksRep.itemAt(5).active") is False, "a row behind SHOW ALL is active")
    for i in range(5, 8):
        check(not built("artistAlbumsRep", i), f"album {i} (behind SHOW ALL) was built")
    check(q("artistAlbumsRep.itemAt(0).opacity") == 1, "the rows are held at opacity behind a veil")

    # The rest of the shown rows land below the fold; still nothing past a cap.
    landed = pump(lambda: all(built("artistAlbumsRep", i) for i in range(5)) and built("artistEpsRep", 1))
    check(landed, "the shown rows below the fold never landed")
    for i in range(5, 8):
        check(not built("artistAlbumsRep", i), f"album {i} (behind SHOW ALL) was built in the background")
    check(not built("artistTracksRep", 5), "the 6th top track was built in the background")
    check(
        q("artistAlbumsRep.itemAt(2).item.title") == "Three",
        f"row 2 shows {q('artistAlbumsRep.itemAt(2).item.title')!r}, not Three",
    )
    check(q("artistTracksRep.itemAt(0).item.tId") == "tr1", "the first track row is not the first track")

    # Fence 3: SHOW ALL builds what it reveals (a screenful inline), SHOW
    # LESS keeps it built, and a second SHOW ALL rebuilds nothing.
    q("root.toggleArtistExpand('albums')")
    for i in range(5, 8):
        check(built("artistAlbumsRep", i), f"SHOW ALL revealed album {i} without building it in the click")
    seventh = q("String(artistAlbumsRep.itemAt(7).item)")
    q("root.toggleArtistExpand('albums')")
    settle(100)
    check(built("artistAlbumsRep", 7), "SHOW LESS threw away the rows SHOW ALL built")
    check(q("artistAlbumsRep.itemAt(7).visible") is False, "SHOW LESS left a row past the cap on screen")
    q("root.toggleArtistExpand('albums')")
    check(q("String(artistAlbumsRep.itemAt(7).item)") == seventh, "a second SHOW ALL rebuilt the row")
    q("root.toggleArtistExpand('albums')")
    settle(50)

    # Fence 4a: the revalidate's correction, followed by identity: al3 sits at
    # index 2 now and at index 0 afterwards.
    before = q("String(artistAlbumsRep.itemAt(2).item)")
    corrected = [
        albums[2],
        dict(albums[0], title="One (Remastered)", quality="HI_RES_LOSSLESS"),
        *albums[3:],
        _album("al9", "Nine"),
    ]
    bridge.artistLoaded.emit(_page(corrected, eps, tracks, refresh=True))
    settle(300)
    got = ids("artistAlbumsModel")
    check(
        got == ["al3", "al1", "al4", "al5", "al6", "al7", "al8", "al9"],
        f"the refresh left the wrong rows, or the wrong order: {got}",
    )
    check(
        q("artistAlbumsModel.get(1).title") == "One (Remastered)",
        f"a changed field was not written: {q('artistAlbumsModel.get(1).title')!r}",
    )
    check(q("artistAlbumsModel.get(1).quality") == "HI_RES_LOSSLESS", "a second changed field was not written")
    check(q("artistAlbumsRep.itemAt(1).item.title") == "One (Remastered)", "the kept row did not take the new title")
    check(q("String(artistAlbumsRep.itemAt(0).item)") == before, "the refresh rebuilt the rows instead of keeping them")
    check(
        ids("artistTracksModel") == [f"tr{i}" for i in range(1, 7)], "an unchanged section was disturbed by the refresh"
    )

    # Fence 4b: the page already open, opened again (its artist's link on an
    # album page): the rows are kept and the payload still lands.
    first_track = q("String(artistTracksRep.itemAt(0).item)")
    again = [dict(t) for t in tracks]
    again[0]["title"] = "Track 1 (Live)"
    bridge.artistLoaded.emit(_page(corrected, eps, again))
    check(q("String(artistTracksRep.itemAt(0).item)") == first_track, "opening the open artist again rebuilt its rows")
    check(q("artistTracksRep.itemAt(0).item.title") == "Track 1 (Live)", "opening it again did not land the payload")

    # Fence 6: another artist. The old rows die without building anything.
    other = [_album(f"x{i}", f"Other {i}") for i in range(1, 30)]
    bridge.artistLoaded.emit(_page(other, [], [_track("xt1", "Other track")], ident="art2"))
    settle(300)
    check(ids("artistAlbumsModel")[:2] == ["x1", "x2"], "the second artist's page did not fill")
    check(built("artistAlbumsRep", 0), "the second artist's opening rows were not built")
    check(not built("artistAlbumsRep", 9), "the second artist built a row behind SHOW ALL")

    # Fence 5: Back into the first artist, restored deep down a long page.
    # Every album shown (SHOW ALL), the restore lands at 1500 px: the rows
    # there are built in the handler, the ones far below still incubate.
    long_albums = [_album(f"lg{i}", f"Long {i}") for i in range(1, 61)]
    q("root.artistAlbumsExpanded = true")
    q("artistView.pendingRestoreKey = 'art1'; artistView.pendingRestoreY = 1500; root._navRestoring = true")
    bridge.artistLoaded.emit(_page(long_albums, [], tracks))
    # The screen at 1500 px holds albums ~11 to ~19 (header >= 170, the top
    # tracks ~450, an album row 76 with its spacing).
    for i in (12, 15, 18):
        check(built("artistAlbumsRep", i), f"album {i}, where the restore lands, was not built in the handler")
    check(not built("artistAlbumsRep", 59), "the last album, far below the restore, was built inline")
    # Fence 5b: the rows ABOVE the restored spot are not built in the handler
    # either (a prefix budget used to build the whole page above it inline).
    check(
        q("artistAlbumsRep.itemAt(0).asynchronous") is True, "the first album, far above the restore, was built inline"
    )
    check(not built("artistAlbumsRep", 0), "the first album, far above the restore, was built in the handler")
    q("root.artistAlbumsExpanded = false")
    settle(400)

    # Fence 7: a fresh long page fills in from the fold DOWN. Qt incubates
    # the newest Loader first, so a section created whole landed its last
    # rows first; rows are created a screenful at a time now.
    q("root.artistAlbumsExpanded = true")
    bridge.artistLoaded.emit(_page(long_albums, [], tracks, ident="art3"))
    fold = int(q("root._artistSyncAlbums"))
    check(0 < fold < 59, f"the opening screen holds {fold} albums; the fence needs rows above and below it")
    check(not built("artistAlbumsRep", 59), "the last album was built in the handler")
    check(
        q("artistAlbumsRep.itemAt(59).active") is False,
        "the last album was created at once, ahead of the rows under the fold",
    )
    landed_under_fold = pump(lambda: built("artistAlbumsRep", fold) and built("artistAlbumsRep", fold + 1))
    check(landed_under_fold, "the rows right under the fold never landed")
    check(not built("artistAlbumsRep", 59), "the last album landed before the rows right under the fold")
    check(pump(lambda: built("artistAlbumsRep", 59)), "the last album never landed")
    q("root.artistAlbumsExpanded = false")
    settle(200)

    # Fence 8: an artist's name inside a track row arms its own prefetch over
    # the row's; when the name's hover ends with the row still hovered, the
    # row's arm comes back (its HoverHandler never re-arms by itself).
    bridge._logged_in = True
    bridge.loggedInChanged.emit()
    rearmed = q(
        "(function(){"
        " var rc = { kind: 'track', album_id: 'alX' }, nc = { kind: 'artist', id: 'ar9' };"
        " root.hoverPrefetch(rc, 450); root.hoverPrefetch(nc); root.hoverPrefetchCancel(nc);"
        " var back = root._hoverPrefetchCard === rc && hoverPrefetchTimer.running && hoverPrefetchTimer.interval === 450;"
        " root.hoverPrefetchCancel(rc);"
        " return back && root._hoverPrefetchCard === null && !hoverPrefetchTimer.running"
        "})()"
    )
    check(rearmed is True, "a name's hover ending did not give the row its prefetch back")

    noisy = [w for w in warns if "Unable to assign" in w or "Binding loop" in w or "TypeError" in w]
    check(not noisy, "the engine warned: " + " | ".join(noisy[:4]))

    if failures:
        for f in failures:
            print("REGRESSED:", f, file=sys.stderr)
        return _EXIT_REGRESSED
    print("artist page opens finished and builds only what it shows: OK")
    return _EXIT_OK


if __name__ == "__main__":
    if "--run-scenario" in sys.argv:
        raise SystemExit(_run_scenario())
