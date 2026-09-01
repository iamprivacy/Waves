"""Building a page of search rows asks the bridge about presence ONCE per badge.

THE COST THIS FENCES OFF
------------------------
libraryAlbumPresence is a QML -> Python crossing, measured at ~22us a call
(the matcher itself is only ~4us of that; the rest is the boundary). The pill
and the Download button each used to carry the album's identity as FOUR
properties, so filling one in fired four change handlers and a fifth from
Component.onCompleted, and each of those asked the same question again: 15
calls per album row, 750 for a 50-row page, about 16ms of GUI thread spent
re-answering during a page build.

The identity is now ONE object property, so it is one binding evaluation and
one call. This pins the budget rather than the exact number, because the
number legitimately moves when rows gain or lose a badge; what must not come
back is the per-property fan-out.

The TRACK side pays the same crossing (libraryTrackPresence) with the same
one-object-identity shape, so its budget is pinned here too, additively and
separately: album fan-out and track fan-out regress independently. A track row
has TWO consumers of that answer, the pill beside the title and the download
button's claim face, and both are counted, so the budget stays "one call per
thing that asks" rather than one per row.

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
# A spy that measured ZERO calls after the consumer-count preconditions have
# already passed. That can only be overload drift (a new argument on the slot
# the spy's overloads no longer intercept), never an environment problem, so
# unlike _EXIT_PRECONDITION it must FAIL: mapped to skip, the dead spy kept CI
# green while measuring nothing, one notch quieter than the silent pass the
# loud-on-zero rule exists to prevent.
_EXIT_DEAD_SPY = 79

_ROWS = 30
#: One call per presence consumer (a pill, or a Download button holding an album
#: identity) is what the current shape costs, measured. The slack is additive,
#: not per-consumer: a few page-level resolves that belong to no counted badge
#: are fine, but anything that scales with the row count is the fan-out coming
#: back, and no multiplier would catch that.
_SLACK = 8

QML_MAIN = Path(__file__).resolve().parent.parent / "waves" / "waves_ui" / "qml" / "Main.qml"


def test_presence_is_asked_once_per_badge_not_once_per_property():
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="waves-presence-budget-")
    proc = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), "--run-scenario"],
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    tail = "\n".join((proc.stdout + proc.stderr).strip().splitlines()[-6:])
    if proc.returncode == _EXIT_NO_QT:
        pytest.skip("PySide6 / offscreen Qt unavailable")
    if proc.returncode == _EXIT_PRECONDITION:
        pytest.skip(f"could not set up the scenario in this environment:\n{tail}")
    if proc.returncode == _EXIT_DEAD_SPY:
        pytest.fail(f"the presence spy measured zero calls (overload drift?):\n{tail}")
    assert proc.returncode == _EXIT_OK, (
        "presence is being resolved more than once per badge again. The usual cause is an "
        "album's identity being split back into separate properties, each with its own change "
        f"handler, so one row asks the same question several times:\n{tail}"
    )


def _run_scenario() -> int:  # (a linear boot -> drive -> measure scenario)
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
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
        from waves.matching import presence_key, track_key
        from waves.waves_ui.backend import WavesBridge
    except Exception as exc:
        print(f"Qt platform/backend unavailable: {exc}", file=sys.stderr)
        return _EXIT_NO_QT

    calls = {"n": 0}

    tcalls = {"n": 0}

    acalls = {"n": 0}

    class CountingBridge(WavesBridge):
        # EVERY overload the real bridge registers, or this counter measures
        # nothing: Qt dispatches a call straight to the base class's
        # registration for any arity the spy does not declare, so a spy one
        # overload short silently sees zero calls while every real caller
        # uses the long form (the duration witness made five arguments the
        # form every QML caller uses). The zero-calls precondition below
        # turns that silent death into a loud one.
        @Slot(str, str, str, int, result="QVariant")
        @Slot(str, str, str, int, int, result="QVariant")
        def libraryAlbumPresence(self, artist, title, year, num_tracks, duration=0):
            calls["n"] += 1
            return WavesBridge.libraryAlbumPresence(self, artist, title, year, num_tracks, duration)

        @Slot(str, str, result="QVariant")
        @Slot(str, str, str, str, result="QVariant")
        @Slot(str, str, str, str, int, result="QVariant")
        def libraryTrackPresence(self, artist, title, album="", album_year="", duration=0):
            tcalls["n"] += 1
            return WavesBridge.libraryTrackPresence(self, artist, title, album, album_year, duration)

        @Slot(str, result="QVariant")
        def artistLibraryPresence(self, name):
            acalls["n"] += 1
            return WavesBridge.artistLibraryPresence(self, name)

    index: dict = {}
    albums: list[str] = []
    for i in range(_ROWS):
        title, artist = f"Album {i}", f"Artist {i}"
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
        albums.append(
            json.dumps(
                {
                    "id": f"al-{i}",
                    "title": title,
                    "artist": artist,
                    "artist_id": f"a{i}",
                    "art": "",
                    "year": "2019",
                    "date": "2019-01-01",
                    "tracks": 11,
                    "quality": "LOSSLESS",
                    "popularity": 50,
                }
            )
        )

    track_index: dict = {}
    tracks: list[str] = []
    for i in range(_ROWS):
        title, artist = f"Track {i}", f"Artist {i}"
        track_index[track_key(title, artist)] = [
            {"id": f"/lib/{i}", "codec": "flac", "bitrate": 0, "bits": 16, "rate": 44100}
        ]
        tracks.append(
            json.dumps(
                {
                    "id": f"tr-{i}",
                    "title": title,
                    "artist": artist,
                    "artist_id": f"a{i}",
                    "album": f"Album {i}",
                    "album_id": f"al-{i}",
                    "art": "",
                    "year": "2019",
                    "date": "2019-01-01",
                    "duration": "3:30",
                    "quality": "LOSSLESS",
                    "popularity": 50,
                }
            )
        )

    # The artist axis. Its answer is a ROLLUP over the album index above, so the
    # names are the ones those albums are credited to and every card resolves to
    # a real "in library" strip rather than a hidden one.
    artists: list[str] = [
        json.dumps({"id": f"a{i}", "name": f"Artist {i}", "art": "", "popularity": 50}) for i in range(_ROWS)
    ]

    engine = QQmlApplicationEngine()
    bridge = CountingBridge(tidal=None)
    bridge._library_index = index
    bridge._library_track_index = track_index
    engine.rootContext().setContextProperty("waves", bridge)
    engine.rootContext().setContextProperty("monoFont", "JetBrains Mono")
    engine.rootContext().setContextProperty("uiFontFamily", app.font().family())
    engine.load(QUrl.fromLocalFile(str(QML_MAIN)))
    roots = engine.rootObjects()
    if not roots:
        print("Main.qml failed to load", file=sys.stderr)
        return _EXIT_PRECONDITION
    root = roots[0]

    def q(expr: str):
        e = QQmlExpression(QQmlEngine.contextForObject(root), root, expr)
        r = e.evaluate()
        if e.hasError():
            raise RuntimeError(e.error().toString())
        return r[0] if isinstance(r, tuple) else r

    def settle(ms: int) -> None:
        loop = QEventLoop()
        QTimer.singleShot(ms, loop.quit)
        loop.exec()

    root.setProperty("width", 1280)
    root.setProperty("height", 900)
    root.setProperty("visible", True)
    settle(300)
    q("bootOverlay.done = true")
    q("bootContentShown = 1")
    q(PARK_LOGIN_QML)
    q("root.openSearch()")
    q("albumsModel.clear()")
    q("tracksModel.clear()")
    q("artistsModel.clear()")
    # Chosen BEFORE the rows land, unlike the album and track sections. The
    # artists section has two forms (a strip when collapsed, a grid when
    # expanded) and switching between them tears one set of cards down and
    # builds the other, so flipping it afterwards would count two page builds
    # and read as a fan-out that is not there.
    q("root.searchArtistsExpanded = true")
    settle(200)

    calls["n"] = 0  # count the page build only, not the boot
    tcalls["n"] = 0
    acalls["n"] = 0
    for a in albums:
        q(f"albumsModel.append({a})")
    for t in tracks:
        q(f"tracksModel.append({t})")
    for ar in artists:
        q(f"artistsModel.append({ar})")
    q("root.searchReveal = 1")
    q("root.searchBuilding = false")
    q("root.searchAlbumsExpanded = true")
    q("root.searchTracksExpanded = true")
    settle(1500)

    # Every consumer that actually resolved presence: a Download button holding
    # an album identity (libTitle) or a presence pill holding one (albumTitle).
    consumers = int(
        q(
            "(function(){ var n = 0;"
            " function walk(it){ if (!it) return;"
            "  if ((it.libTitle !== undefined && it.libTitle !== '')"
            "   || (it.albumTitle !== undefined && it.albumTitle !== '' && it.qclass !== undefined)) n++;"
            "  for (var i = 0; i < it.children.length; i++) walk(it.children[i].item || it.children[i]); }"
            " walk(contentCol); return n; })()"
        )
        or 0
    )
    if consumers < _ROWS:
        print(f"only {consumers} presence consumers built for {_ROWS} rows", file=sys.stderr)
        return _EXIT_PRECONDITION

    # Track consumers: the TrackPresencePill beside the title (identified by
    # its `track` identity plus a pill's qclass) and the row's DownloadButton
    # claim face (its `libTrack` identity plus a button's st). Two per row.
    track_consumers = int(
        q(
            "(function(){ var n = 0;"
            " function walk(it){ if (!it) return;"
            "  if (it.track !== undefined && it.track && it.track.title !== undefined"
            "   && it.qclass !== undefined) n++;"
            "  if (it.libTrack !== undefined && it.libTrack && it.libTrack.title !== undefined"
            "   && it.st !== undefined) n++;"
            "  for (var i = 0; i < it.children.length; i++) walk(it.children[i].item || it.children[i]); }"
            " walk(contentCol); return n; })()"
        )
        or 0
    )
    if track_consumers < _ROWS * 2:
        print(f"only {track_consumers} track consumers built for {_ROWS} rows", file=sys.stderr)
        return _EXIT_PRECONDITION

    # Artist consumers: the library strip on an artist card, identified by the
    # name it holds. One per card, and it rides EVERY artist card on a search
    # page, so a per-property fan-out here would cost the same as the album
    # one it was modelled on.
    artist_consumers = int(
        q(
            "(function(){ var n = 0;"
            " function walk(it){ if (!it) return;"
            "  if (it.artistName !== undefined && it.artistName !== '' && it.presence !== undefined) n++;"
            "  for (var i = 0; i < it.children.length; i++) walk(it.children[i].item || it.children[i]); }"
            " walk(contentCol); return n; })()"
        )
        or 0
    )
    if artist_consumers < _ROWS:
        print(f"only {artist_consumers} artist consumers built for {_ROWS} rows", file=sys.stderr)
        return _EXIT_PRECONDITION

    # A dead spy (an overload the real callers use but the spy does not
    # declare) counts zero and would pass every budget forever; zero calls
    # from this many consumers is a broken measurement, never a real result,
    # and gets its own FAILING exit code rather than the precondition skip.
    if calls["n"] == 0 or tcalls["n"] == 0 or acalls["n"] == 0:
        print(
            f"spy measured zero calls (album={calls['n']}, track={tcalls['n']}, "
            f"artist={acalls['n']}): overload drift?",
            file=sys.stderr,
        )
        return _EXIT_DEAD_SPY

    budget = consumers + _SLACK
    tbudget = track_consumers + _SLACK
    abudget = artist_consumers + _SLACK
    per = calls["n"] / consumers
    tper = tcalls["n"] / track_consumers
    aper = acalls["n"] / artist_consumers
    print(
        f"consumers={consumers} calls={calls['n']} per_consumer={per:.2f} budget={budget} "
        f"track_consumers={track_consumers} track_calls={tcalls['n']} per_track={tper:.2f} track_budget={tbudget} "
        f"artist_consumers={artist_consumers} artist_calls={acalls['n']} per_artist={aper:.2f} "
        f"artist_budget={abudget}",
        flush=True,
    )
    if calls["n"] > budget or tcalls["n"] > tbudget or acalls["n"] > abudget:
        return _EXIT_REGRESSED
    return _EXIT_OK


if __name__ == "__main__":
    raise SystemExit(_run_scenario())
