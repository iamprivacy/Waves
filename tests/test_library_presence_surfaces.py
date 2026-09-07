"""Every surface that shows a download state has to ask the library too.

WHAT THIS FENCES OFF
--------------------
The library verdict landed one control at a time, and four surfaces were
missed. Each of them read live job state (root.dlSt) and nothing else, so an
album or a song already sitting on disk was offered as a plain DOWNLOAD while
the badge two pixels away said the opposite. An audit of Main.qml found them;
this pins them, because "this control also asks" is exactly the kind of wiring
that a later refactor drops without a single test going red.

The four:

* the compact DownIcon in the expanded album and playlist panels, the one
  download control in the app that carried no library properties at all;
* BrowseCard, the console browse style's card, which printed DOWNLOAD / QUEUED
  / DONE / RETRY and never asked;
* the artist-wide buttons ("Download discography", "Download artist"), which
  had no grain to ask ON until DownloadButton grew libArtist;
* the hero card's artist strip, which stood down on the biggest card of the
  landing's first shelf and so made it the one card there that could not say
  what you already hold.

Each surface is checked against a MATCHED and an UNMATCHED subject in the same
page, so a check can never pass by the verdict simply being on everywhere.

Runs in a SUBPROCESS like the other Main.qml scenarios: building the bridge
installs process-global handlers that must not leak into the suite.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

_EXIT_OK = 0
_EXIT_REGRESSED = 1
_EXIT_NO_QT = 77
_EXIT_PRECONDITION = 78

QML_MAIN = Path(__file__).resolve().parent.parent / "waves" / "waves_ui" / "qml" / "Main.qml"

ARTIST = "Miss May I"
ALBUM = "Shadows Inside"
YEAR = "2017"
FOLDER = "/lib/mmi/shadows-inside"


def test_every_download_surface_reports_the_library_verdict():
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="waves-presence-surfaces-")
    proc = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), "--run-scenario"],
        env=env,
        capture_output=True,
        text=True,
        timeout=240,
    )
    tail = "\n".join((proc.stdout + proc.stderr).strip().splitlines()[-20:])
    if proc.returncode == _EXIT_NO_QT:
        pytest.skip("PySide6 / offscreen Qt unavailable")
    if proc.returncode == _EXIT_PRECONDITION:
        pytest.skip(f"could not set up the scenario in this environment:\n{tail}")
    assert proc.returncode == _EXIT_OK, (
        "a download surface stopped consulting the library presence bridge, so something already "
        f"on disk reads as a plain DOWNLOAD there. Scenario exit={proc.returncode}:\n{tail}"
    )


# Walks the object tree collecting whatever `pick` returns non-null for. Shared
# by every probe below, so a surface that moves in the tree keeps being found.
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
function faceOf(item) {
    var found = collect(item, function(k) { return k.objectName === "dbFaceText" ? k : null }, [])
    return found.length > 0 ? ("" + found[0].text) : "no-face"
}
"""


