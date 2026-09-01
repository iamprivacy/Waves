"""A browse card has to say what your library holds, without being hovered.

WHAT THIS FENCES OFF
--------------------
Everywhere else the download control is a full-width button that can spell
out ALBUM IN LIBRARY. On a browse card it is one half of a pill riding on
the artwork, and that pill only rises under the cursor. So a shelf of
albums said nothing at all about what was already on disk: a record owned
in full looked exactly like one never heard of, and the only way to find
out was to open the page.

Two things answer it now, and they answer different questions:

* the library PILL sits on the artwork and does not wait for a hover, so a
  shelf reads at a glance (this is the half a hover-gated control cannot do);
* the strip's download HALF carries the verdict in the same four colours the
  full button uses, so what the click will do is never a surprise.

Both are pinned here, along with the two ways they must stay quiet: a
playlist or mix card has no album identity and must never wear an album's
badge, and an album that is not in the library must look exactly as it
always did.

Runs in a SUBPROCESS for the same reason as the other Main.qml scenarios:
building the bridge installs process-global handlers that must not leak
into the rest of the suite.
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

QML_MAIN = Path(__file__).resolve().parent.parent / "waves" / "waves_ui" / "qml" / "Main.qml"

# (title, artist, year, files held, declared by the release). Chosen so the
# three album verdicts each arise for a DIFFERENT reason.
SEED = [
    ("Shadows Inside", "Miss May I", "2017", 10, 10),  # complete and provable
    ("Geogaddi", "Boards of Canada", "", 23, 0),  # complete, undated, unproven
    ("Rumours", "Fleetwood Mac", "1977", 7, 11),  # short of the album
]

# What each card must resolve to: (kind, libState, the word on the download half)
EXPECTED = {
    "Shadows Inside": ("proven", "IN LIBRARY"),
    "Geogaddi": ("maybe", "MAYBE"),
    "Rumours": ("partial", "7 OF 11"),
    "Pain Remains": ("", "DOWNLOAD"),  # not in the library at all
    "Deep Focus": ("", "DOWNLOAD"),  # a playlist, never an album verdict
}


def test_browse_cards_carry_the_library_verdict():
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="waves-browse-card-lib-test-")
    proc = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), "--run-scenario"],
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    tail = "\n".join((proc.stdout + proc.stderr).strip().splitlines()[-14:])
    import pytest

    if proc.returncode == _EXIT_NO_QT:
        pytest.skip("PySide6 / offscreen Qt unavailable")
    if proc.returncode == _EXIT_PRECONDITION:
        pytest.skip(f"could not set up the scenario in this environment:\n{tail}")
    assert (
        proc.returncode == _EXIT_OK
    ), f"a browse card reported the wrong library state. Scenario exit={proc.returncode}:\n{tail}"


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
        from waves import matching
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
    q('browseStyle = "art"')  # the art-first cards are the surface under test

    # The scan's finished index, handed over the way a real scan hands it over.
    # No folder is walked and no setting is written: library_enabled is off in
    # this temp config, so _library_root stays empty and no scan can start.
    index: dict = {}
    for title, artist, year, tracks, declared in SEED:
        index.setdefault(matching.presence_key(title, artist), []).append(
            {
                "title": title,
                "year": year,
                "tracks": tracks,
                "id": f"/lib/{artist}/{title}",
                "codec": "flac",
                "bitrate": 900,
                "bits": 16,
                "rate": 44100,
                "declared": declared,
                "disc_no": 0,
                "disc_total": 0,
            }
        )
    bridge._library_index = index
    q("libraryOn = true")

    items = [
        {"id": "a1", "kind": "album", "title": "Shadows Inside", "artist": "Miss May I", "year": "2017", "tracks": 10},
        {"id": "a2", "kind": "album", "title": "Geogaddi", "artist": "Boards of Canada", "year": "2002", "tracks": 23},
        {"id": "a3", "kind": "album", "title": "Rumours", "artist": "Fleetwood Mac", "year": "1977", "tracks": 11},
        {"id": "a4", "kind": "album", "title": "Pain Remains", "artist": "Lorna Shore", "year": "2022", "tracks": 13},
        {"id": "p1", "kind": "playlist", "title": "Deep Focus", "artist": "", "year": "", "tracks": 40},
    ]
    root.setProperty("browseSections", [{"title": "ALBUMS", "rowKind": "cards", "data": "", "items": items}])
    settle(900)

    # Every ArtCard, found by libState (nothing else in the tree declares one),
    # with the word its strip would show and whether its pill is up. The strip
    # is a child of the card, so it is picked up on the way down. JSON, because
    # a QML object comes back as an opaque QJSValue.
    probe = """
    JSON.stringify((function() {
        var out = []
        function strip(item) {
            if (!item) return null
            var kids = item.children || []
            for (var i = 0; i < kids.length; i++) {
                if (kids[i] && kids[i].stWord !== undefined) return kids[i].stWord
                var deeper = strip(kids[i])
                if (deeper !== null) return deeper
            }
            return null
        }
        function pill(item) {
            if (!item) return false
            var kids = item.children || []
            for (var i = 0; i < kids.length; i++) {
                var k = kids[i]
                if (k && k.proven !== undefined && k.qclass !== undefined && k.visible) return true
                if (pill(k)) return true
            }
            return false
        }
        function walk(item) {
            if (!item) return
            var kids = item.children || []
            for (var i = 0; i < kids.length; i++) {
                var k = kids[i]
                if (k && k.libState !== undefined && k.card) {
                    out.push({ title: "" + (k.card.title || ""), state: "" + k.libState,
                               word: strip(k), pill: pill(k) })
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
        cards = json.loads(q(probe))
    except Exception as exc:
        print(f"probe failed: {exc}", file=sys.stderr)
        return _EXIT_PRECONDITION
    if len(cards) != len(items):
        print(f"expected {len(items)} cards, probed {len(cards)}: {cards}", file=sys.stderr)
        return _EXIT_PRECONDITION

    bad = []
    for c in cards:
        want_state, want_word = EXPECTED[c["title"]]
        # The pill is up exactly when the album is in the library, and never on
        # a playlist. It does NOT wait for a hover: that is the point of it.
        want_pill = want_state != ""
        if c["state"] != want_state or c["word"] != want_word or c["pill"] != want_pill:
            bad.append(
                f"{c['title']}: got state={c['state']!r} word={c['word']!r} pill={c['pill']}, "
                f"wanted state={want_state!r} word={want_word!r} pill={want_pill}"
            )
    if bad:
        print("\n".join(bad), file=sys.stderr)
        return _EXIT_REGRESSED
    print(f"ok: {cards}")
    return _EXIT_OK


if __name__ == "__main__":
    if "--run-scenario" in sys.argv:
        raise SystemExit(_run_scenario())
    raise SystemExit(_EXIT_PRECONDITION)
