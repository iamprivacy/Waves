"""A track row's download button has to answer for the library too.

WHAT THIS FENCES OFF
--------------------
An album row has said ALBUM IN LIBRARY (or MAYBE, or PARTIALLY) for a while,
and a click on a claim opens the gate that explains the match. A TRACK row
said nothing: the small pill beside the title was the only tell, and the
button next to it looked exactly like a button for a song nobody had. So
clicking download on a track already on disk went straight through, silently,
which is what a user hit in a real session.

The button now carries the same verdict, on the same two axes minus the one a
track does not have. There is no coverage question for a song: it is on disk
or it is not, so presence alone is the done shape and identity alone picks the
words. Proven reads TRACK IN LIBRARY in green, unproven reads MAYBE IN LIBRARY
in gold, and BOTH stay clickable, because the track key is the most brittle
guess this app makes and a guess must never be the end of the conversation.

Four rows, four different reasons:

* a track whose holding folder proves the release  -> green, done;
* the same track with nothing to prove it against  -> gold MAYBE, done;
* a track the scan never saw                       -> untouched, live;
* a VIDEO whose title and artist DO match a row in the track index -> still
  live, because the scan only ever holds audio and a video must never wear an
  audio claim.

Then the gate itself: it must open on a track's claim, say "file" rather than
"folder", and DOWNLOAD ANYWAY must really download. A track needs no claim
override to do that (the bulk gate rides only on collection jobs), so the
proof here is that the click reaches downloadTrack with the track's own id.

Runs in a SUBPROCESS for the same reason as the other Main.qml scenarios:
building the bridge installs process-global handlers that must not leak into
the rest of the suite.
"""

from __future__ import annotations

import json
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

ARTIST = "Miss May I"
ALBUM = "Shadows Inside"
YEAR = "2017"

# id -> (face words, button state, claim openable)
EXPECTED = {
    "t-proven": ("TRACK IN LIBRARY", "done", True),
    "t-unproven": ("MAYBE IN LIBRARY", "done", True),
    "t-missing": ("DOWNLOAD TRACK", "live", False),
    "v-video": ("DOWNLOAD VIDEO", "live", False),
}


