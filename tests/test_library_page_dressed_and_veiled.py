"""A My Tidal page is built from its baked verdicts, and a cold one waits.

WHAT THIS FENCES OFF
--------------------
Two halves of the same page build.

1. THE ROWS ARRIVE DRESSED. A category page used to be emitted bare and every
   row's pill, its download button's claim face and every artist card's strip
   asked the bridge at creation (three crossings per album row, measured, on
   the GUI thread) for answers that were all knowable before the emit. The rows now carry ``lib`` and ``libStamp`` (see
   test_library_rows_dressing for the Python half) and the badges read them:
   a dressed page makes ZERO presence calls while it builds, and its badges
   are simply there, not fading in.

2. A COLD PAGE WAITS FOR THE LIBRARY. Before the index has published once,
   every verdict reads "not present", so a page built in that window rendered
   with no badges and then lit every one of them a moment later: to the
   reader, the same pop the navigation fix ended everywhere else. The pane
   now holds behind a veil (opacity 0, badges' fade off) until the first
   publish has landed and every badge has re-resolved, then fades in whole.
   And the veil is bounded: a library that never answers drops it by itself.

3. AN EXPANDED ALBUM'S ROWS ARRIVE DRESSED TOO. The panel's download controls
   were the last badge surface asking live, one crossing per row per part of
   its identity literal landing during creation. The rows now carry the
   verdict (the bridge's _dress_panel_rows) and a control resolves once.

4. A QUIET REFILL KEEPS ITS ROWS. A revalidate of the category on screen used
   to clear and rebuild every delegate; it now reconciles by id, the search
   refresh's rule: kept rows keep their delegates (and their badges do not
   re-fade), a moved row moves, a changed field lands, a gone row goes.

Runs in a SUBPROCESS like the other Main.qml scenarios.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

_EXIT_OK = 0
_EXIT_REGRESSED = 1
_EXIT_NO_QT = 77
_EXIT_PRECONDITION = 78

REPO = Path(__file__).resolve().parent.parent
QML_MAIN = REPO / "waves" / "waves_ui" / "qml" / "Main.qml"

_HELD = 4  # albums (and artists) the library holds
_ABSENT = 2  # and ones it does not, so a pass cannot come from "everything is on"


def test_a_dressed_page_builds_without_asking_and_a_cold_one_waits():
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="waves-lib-veil-")
    env["HOME"] = env["XDG_CONFIG_HOME"]  # nothing in the scenario may reach the real home
    proc = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), "--run-scenario"],
        env=env,
        capture_output=True,
        text=True,
        timeout=240,
    )
    tail = "\n".join((proc.stdout + proc.stderr).strip().splitlines()[-25:])
    if proc.returncode == _EXIT_NO_QT:
        pytest.skip("PySide6 / offscreen Qt unavailable")
    if proc.returncode == _EXIT_PRECONDITION:
        pytest.skip(f"could not set up the scenario in this environment:\n{tail}")
    assert proc.returncode == _EXIT_OK, f"Scenario exit={proc.returncode}:\n{tail}"


def test_every_badge_dates_the_verdict_it_was_handed():
    """The source half: the three My Tidal badges (album pill, track pill,
    artist strip) and the download button's claim face each compare the baked
    stamp against the window's before trusting the answer, the browse cards'
    rule (test_browse_card_dressing), and the veil switches the badges' fade
    off while it is up."""
    qml = QML_MAIN.read_text()
    dated = re.findall(r"!live && \w+(?:\.lib|Baked) !== undefined[^\n]*libStamp === root\.libStamp", qml)
    assert (
        len(dated) == 5
    ), f"expected the album pill, the track pill, the artist strip, the button and the panel icon, found {len(dated)}"
    gates = re.findall(r"enabled: \w+\._settled && !root\.searchBuilding && !root\.browseBuilding[^\n]*", qml)
    assert len(gates) == 2 and all("!root.libraryBuilding" in g for g in gates), gates
    # The pane wears the veil, and a category page raises it before its fill.
    assert "opacity: root.libraryReveal" in qml
    assert (
        "root._libBuildStart(cat, items ? items.length : 0, !lm || lm.count === 0)\n            root.libFill(cat, items)"
    ) in qml


def test_every_library_emit_dresses_its_rows():
    src = (REPO / "waves" / "waves_ui" / "backend.py").read_text()
    bare = re.findall(r"self\.library(?:Loaded|More)\.emit\(category, (?!self\._dress_library_rows)", src)
    assert not bare, f"a My Tidal emit still sends undressed rows: {len(bare)}"
    assert src.count("self._dress_library_rows(category,") == 5
    # And the two panels: the album's two emits (fresh, session cache) and the playlist's.
    bare = re.findall(r"self\.(?:album|playlist)TracksLoaded\.emit\(\w+, (?!self\._dress_panel_rows|\[\])", src)
    assert not bare, f"a panel emit still sends undressed rows: {len(bare)}"
    assert src.count("self._dress_panel_rows(") == 3
    # The refill is the in-place one.
    qml = QML_MAIN.read_text()
    assert (
        "function libFill(cat, items) { var m = libModelFor(cat); if (m) reconcileById(m, items, libIsMedia(cat)) }"
        in qml
    )


# Walks the object tree collecting whatever `pick` returns non-null for.
_COLLECT = """
function collect(item, pick, out) {
    if (!item) return out
    var kids = item.children || []
    for (var i = 0; i < kids.length; i++) {
        var k = kids[i].item || kids[i]
        if (!k) continue
        var got = pick(k)
        if (got !== null && got !== undefined) out.push(got)
        collect(k, pick, out)
    }
    return out
}
"""


def _run_scenario() -> int:  # (a linear boot -> drive -> measure scenario)
    sys.path.insert(0, str(REPO))
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    try:
        from PySide6.QtCore import QEventLoop, QTimer, QUrl, Slot
        from PySide6.QtGui import QGuiApplication
        from PySide6.QtQml import QQmlApplicationEngine, QQmlEngine, QQmlExpression
    except Exception as exc:
        print(f"Qt unavailable: {exc}", file=sys.stderr)
        return _EXIT_NO_QT

    from _qml_offline import PARK_LOGIN_QML, patch_offline

    patch_offline()
    app = QGuiApplication.instance() or QGuiApplication([])
    try:
        from waves import matching
        from waves.matching import presence_key, track_key
        from waves.waves_ui.backend import WavesBridge
    except Exception as exc:
        print(f"Qt platform/backend unavailable: {exc}", file=sys.stderr)
        return _EXIT_NO_QT

    asked = {"album": 0, "artist": 0, "track": 0}

    class CountingBridge(WavesBridge):
        # Every overload the real bridge registers, or Qt dispatches straight to
        # the base class and this counter silently measures nothing.
        @Slot(str, str, str, int, result="QVariant")
        @Slot(str, str, str, int, int, result="QVariant")
        @Slot(str, str, str, int, int, int, result="QVariant")
        def libraryAlbumPresence(self, artist, title, year, num_tracks, duration=0, explicit=-1):
            asked["album"] += 1
            return WavesBridge.libraryAlbumPresence(self, artist, title, year, num_tracks, duration, explicit)

        @Slot(str, result="QVariant")
        def artistLibraryPresence(self, name):
            asked["artist"] += 1
            return WavesBridge.artistLibraryPresence(self, name)

        @Slot(str, str, result="QVariant")
        @Slot(str, str, str, str, result="QVariant")
        @Slot(str, str, str, str, int, result="QVariant")
        @Slot(str, str, str, str, int, int, result="QVariant")
        def libraryTrackPresence(self, artist, title, album="", album_year="", duration=0, explicit=-1):
            asked["track"] += 1
            return WavesBridge.libraryTrackPresence(self, artist, title, album, album_year, duration, explicit)

    index: dict = {}
    rollup: dict = {}
    albums: list[dict] = []
    artists: list[dict] = []
    for i in range(_HELD + _ABSENT):
        title, artist = f"Album {i}", f"Artist {i}"
        if i < _HELD:
            index[presence_key(title, artist)] = [
                {
                    "title": title,
                    "year": "2019",
                    "tracks": 11,
                    "id": f"/lib/{i}",
                    "codec": "flac",
                    "bitrate": 0,
                    "bits": 16,
                    "rate": 44100,
                }
            ]
            rollup[matching.norm_artist(matching.canon(artist))] = {
                "present": True,
                "albums": 1,
                "tracks": 11,
                "lossless": True,
            }
        albums.append(
            {
                "id": f"al-{i}",
                "title": title,
                "artist": artist,
                "artist_id": f"a{i}",
                "art": "",
                "year": "2019",
                "date": "2019-01-01",
                "tracks": 11,
                "duration_sec": 2400,
                "quality": "LOSSLESS",
                "popularity": 50,
            }
        )
        artists.append({"id": f"a{i}", "name": artist, "art": "", "roles": [], "popularity": -1})

    # Album 0's panel: two of its three songs are on disk, in its own folder.
    _SONGS, _SONGS_HELD = 3, 2
    track_index = {
        track_key(f"Song {i}", "Artist 0"): [
            {
                "id": "/lib/0",
                "codec": "flac",
                "bitrate": 0,
                "bits": 16,
                "rate": 44100,
                "album": "Album 0",
                "album_year": "2019",
            }
        ]
        for i in range(_SONGS_HELD)
    }
    panel_rows = [
        {
            "id": f"t{i}",
            "num": i + 1,
            "title": f"Song {i}",
            "artist": "Artist 0",
            "album": "Album 0",
            "year": "2019",
            "duration": "3:00",
            "duration_sec": 180,
            "popularity": 10,
            "explicit": False,
        }
        for i in range(_SONGS)
    ]

    def warm() -> None:
        bridge._library_index = index
        bridge._library_track_index = track_index
        bridge._library_artist_index = rollup
        bridge._library_artist_index_src = index

    engine = QQmlApplicationEngine()
    bridge = CountingBridge(tidal=None)
    bridge._library_stamp = 1
    warm()
    engine.rootContext().setContextProperty("waves", bridge)
    engine.rootContext().setContextProperty("monoFont", "JetBrains Mono")
    engine.rootContext().setContextProperty("uiFontFamily", app.font().family())
    engine.load(QUrl.fromLocalFile(str(QML_MAIN)))
    roots = engine.rootObjects()
    if not roots:
        print("Main.qml failed to load", file=sys.stderr)
        return _EXIT_PRECONDITION
    root = roots[0]
    root.setProperty("width", 1400)
    root.setProperty("height", 900)
    root.setProperty("visible", True)

    def q(expr: str):
        e = QQmlExpression(QQmlEngine.contextForObject(root), root, expr)
        r = e.evaluate()
        if e.hasError():
            raise RuntimeError(e.error().toString())
        return r[0] if isinstance(r, tuple) else r

    def settle(ms: int = 200) -> None:
        loop = QEventLoop()
        QTimer.singleShot(ms, loop.quit)
        loop.exec()

    settle(300)
    q("bootOverlay.done = true")
    q("bootContentShown = 1")
    q("browseBuilding = false")
    q("_browseAsyncBuild = false")
    q("libraryOn = true")
    q(PARK_LOGIN_QML)
    settle(200)

    # The badges of the LIVE rows only, reached through the view's own
    # itemAtIndex: a refill pools the previous delegates (reuseItems) and a
    # tree walk would count their badges as the page's.
    def _live(view: str, pick: str) -> str:
        return (
            "JSON.stringify((function() {" + _COLLECT + " var out = [];"
            f" for (var i = 0; i < {view}.count; i++) {{ var d = {view}.itemAtIndex(i); if (d) collect(d, function(k) {{"
            f"   {pick}"
            "   return { shown: !!k.shown, op: Math.round(k.opacity * 1000) / 1000 }"
            " }, out) } return out })())"
        )

    pills = _live("libAlbumsList", "if (k.album === undefined || k.presence === undefined) return null;")
    strips = _live(
        "libArtistsGrid",
        "if (k.artistName === undefined || k.presence === undefined || k.bar === undefined) return null;",
    )

    def read(expr=pills):
        return json.loads(q(expr))

    bad: list[str] = []
    q("root.openLibrary()")

    # ---- 1. a dressed albums page: zero calls, badges simply there ----------
    q("root.libraryCategory = 'albums'")
    dressed = bridge._dress_library_rows("albums", albums)
    if not all(r.get("libStamp") == 1 and "lib" in r for r in dressed):
        print(f"the rows did not come back dressed: {dressed[0]}", file=sys.stderr)
        return _EXIT_PRECONDITION
    asked["album"] = 0
    bridge.libraryLoaded.emit("albums", dressed, False)
    for ms in (16, 32, 80):
        settle(ms)
        mid = [p for p in read() if p["shown"] and p["op"] < 0.999]
        if mid:
            bad.append(f"dressed albums page: {len(mid)} badge(s) faded in at {ms}ms: {mid[:3]}")
            break
    settle(300)
    got = read()
    if len(got) != _HELD + _ABSENT:
        print(f"the albums pane built {len(got)} pills, wanted {_HELD + _ABSENT}", file=sys.stderr)
        return _EXIT_PRECONDITION
    if sum(p["shown"] for p in got) != _HELD:
        bad.append(f"dressed albums page: {sum(p['shown'] for p in got)} pills said IN LIBRARY, wanted {_HELD}: {got}")
    if asked["album"]:
        bad.append(f"dressed albums page: the pills still asked the bridge {asked['album']} times")
    if q("root.libraryBuilding"):
        bad.append("a warm library raised the veil")

    # ---- 2. a dressed artists page: the strips, the same ---------------------
    q("root.libraryCategory = 'artists'")
    dressed_artists = bridge._dress_library_rows("artists", artists)  # (counted: the dressing asks)
    asked["artist"] = 0
    bridge.libraryLoaded.emit("artists", dressed_artists, False)
    settle(400)
    got = read(strips)
    if len(got) != _HELD + _ABSENT:
        print(f"the artists pane built {len(got)} strips, wanted {_HELD + _ABSENT}", file=sys.stderr)
        return _EXIT_PRECONDITION
    if sum(p["shown"] for p in got) != _HELD or any(p["shown"] and p["op"] < 0.999 for p in got):
        bad.append(f"dressed artists page: strips wrong or fading: {got}")
    if asked["artist"]:
        bad.append(f"dressed artists page: the strips still asked the bridge {asked['artist']} times")

    # ---- 3. a page built before the library answered waits behind the veil --
    q("root.libraryCategory = 'albums'")
    # A FIRST build, not a revalidate of rows on screen. reuseItems off for
    # the clear, so the old delegates are destroyed rather than pooled: a
    # pooled row keeps its bindings, and an expanded one would go on
    # answering (and asking) beside its replacement.
    q("libAlbumsList.reuseItems = false; libAlbumsModel.clear(); libAlbumsList.reuseItems = true; gc()")
    settle(30)
    bridge._library_index = None
    bridge._library_root = lambda: "/library"  # configured, so "not yet" is a real state
    if q("waves.libraryIndexReady()"):
        print("could not make the index read as unanswered", file=sys.stderr)
        return _EXIT_PRECONDITION
    bridge.libraryLoaded.emit("albums", bridge._dress_library_rows("albums", albums), False)
    settle(50)
    if not q("root.libraryBuilding") or q("root.libraryReveal") != 0 or q("libArea.opacity") != 0:
        bad.append("a cold page did not raise the veil")
    if any(p["shown"] for p in read()):
        bad.append("badges showed before the library had answered")
    # The first publish lands: badges are there behind the veil, no fade, and
    # the veil drops one pass later and fades the whole pane in.
    warm()
    bridge._bump_library_stamp()
    bridge.libraryPresenceChanged.emit()
    rose = False
    for ms in (16, 32, 80):
        settle(ms)
        now = read()
        if sum(p["shown"] for p in now) != _HELD or any(p["shown"] and p["op"] < 0.999 for p in now):
            bad.append(f"after the publish at {ms}ms the badges were not simply there: {now}")
            break
        if 0 < q("root.libraryReveal") < 1:
            rose = True
    if q("root.libraryBuilding"):
        bad.append("the publish did not drop the veil")
    if not rose:
        bad.append("the pane did not fade in when the veil dropped")
    settle(300)
    if q("root.libraryReveal") != 1 or q("libArea.opacity") != 1:
        bad.append("the pane did not finish revealing")

    # ---- 4. the veil is bounded: a library that never answers drops it -------
    bridge._library_index = None
    q("libAlbumsList.reuseItems = false; libAlbumsModel.clear(); libAlbumsList.reuseItems = true; gc()")
    settle(30)
    bridge.libraryLoaded.emit("albums", bridge._dress_library_rows("albums", albums), False)
    settle(50)
    if not q("root.libraryBuilding"):
        bad.append("the second cold page did not raise the veil")
    settle(1100)
    if q("root.libraryBuilding"):
        bad.append("the veil outlived its guard with no publish coming")
    # ---- 4b. a revalidate of a page already on screen never veils it, and a
    # category with no badges has nothing to wait for ------------------------
    bridge.libraryLoaded.emit("albums", bridge._dress_library_rows("albums", albums), False)
    settle(20)
    if q("root.libraryBuilding") or q("libArea.opacity") != 1:
        bad.append("a cold revalidate blanked the rows the reader was looking at")
    q("root.libraryCategory = 'playlists'; libPlaylistsModel.clear()")
    bridge.libraryLoaded.emit("playlists", [{"id": "pl1", "title": "Mix tape", "artist": "", "art": ""}], False)
    settle(20)
    if q("root.libraryBuilding"):
        bad.append("a playlists page raised the veil with no badge to wait for")
    q("root.libraryCategory = 'albums'")

    # ---- 5. an expanded album's rows arrive dressed: no control asks --------
    warm()
    bridge._bump_library_stamp()
    bridge.libraryPresenceChanged.emit()
    settle(50)
    stamp = bridge._library_stamp
    bridge.libraryLoaded.emit("albums", bridge._dress_library_rows("albums", albums), False)
    settle(300)
    q("root.expandedAlbums = ({ 'al-0': true })")
    settle(100)
    icons = (
        "JSON.stringify((function() {" + _COLLECT + " var d = libAlbumsList.itemAtIndex(0); var out = [];"
        " if (d) collect(d, function(k) { if (k.objectName !== 'downIcon' || !k.libTrack) return null;"
        "   return { present: !!k.libPresent, sure: !!k.libSure } }, out); return out })())"
    )
    dressed_rows = bridge._dress_panel_rows(panel_rows)
    if not all(r.get("libStamp") == stamp and "lib" in r for r in dressed_rows):
        print(f"the panel rows did not come back dressed: {dressed_rows[0]}", file=sys.stderr)
        return _EXIT_PRECONDITION
    asked["track"] = 0
    bridge.albumTracksLoaded.emit("al-0", dressed_rows)
    settle(300)
    got = read(icons)
    if len(got) != _SONGS:
        print(f"the panel built {len(got)} download controls, wanted {_SONGS}", file=sys.stderr)
        return _EXIT_PRECONDITION
    if sum(p["present"] and p["sure"] for p in got) != _SONGS_HELD or sum(p["present"] for p in got) != _SONGS_HELD:
        bad.append(f"dressed panel: the controls read the wrong verdicts: {got}")
    if asked["track"]:
        bad.append(f"dressed panel: the controls still asked the bridge {asked['track']} times")
    # Undressed rows (a stub, an older cache) ask ONCE per control, never once
    # per part of the identity literal landing, and read the same answers.
    asked["track"] = 0
    bridge.albumTracksLoaded.emit("al-0", panel_rows)
    settle(300)
    if read(icons) != got:
        bad.append(f"undressed panel rows answered differently: {read(icons)} vs {got}")
    if asked["track"] != _SONGS:
        bad.append(f"undressed panel: {asked['track']} calls for {_SONGS} controls, wanted one each")
    q("root.expandedAlbums = ({})")
    settle(300)

    # ---- 6. a quiet refill keeps the rows it can: in place, by id -----------
    q(
        "(function(){ for (var i = 0; i < libAlbumsList.count; i++) { var d = libAlbumsList.itemAtIndex(i);"
        " if (d) d.objectName = 'keep-' + libAlbumsModel.get(i).id } })()"
    )
    newcomer = dict(albums[1], id="al-9", title="Album 9", artist="Artist 9", artist_id="a9")
    refill = [albums[5], albums[0], dict(albums[2], popularity=77), albums[3], newcomer, albums[4]]
    dressed_refill = bridge._dress_library_rows("albums", refill)  # (counted: the dressing asks)
    q("root.libPinRefill = true")
    asked["album"] = 0
    bridge.libraryLoaded.emit("albums", dressed_refill, False)
    settle(16)
    mid = [p for p in read() if p["shown"] and p["op"] < 0.999]
    if mid:
        bad.append(f"refill: {len(mid)} kept badge(s) faded in again: {mid[:3]}")
    settle(300)
    order = json.loads(
        q(
            "JSON.stringify((function(){ var o = []; for (var i = 0; i < libAlbumsModel.count; i++) o.push(libAlbumsModel.get(i).id); return o })())"
        )
    )
    if order != [r["id"] for r in refill]:
        bad.append(f"refill: rows landed as {order}, wanted {[r['id'] for r in refill]}")
    names = json.loads(
        q(
            "JSON.stringify((function(){ var o = []; for (var i = 0; i < libAlbumsList.count; i++) {"
            " var d = libAlbumsList.itemAtIndex(i); o.push(d ? d.objectName : null) } return o })())"
        )
    )
    for j, r in enumerate(refill):
        if r["id"] != "al-9" and names[j] != "keep-" + r["id"]:
            bad.append(f"refill: row {r['id']} was rebuilt rather than kept (delegate {names[j]})")
    if q("libAlbumsModel.get(2).popularity") != 77:
        bad.append("refill: a changed field did not land on the kept row")
    held_now = sum(1 for r in refill if int(r["id"].split("-")[1]) < _HELD)
    shown = sum(p["shown"] for p in read())
    if shown != held_now:
        bad.append(f"refill: {shown} pills said IN LIBRARY, wanted {held_now}")
    if asked["album"]:
        bad.append(f"refill: the kept rows asked the bridge {asked['album']} times")

    for line in bad:
        print(f"REGRESSED: {line}", file=sys.stderr)
    return _EXIT_REGRESSED if bad else _EXIT_OK


if __name__ == "__main__":
    sys.exit(_run_scenario())
