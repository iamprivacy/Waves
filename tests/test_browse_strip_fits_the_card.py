"""The hover strip on a browse card stays inside the artwork.

WHAT THIS FENCES OFF
--------------------
The strip is sized by its own words (each half hugs its label), and nothing
capped it against the card it rides on. The artwork clips, and the pill is
centred, so a strip wider than the cover was cut off at BOTH ends: the pill
lost its rounded corners and the words lost their first and last letters.

It was never a corner case. Measured with the real UI font at the real 10px,
on the ordinary 200px card the everyday DOWNLOAD and IN LIBRARY ran over by
about 10px and DOWNLOADED by about 27, so every state but MAYBE and the
partial count overflowed. Only the worst one was ever noticed.

The fence is the artwork itself: whatever the strip says, in whatever font the
platform gives it, the pill must fit on the cover. That is the boundary that
actually clips, and it holds the fix (the halves' padding gives way as the
words grow) without restating its arithmetic, so a wider word, a wider font or
a smaller card fails here rather than on someone's screen.

Both hard-won lessons from the sibling scenarios apply: the three widest words
must really be on screen (a probe that finds no strip proves nothing), and the
run happens in a SUBPROCESS because building the bridge installs process-global
handlers.
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

# The three widest states, one card each. "IN LIBRARY" comes from a complete,
# provable match; "DOWNLOADED" from the ownership rollup, forced on the card
# itself (the store is empty in a scenario, and the strip reads the property);
# "DOWNLOAD" is the untouched default every shelf is full of.
SEED_TITLE = "Shadows Inside"
SEED_ARTIST = "Miss May I"
WIDEST = {"DOWNLOAD", "IN LIBRARY", "DOWNLOADED"}


def test_the_hover_strip_never_overflows_the_artwork():
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="waves-strip-fit-test-")
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
    ), f"a browse card's hover strip is wider than the cover it rides on. Scenario exit={proc.returncode}:\n{tail}"


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
        from tidaler import matching
        from tidaler.waves_ui.app import _load_mono
        from tidaler.waves_ui.backend import WavesBridge
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
    q('browseStyle = "art"')

    # One complete, provable album so a card can say IN LIBRARY. Nothing is
    # scanned and no setting is written: library_enabled is off in this temp
    # config, so no folder walk can start.
    bridge._library_index = {
        matching.presence_key(SEED_TITLE, SEED_ARTIST): [
            {
                "title": SEED_TITLE,
                "year": "2017",
                "tracks": 10,
                "id": f"/lib/{SEED_ARTIST}/{SEED_TITLE}",
                "codec": "flac",
                "bitrate": 900,
                "bits": 16,
                "rate": 44100,
                "declared": 10,
                "disc_no": 0,
                "disc_total": 0,
            }
        ]
    }
    q("libraryOn = true")

    items = [
        {"id": "a1", "kind": "album", "title": SEED_TITLE, "artist": SEED_ARTIST, "year": "2017", "tracks": 10},
        {"id": "a2", "kind": "album", "title": "Pain Remains", "artist": "Lorna Shore", "year": "2022", "tracks": 13},
        {"id": "a3", "kind": "album", "title": "Rumours", "artist": "Fleetwood Mac", "year": "1977", "tracks": 11},
    ]
    # The SECOND shelf, deliberately: the first landing shelf is the hero row,
    # whose 280px covers swallow even the widest strip. The ordinary card is
    # the one the shelves are full of and the one that clipped.
    root.setProperty(
        "browseSections",
        [
            {"title": "FEATURED", "rowKind": "cards", "data": "", "items": []},
            {"title": "ALBUMS", "rowKind": "cards", "data": "", "items": items},
        ],
    )
    settle(900)

    # The third card is made a finished download the way the strip learns it:
    # the ownership rollup's own property. Its match is deliberately absent
    # from the library index, so the word can only be DOWNLOADED.
    force_owned = """
    (function() {
        function walk(item) {
            if (!item) return null
            var kids = item.children || []
            for (var i = 0; i < kids.length; i++) {
                var k = kids[i]
                if (k && k.libState !== undefined && k.card) {
                    if (("" + (k.card.id || "")) === "a3") { k.owned = true; return true }
                } else if (walk(k)) return true
            }
            return null
        }
        return walk(browseLanding) === true
    })()
    """
    if q(force_owned) is not True:
        print("could not find the card to mark owned", file=sys.stderr)
        return _EXIT_PRECONDITION
    settle(200)

    # Each card's strip: the word it shows and the pill's width, against the
    # artwork it rides on. The strip is the only thing in the tree declaring
    # stWord; the cover is the ArtCard's own artSize.
    probe = """
    JSON.stringify((function() {
        var out = []
        function strip(item) {
            if (!item) return null
            var kids = item.children || []
            for (var i = 0; i < kids.length; i++) {
                var k = kids[i]
                if (k && k.stWord !== undefined) return { word: "" + k.stWord, width: k.width }
                var deeper = strip(k)
                if (deeper !== null) return deeper
            }
            return null
        }
        function walk(item) {
            if (!item) return
            var kids = item.children || []
            for (var i = 0; i < kids.length; i++) {
                var k = kids[i]
                if (k && k.libState !== undefined && k.card) {
                    var s = strip(k)
                    out.push({ id: "" + (k.card.id || ""), art: k.artSize,
                               word: s ? s.word : null, width: s ? s.width : -1 })
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

    # Vacuum guard: the three widest words must actually be on screen. A probe
    # that measured three MAYBEs would pass while proving nothing.
    words = {c["word"] for c in cards}
    if words != WIDEST:
        print(f"the widest states were not all rendered: got {sorted(words)}, wanted {sorted(WIDEST)}", file=sys.stderr)
        return _EXIT_PRECONDITION

    # Four pixels of cover on each side, not zero. The artwork is where the
    # clipping happens, but a pill that merely stops short of being cut has
    # still lost the margin it is drawn with, and the widest words got there
    # first: DOWNLOADED ran 7px past the cover while DOWNLOAD and IN LIBRARY
    # closed to about 5px of it. So the fence sits just inside the edge.
    margin = 4
    over = [
        f"{c['word']!r}: pill {c['width']:.1f}px on a {c['art']:.0f}px cover, "
        f"leaving {(c['art'] - c['width']) / 2:.1f}px a side (wanted at least {margin})"
        for c in cards
        if c["width"] > c["art"] - 2 * margin
    ]
    if over:
        print("\n".join(over), file=sys.stderr)
        return _EXIT_REGRESSED
    print(f"ok: {cards}")
    return _EXIT_OK


if __name__ == "__main__":
    if "--run-scenario" in sys.argv:
        raise SystemExit(_run_scenario())
    raise SystemExit(_EXIT_PRECONDITION)