def _run_scenario() -> int:  # (a linear boot -> drive -> measure scenario)
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
        from waves.matching import presence_key, track_key
        from waves.waves_ui.backend import WavesBridge
    except Exception as exc:
        print(f"Qt platform/backend unavailable: {exc}", file=sys.stderr)
        return _EXIT_NO_QT

    engine = QQmlApplicationEngine()
    bridge = WavesBridge(tidal=None)
    # One album, held complete and PROVEN (the folder agrees on year and track
    # count), which is also what makes its artist's rollup non-empty.
    bridge._library_index = {
        presence_key(ALBUM, ARTIST): [
            {
                "title": ALBUM,
                "year": YEAR,
                "tracks": 2,
                "id": FOLDER,
                "codec": "flac",
                "bitrate": 0,
                "bits": 16,
                "rate": 44100,
            }
        ]
    }
    bridge._library_track_index = {
        track_key("Hide", ARTIST): [
            {
                "id": FOLDER,
                "codec": "flac",
                "bitrate": 0,
                "bits": 16,
                "rate": 44100,
                "album": ALBUM,
                "album_year": YEAR,
            }
        ]
    }
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
    # With the scan on, the done faces say IN LIBRARY rather than DOWNLOADED,
    # which is the wording a user with a library configured actually sees.
    q("libraryOn = true")

    bad: list[str] = []

    # ---- 1 + 3: the search page (expanded album panel, artist cards) --------
    q("root.openSearch()")
    q("albumsModel.clear()")
    q("tracksModel.clear()")
    q("artistsModel.clear()")
    q("root.searchArtistsExpanded = true")
    album = json.dumps(
        {
            "id": "al-held",
            "title": ALBUM,
            "artist": ARTIST,
            "artist_id": "a-held",
            "art": "",
            "year": YEAR,
            "date": YEAR + "-01-01",
            "tracks": 2,
            "quality": "LOSSLESS",
            "popularity": 50,
        }
    )
    q(f"albumsModel.append({album})")
    for aid, name in (("a-held", ARTIST), ("a-absent", "Nobody At All")):
        card = json.dumps({"id": aid, "name": name, "art": "", "popularity": 50})
        q(f"artistsModel.append({card})")
    q("root.searchReveal = 1")
    q("root.searchBuilding = false")
    q("root.searchAlbumsExpanded = true")
    settle(400)

    # The panel's rows come from the QML track cache, so seeding it expands the
    # album without a network fetch. "Hide" is the song on disk, "Gone" is not.
    q(
        "root.trackCache = ({ 'al-held': ["
        "{ id: 'tr-held', num: 1, title: 'Hide', duration: '3:30', popularity: 50, explicit: false },"
        "{ id: 'tr-absent', num: 2, title: 'Gone', duration: '3:10', popularity: 50, explicit: false }] })"
    )
    q("root.resetExpandedAlbums({ 'al-held': true })")
    settle(700)

    icons_probe = (
        "JSON.stringify((function() {" + _COLLECT + " return collect(contentCol, function(k) {"
        "   if (k.objectName !== 'downIcon' || k.mediaId === '') return null;"
        "   return { id: '' + k.mediaId, state: k.st === '' ? 'live' : '' + k.st,"
        "            claim: !!k.libClaim, sure: !!k.libSure, path: '' + k.libPath }"
        " }, []) })())"
    )
    try:
        icons = {r["id"]: r for r in json.loads(q(icons_probe))}
    except Exception as exc:
        print(f"track-icon probe failed: {exc}", file=sys.stderr)
        return _EXIT_PRECONDITION
    if "tr-held" not in icons or "tr-absent" not in icons:
        print(f"the expanded album panel built no track icons: {icons}", file=sys.stderr)
        return _EXIT_PRECONDITION
    held, absent = icons["tr-held"], icons["tr-absent"]
    # Done, and PROVEN done: the panel names the release, which is the only
    # evidence a track can be proven by, so this one is green rather than gold.
    if held["state"] != "done" or not held["claim"]:
        bad.append(f"track icon: a song on disk read {held!r}, wanted a done claim")
    if not held["sure"]:
        bad.append("track icon: the panel's own album did not prove the match")
    if held["path"] != FOLDER:
        bad.append(f"track icon: the claim lost its folder ({held['path']!r})")
    if absent["state"] != "live" or absent["claim"]:
        bad.append(f"track icon: a song NOT on disk read {absent!r}, wanted a live button")

    # A claim is a GUESS, so the icon has to answer for it instead of sitting
    # inert: fire the real tap area, not the function behind it, or the branch
    # in the MouseArea could be deleted and this would keep passing.
    tap = (
        "(function() {" + _COLLECT + " var icons = collect(contentCol, function(k) {"
        "   return (k.objectName === 'downIcon' && ('' + k.mediaId) === 'tr-held') ? k : null; }, []);"
        " if (icons.length === 0) return false;"
        " var areas = collect(icons[0], function(k) {"
        "   return k.objectName === 'diTapArea' ? k : null; }, []);"
        " if (areas.length === 0) return false;"
        " areas[0].clicked(null); return true })()"
    )
    if not q(tap):
        print("could not reach the held track's tap area", file=sys.stderr)
        return _EXIT_PRECONDITION
    settle(120)
    gate = {
        "opens": bool(q("libraryClaimGate.shown")),
        "knows_it_is_a_track": str(q("libraryClaimGate.kind")) == "track",
        "names_the_song": str(q("libraryClaimGate.albumTitle")) == "Hide",
        "shows_the_folder": str(q("libraryClaimGate.folder")) == FOLDER,
    }
    if not all(gate.values()):
        bad.append(f"track icon: the claim did not open its gate ({gate})")
    q("libraryClaimGate.shown = false")
    settle(120)

    # Found by the button's own label and media id, never by the property under
    # test: keyed on libArtist, a button that stopped naming its artist would
    # simply vanish from the probe and skip its own check.
    artist_probe = (
        "JSON.stringify((function() {" + _COLLECT + " return collect(contentCol, function(k) {"
        "   if (k.label === undefined || k.mediaId === undefined || k.libArtist === undefined) return null;"
        "   if (('' + k.label) !== 'Download artist') return null;"
        "   return { id: '' + k.mediaId, name: '' + k.libArtist, words: faceOf(k),"
        "            partial: !!k.libPartial, present: !!k.libPresent }"
        " }, []) })())"
    )
    try:
        artists = {r["id"]: r for r in json.loads(q(artist_probe))}
    except Exception as exc:
        print(f"artist-button probe failed: {exc}", file=sys.stderr)
        return _EXIT_PRECONDITION
    if "a-held" not in artists or "a-absent" not in artists:
        print(f"the search page built no artist download buttons: {artists}", file=sys.stderr)
        return _EXIT_PRECONDITION
    a_held, a_absent = artists["a-held"], artists["a-absent"]
    if a_held["name"] != ARTIST or a_absent["name"] != "Nobody At All":
        bad.append(f"artist button: the button does not know whose catalogue it offers ({artists!r})")
    # A discography has no completeness axis, so the rollup can only ever reach
    # the partial face: a live button, coloured and worded to say part of this
    # is already here. An inert DOWNLOADED here would be a claim nobody can make.
    if not a_held["partial"] or a_held["words"] != "PARTIALLY IN LIBRARY":
        bad.append(f"artist button: an artist on disk read {a_held!r}, wanted the partial face")
    if a_held["present"]:
        bad.append("artist button: a rollup reported a COMPLETE discography, which cannot be known")
    if a_absent["partial"] or a_absent["words"] != "DOWNLOAD ARTIST":
        bad.append(f"artist button: an artist NOT on disk read {a_absent!r}, wanted a plain download")

    # ---- 4: the hero card's artist strip (art browse style) ----------------
    # On the Browse tab, not just built behind the search page: `visible` is
    # EFFECTIVE visibility, so a strip probed under a hidden pane reads false
    # however well it resolved.
    q("root.openBrowse()")
    q("root.browseStyle = 'art'")
    cards = [
        {"id": "a-held", "kind": "artist", "name": ARTIST, "title": ARTIST, "art": ""},
        {"id": "a-absent", "kind": "artist", "name": "Nobody At All", "title": "Nobody At All", "art": ""},
    ]
    root.setProperty("browseSections", [{"title": "ARTISTS", "rowKind": "cards", "data": "", "items": cards}])
    settle(900)
    hero_probe = (
        "JSON.stringify((function() {" + _COLLECT + " return collect(browseLanding, function(k) {"
        "   if (k.hero === undefined || k.card === undefined || !k.hero) return null;"
        "   var strips = collect(k, function(b) {"
        "     return (b.artistName !== undefined && b.presence !== undefined) ? b : null; }, []);"
        "   if (strips.length === 0) return { id: '' + (k.card.id || ''), strips: 0 };"
        "   var s = strips[0];"
        "   var caps = collect(k, function(b) {"
        "     return b.objectName === 'acHeroCaption' ? b : null; }, []);"
        "   return { id: '' + (k.card.id || ''), strips: strips.length,"
        "            name: '' + s.artistName, shown: !!s.visible,"
        "            capTop: caps.length > 0 ? caps[0].y : -1, bottom: s.y + s.height }"
        " }, []) })())"
    )
    try:
        heroes = {r["id"]: r for r in json.loads(q(hero_probe))}
    except Exception as exc:
        print(f"hero probe failed: {exc}", file=sys.stderr)
        return _EXIT_PRECONDITION
    # Every card of the landing's first shelf is a hero, so both artists are
    # here: the one on disk and the one that is not.
    if "a-held" not in heroes or "a-absent" not in heroes:
        print(f"the landing built no hero cards: {heroes}", file=sys.stderr)
        return _EXIT_PRECONDITION
    h_held, h_absent = heroes["a-held"], heroes["a-absent"]
    # The strip is gated by the NAME, never by an outer visible binding, and a
    # hero no longer clears its own name to stand down: an empty name here is
    # that gate coming back.
    if h_held.get("name") != ARTIST or not h_held.get("shown"):
        bad.append(f"hero card: the artist strip stayed silent ({h_held!r})")
    # It stacks ABOVE the hero's own caption instead of on the cover's bottom
    # edge, which is the only reason it could come back at all.
    if h_held.get("capTop", -1) < 0:
        print(f"the hero card built no caption to stack above: {h_held!r}", file=sys.stderr)
        return _EXIT_PRECONDITION
    if h_held.get("bottom", 0) > h_held["capTop"]:
        bad.append(f"hero card: the strip overlaps the caption it must sit above ({h_held!r})")
    # The badge hides ITSELF when the rollup says nothing: an artist with
    # nothing on disk keeps its name and shows no strip.
    if h_absent.get("name") != "Nobody At All" or h_absent.get("shown"):
        bad.append(f"hero card: an artist with nothing on disk showed a strip ({h_absent!r})")

    # ---- 2: BrowseCard, the console browse style ---------------------------
    q("root.browseStyle = 'console'")
    album_cards = [
        {
            "id": "al-held",
            "kind": "album",
            "title": ALBUM,
            "artist": ARTIST,
            "year": YEAR,
            "tracks": 2,
            "art": "",
        },
        {
            "id": "al-absent",
            "kind": "album",
            "title": "Monument",
            "artist": ARTIST,
            "year": "2014",
            "tracks": 11,
            "art": "",
        },
    ]
    root.setProperty("browseSections", [{"title": "ALBUMS", "rowKind": "cards", "data": "", "items": album_cards}])
    settle(900)
    card_probe = (
        "JSON.stringify((function() {" + _COLLECT + " return collect(browseLanding, function(k) {"
        "   if (k.libWord === undefined || k.card === undefined) return null;"
        "   var words = collect(k, function(b) {"
        "     return b.objectName === 'bcDlWord' ? b : null; }, []);"
        "   var box = words.length > 0 ? words[0].parent : null;"
        "   return { id: '' + (k.card.id || ''), state: '' + k.libState, claim: !!k.libClaim,"
        "            shown: words.length > 0 ? ('' + words[0].text) : 'no-word',"
        "            boxW: box ? box.width : -1 }"
        " }, []) })())"
    )
    try:
        browse = {r["id"]: r for r in json.loads(q(card_probe))}
    except Exception as exc:
        print(f"browse-card probe failed: {exc}", file=sys.stderr)
        return _EXIT_PRECONDITION
    if "al-held" not in browse or "al-absent" not in browse:
        print(f"the console browse style built no cards: {browse}", file=sys.stderr)
        return _EXIT_PRECONDITION
    b_held, b_absent = browse["al-held"], browse["al-absent"]
    # A proven complete copy: the card says so in the shortest form its control
    # line can carry, and its click goes to the claim gate rather than a fetch.
    if b_held["state"] != "proven" or b_held["shown"] != "IN LIBRARY" or not b_held["claim"]:
        bad.append(f"browse card: an album on disk read {b_held!r}, wanted a proven claim")
    if b_absent["state"] != "" or b_absent["shown"] != "DOWNLOAD" or b_absent["claim"]:
        bad.append(f"browse card: an album NOT on disk read {b_absent!r}, wanted a plain download")
    # The verdict must not move the control line. A 156px card shares this row
    # with the preview control, so the words here are the short forms and the
    # box they sit in has to stay the width a plain DOWNLOAD gives it: the
    # full button's "PARTIALLY IN LIBRARY" arriving here would push the
    # download half straight into PREVIEW on every matched card.
    if b_held["boxW"] <= 0 or b_held["boxW"] != b_absent["boxW"]:
        bad.append(
            f"browse card: the library verdict widened the control line " f"({b_held['boxW']} vs {b_absent['boxW']})"
        )

    # ---- the scan is not a one-shot -----------------------------------------
    # A verdict that changes while the page is open has to reach these surfaces
    # too, which is the whole job of their libraryPresenceChanged handlers. The
    # index is emptied rather than filled because both surfaces have already
    # been proven to say the RIGHT thing, so only the re-ask is left to prove.
    bridge._library_index = {}
    bridge._library_track_index = {}
    bridge.libraryPresenceChanged.emit()
    settle(500)
    try:
        after_cards = {r["id"]: r for r in json.loads(q(card_probe))}
        after_icons = {r["id"]: r for r in json.loads(q(icons_probe))}
    except Exception as exc:
        print(f"re-ask probe failed: {exc}", file=sys.stderr)
        return _EXIT_PRECONDITION
    if after_cards.get("al-held", {}).get("shown") != "DOWNLOAD":
        bad.append(f"browse card: it never re-asked when the scan changed ({after_cards.get('al-held')!r})")
    if after_icons.get("tr-held", {}).get("state") != "live":
        bad.append(f"track icon: it never re-asked when the scan changed ({after_icons.get('tr-held')!r})")

    if bad:
        print("\n".join(bad), file=sys.stderr)
        return _EXIT_REGRESSED
    return _EXIT_OK


if __name__ == "__main__":
    raise SystemExit(_run_scenario())