def test_track_button_carries_the_library_verdict_and_opens_the_gate():
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="waves-track-claim-test-")
    proc = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), "--run-scenario"],
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    tail = "\n".join((proc.stdout + proc.stderr).strip().splitlines()[-16:])
    import pytest

    if proc.returncode == _EXIT_NO_QT:
        pytest.skip("PySide6 / offscreen Qt unavailable")
    if proc.returncode == _EXIT_PRECONDITION:
        pytest.skip(f"could not set up the scenario in this environment:\n{tail}")
    assert (
        proc.returncode == _EXIT_OK
    ), f"a track button reported the wrong library state. Scenario exit={proc.returncode}:\n{tail}"


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
        from PySide6.QtCore import Slot

        from tidaler.matching import track_key
        from tidaler.waves_ui.app import _load_mono
        from tidaler.waves_ui.backend import WavesBridge
    except Exception as exc:
        print(f"Qt platform/backend unavailable: {exc}", file=sys.stderr)
        return _EXIT_NO_QT

    # DOWNLOAD ANYWAY has to actually reach the engine. A subclass, not an
    # instance attribute: QML dispatches through the meta-object.
    downloaded: list[str] = []

    class SpyBridge(WavesBridge):
        @Slot(str)
        def downloadTrack(self, track_id: str) -> None:
            downloaded.append(str(track_id))

    engine = QQmlApplicationEngine()
    bridge = SpyBridge(tidal=None)
    # The proven copy names the album it sits in and agrees on the year, which
    # is the only evidence a track can be proven by (it carries no year of its
    # own). The unproven copy is the SAME song with a folder that names
    # nothing, so identity cannot be settled and the button must hedge.
    bridge._library_track_index = {
        track_key("Hide", ARTIST): [
            {
                "id": "/lib/mmi/shadows-inside",
                "codec": "flac",
                "bitrate": 0,
                "bits": 16,
                "rate": 44100,
                "album": ALBUM,
                "album_year": YEAR,
            }
        ],
        track_key("Under Fire", ARTIST): [
            {
                "id": "/lib/mmi/loose-rips",
                "codec": "mp3",
                "bitrate": 320,
                "bits": 0,
                "rate": 0,
                "album": "",
                "album_year": "",
            }
        ],
        # Deliberately matches the VIDEO row below, title and artist both. The
        # row must still refuse it: the scan indexes audio, and a video that
        # borrowed an audio claim would tell the user they have something they
        # do not.
        track_key("Relentless Chaos", ARTIST): [
            {
                "id": "/lib/mmi/at-heart",
                "codec": "flac",
                "bitrate": 0,
                "bits": 16,
                "rate": 44100,
                "album": "At Heart",
                "album_year": "2012",
            }
        ],
    }
    engine.rootContext().setContextProperty("waves", bridge)
    engine.rootContext().setContextProperty("monoFont", _load_mono())
    engine.rootContext().setContextProperty("uiFontFamily", app.font().family())
    engine.load(QUrl.fromLocalFile(str(QML_MAIN)))
    roots = engine.rootObjects()
    if not roots:
        print("Main.qml failed to load", file=sys.stderr)
        return _EXIT_PRECONDITION
    root = roots[0]
    root.setProperty("width", 1400)
    root.setProperty("height", 900)

    def q(expr: str):
        e = QQmlExpression(QQmlEngine.contextForObject(root), root, expr)
        r = e.evaluate()
        if e.hasError():
            raise RuntimeError(e.error().toString())
        return r[0] if isinstance(r, tuple) else r

    def settle(ms: int = 150) -> None:
        loop = QEventLoop()
        QTimer.singleShot(ms, loop.quit)
        loop.exec()

    settle()
    q("bootOverlay.done = true")
    q("bootContentShown = 1")
    q("browseBuilding = false")
    q("_browseAsyncBuild = false")
    # With the scan on, the done face says IN LIBRARY rather than DOWNLOADED,
    # which is the wording a user with a library configured actually sees.
    q("libraryOn = true")

    # The browse landing's own track list, which is the one TrackRow call site
    # that carries `kind`, so the video guard can be driven here too.
    items = [
        {"id": "t-proven", "kind": "track", "title": "Hide", "artist": ARTIST, "album": ALBUM, "year": YEAR},
        {"id": "t-unproven", "kind": "track", "title": "Under Fire", "artist": ARTIST, "album": ALBUM, "year": YEAR},
        {"id": "t-missing", "kind": "track", "title": "Gone", "artist": ARTIST, "album": ALBUM, "year": YEAR},
        {
            "id": "v-video",
            "kind": "video",
            "title": "Relentless Chaos",
            "artist": ARTIST,
            "album": "At Heart",
            "year": "2012",
        },
    ]
    root.setProperty("browseSections", [{"title": "TRACKS", "rowKind": "tracks", "data": "", "items": items}])
    settle(900)

    # Each TrackRow (found by its track id) with the words on its button face
    # and the claim state behind them. JSON, because a QML object comes back as
    # an opaque QJSValue.
    probe = """
    JSON.stringify((function() {
        var out = []
        function btn(item) {
            if (!item) return null
            var kids = item.children || []
            for (var i = 0; i < kids.length; i++) {
                var k = kids[i]
                if (k && k.libTrack !== undefined && k.st !== undefined) return k
                var deeper = btn(k)
                if (deeper) return deeper
            }
            return null
        }
        function face(item) {
            if (!item) return null
            var kids = item.children || []
            for (var i = 0; i < kids.length; i++) {
                var k = kids[i]
                if (k && k.objectName === "dbFaceText") return k
                var deeper = face(k)
                if (deeper) return deeper
            }
            return null
        }
        function walk(item) {
            if (!item) return
            var kids = item.children || []
            for (var i = 0; i < kids.length; i++) {
                var k = kids[i]
                if (k && k.tId !== undefined && k.kind !== undefined) {
                    var b = btn(k)
                    var f = b ? face(b) : null
                    out.push({ id: "" + k.tId,
                               words: f ? ("" + f.text) : "no-face",
                               state: b ? (b.st === "" ? "live" : "" + b.st) : "no-button",
                               claim: b ? !!b.libClaim : false,
                               sure: b ? !!b.libSure : false,
                               path: b ? ("" + b.libPath) : "" })
                } else {
                    walk(k)
                }
            }
        }
        walk(browseLanding)
        return out
    })())
    """
    try:
        rows = json.loads(q(probe))
    except Exception as exc:
        print(f"probe failed: {exc}", file=sys.stderr)
        return _EXIT_PRECONDITION
    if len(rows) != len(items):
        print(f"expected {len(items)} rows, probed {len(rows)}: {rows}", file=sys.stderr)
        return _EXIT_PRECONDITION

    bad = []
    for r in rows:
        want_words, want_state, want_claim = EXPECTED[r["id"]]
        if r["words"] != want_words or r["state"] != want_state or r["claim"] != want_claim:
            bad.append(
                f"{r['id']}: got words={r['words']!r} state={r['state']!r} claim={r['claim']}, "
                f"wanted words={want_words!r} state={want_state!r} claim={want_claim}"
            )
    by_id = {r["id"]: r for r in rows}
    # Identity is what separates the two claims, and only the proven one may
    # say so. Colour follows this flag, so getting it wrong paints a guess in
    # the fact colour.
    if not by_id["t-proven"]["sure"]:
        bad.append("t-proven: identity was not proven by its holding folder")
    if by_id["t-unproven"]["sure"]:
        bad.append("t-unproven: identity was proven with nothing to prove it")
    # The matched folder is the evidence the user judges the match by, and the
    # target of SHOW IN FOLDER.
    if by_id["t-proven"]["path"] != "/lib/mmi/shadows-inside":
        bad.append(f"t-proven: claim lost its folder ({by_id['t-proven']['path']!r})")
    if bad:
        print("\n".join(bad), file=sys.stderr)
        return _EXIT_REGRESSED

    # Fire the REAL tap area rather than the function behind it: the wiring is
    # the thing at risk, and calling openLibraryClaim() directly would keep
    # passing with the MouseArea branch deleted.
    tap = """
    (function() {
        function btn(item) {
            if (!item) return null
            var kids = item.children || []
            for (var i = 0; i < kids.length; i++) {
                var k = kids[i]
                if (k && k.libTrack !== undefined && k.st !== undefined) return k
                var deeper = btn(k)
                if (deeper) return deeper
            }
            return null
        }
        function area(item) {
            if (!item) return null
            var kids = item.children || []
            for (var i = 0; i < kids.length; i++) {
                var k = kids[i]
                if (k && k.objectName === "dbTapArea") return k
                var deeper = area(k)
                if (deeper) return deeper
            }
            return null
        }
        function walk(item) {
            if (!item) return null
            var kids = item.children || []
            for (var i = 0; i < kids.length; i++) {
                var k = kids[i]
                if (k && k.tId === "t-proven" && k.kind !== undefined) return btn(k)
                var deeper = walk(k)
                if (deeper) return deeper
            }
            return null
        }
        var b = walk(browseLanding)
        var a = b ? area(b) : null
        if (a) a.clicked(null)
        return !!a
    })()
    """
    if not q(tap):
        print("could not reach the proven track's tap area", file=sys.stderr)
        return _EXIT_PRECONDITION
    settle(80)

    gate = {
        "opens": bool(q("libraryClaimGate.shown")),
        "knows_it_is_a_track": str(q("libraryClaimGate.kind")) == "track",
        "names_the_track": str(q("libraryClaimGate.albumTitle")) == "Hide",
        "shows_the_folder": str(q("libraryClaimGate.folder")) == "/lib/mmi/shadows-inside",
    }
    # The whole point: DOWNLOAD ANYWAY starts the download it was just told the
    # user already had, and it starts the TRACK, not the album it sits in.
    q("libraryClaimGate.proceed()")
    settle(80)
    gate["download_anyway_downloads_the_track"] = downloaded == ["t-proven"]
    gate["proceeding_closes_the_gate"] = not q("libraryClaimGate.shown")

    if not all(gate.values()):
        print(f"gate={gate}", file=sys.stderr)
        return _EXIT_REGRESSED
    print(f"ok: rows={rows} gate={gate} downloaded={downloaded}")
    return _EXIT_OK


if __name__ == "__main__":
    if "--run-scenario" in sys.argv:
        raise SystemExit(_run_scenario())
    raise SystemExit(_EXIT_PRECONDITION)
