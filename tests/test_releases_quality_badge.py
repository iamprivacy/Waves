"""The Releases feed's quality badge, on screen.

Each album card in the feed wears a quality mark in its cover's top-right
corner (CoverMarks): the best tier the release offers, never above the audio
quality the Settings page asks for (Hi-Res on offer under a Lossless setting
reads LOSSLESS), or ATMOS for a Dolby Atmos release (Atmos-only, or Atmos
beside stereo while the Atmos setting is on). At rest it is a 16px dot; under
the cover's hover it opens into its word. The scenario proves, on cards
loaded through the real store and bridge:

- the capped word on every card, and no mark for a row stored without a tier;
- a new audio quality (targetTierChanged) and a new Atmos setting re-word
  the cards the pane already shows, with no feed reload;
- the mark is a 16px dot 8px into the cover's top-right corner and the card
  keeps its size; the library mark (top-left) sits on the same line and never
  reaches it; the marks stay up while the card's download controls are up;
- opened (the hover, staged through `open`), each mark grows into its word,
  and the word's capitals are centered on the dot's center.

Runs in a SUBPROCESS like the other Main.qml scenarios: building the bridge
installs process-global handlers that must not leak into the suite. Set
WAVES_SHOT_DIR to also save a screenshot of the feed there.
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


def test_the_feed_cards_wear_the_capped_quality() -> None:
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    sandbox = tempfile.mkdtemp(prefix="waves-releases-quality-")
    env["XDG_CONFIG_HOME"] = sandbox
    env["HOME"] = sandbox
    proc = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), "--run-scenario"],
        env=env,
        capture_output=True,
        text=True,
        timeout=240,
    )
    # Teardown noise (bindings asking the bridge as it goes) left out.
    lines = [ln for ln in (proc.stdout + proc.stderr).strip().splitlines() if " of null" not in ln]
    tail = "\n".join(lines[-20:])
    if proc.returncode == _EXIT_NO_QT:
        pytest.skip("PySide6 / offscreen Qt unavailable")
    if proc.returncode == _EXIT_PRECONDITION:
        pytest.skip(f"could not set up the scenario in this environment:\n{tail}")
    assert proc.returncode == _EXIT_OK, f"the feed's quality badges are wrong:\n{tail}"


# Every rendered album card in the feed: its badge's word (null when the
# badge does not show), where the badge sits on the cover, and the card's
# own height against the feed's card height.
_BADGES = """
JSON.stringify((function() {
    var out = {}
    function find(it, name) {
        if (!it) return null
        if (it.objectName === name) return it
        var kids = it.children || []
        for (var i = 0; i < kids.length; i++) { var f = find(kids[i], name); if (f) return f }
        return null
    }
    function walk(it) {
        if (!it) return
        if (it.upcoming !== undefined && it.day !== undefined && it.card !== undefined && it.card.id) {
            var b = find(it, "artQualBadge"), art = b ? b.parent.parent : null
            out["" + it.card.id] = {
                q: b && b.visible ? b.q : null,
                x: b ? Math.round(b.x) : -1, y: b ? Math.round(b.y) : -1,
                w: b ? Math.round(b.width) : -1, h: b ? Math.round(b.height) : -1,
                artW: art ? Math.round(art.width) : -1, artH: art ? Math.round(art.height) : -1,
                cardH: Math.round(it.height), feedH: root.relCardH
            }
            return
        }
        var kids = it.children || []
        for (var i = 0; i < kids.length; i++) walk(kids[i])
    }
    walk(relFeedList.contentItem)
    return out
})())
"""

# Hand one card's ArtCard a library answer ("partial": 3 of its tracks held,
# "full": all of them), then report its library mark's right edge and top,
# and the quality mark's box, as "libRight,libY,x,y,w,h"; "" when the card
# is not rendered.
_LIB_PILL = """
(function(id, how) {
    function find(it, test) {
        if (!it) return null
        if (test(it)) return it
        var kids = it.children || []
        for (var i = 0; i < kids.length; i++) { var f = find(kids[i], test); if (f) return f }
        return null
    }
    var rc = find(relFeedList.contentItem, function (it) {
        return it.upcoming !== undefined && it.card !== undefined && "" + it.card.id === id
    })
    if (!rc) return ""
    var ac = find(rc, function (it) { return it.qualWord !== undefined && it.libPresence !== undefined })
    ac.libPresence = how === "partial" ? { present: true, full: false, sure: true, local_tracks: 3 }
                                       : { present: true, full: true, sure: true, local_tracks: ac.card.tracks }
    var b = find(ac, function (it) { return it.objectName === "artQualBadge" })
    var lib = find(ac, function (it) { return it.objectName === "artLibBadge" })
    if (!lib.visible) return "hidden"
    return [Math.round(lib.x + lib.width), Math.round(lib.y),
            Math.round(b.x), Math.round(b.y), Math.round(b.width), Math.round(b.height)].join(",")
})("%s", "%s")
"""

# One card's two marks, staged open (or closed) through CoverMarks.open:
# each mark's width, its word's opacity, and how far the word's capitals
# sit from the mark's center line (0 = centered).
_OPENED = """
JSON.stringify((function(id, open) {
    function find(it, test) {
        if (!it) return null
        if (test(it)) return it
        var kids = it.children || []
        for (var i = 0; i < kids.length; i++) { var f = find(kids[i], test); if (f) return f }
        return null
    }
    var rc = find(relFeedList.contentItem, function (it) {
        return it.upcoming !== undefined && it.card !== undefined && "" + it.card.id === id
    })
    if (!rc) return null
    var marks = find(rc, function (it) { return it.libState !== undefined && it.qualWord !== undefined && it.open !== undefined })
    marks.open = open
    function read(name) {
        var m = find(marks, function (it) { return it.objectName === name })
        var word = null
        for (var i = 0; i < m.children.length; i++) if (m.children[i].baselineOffset !== undefined) word = m.children[i]
        var fm = null
        for (var j = 0; j < m.data.length; j++) if (m.data[j].tightBoundingRect !== undefined) fm = m.data[j]
        var capH = fm.tightBoundingRect("H").height
        return { w: Math.round(m.width), word: word.text, wordOpacity: word.opacity,
                 off: (word.y + word.baselineOffset - capH / 2) - m.height / 2 }
    }
    return { lib: read("artLibBadge"), qual: read("artQualBadge") }
})("%s", %s))
"""


def _rel(rid: str, title: str, quality: str, *, atmos: bool = False) -> dict:
    return {
        "kind": "album",
        "id": rid,
        "title": title,
        "artist": "Artist One",
        "artist_id": "ar1",
        "artists": [{"id": "ar1", "name": "Artist One"}],
        "art": "",
        "date": "2026-10-12",
        "listed": "",
        "year": "2026",
        "rtype": "album",
        "explicit": False,
        "tracks": 10,
        "duration_sec": 2400,
        "quality": quality,
        "atmos": atmos,
    }


def _run_scenario() -> int:
    here = Path(__file__).resolve()
    sys.path.insert(0, str(here.parent.parent))
    sys.path.insert(0, str(here.parent))
    try:
        from PySide6.QtCore import QEventLoop, QTimer, QUrl
        from PySide6.QtGui import QGuiApplication
        from PySide6.QtQml import QQmlApplicationEngine, QQmlEngine, QQmlExpression
        from PySide6.QtQuick import QQuickWindow  # noqa: F401 (types the root window, for grabWindow)
    except Exception as exc:  # pragma: no cover - environment guard
        print(f"Qt unavailable: {exc}", file=sys.stderr)
        return _EXIT_NO_QT

    app = QGuiApplication.instance() or QGuiApplication([])
    try:
        from _qml_offline import PARK_LOGIN_QML, patch_offline

        patch_offline()
        from waves.waves_ui.app import _load_mono
        from waves.waves_ui.backend import WavesBridge
    except Exception as exc:  # pragma: no cover - environment guard
        print(f"Qt platform/backend unavailable: {exc}", file=sys.stderr)
        return _EXIT_NO_QT

    import datetime as _datetime

    from waves.waves_ui import bridge_releases

    class _Day(_datetime.date):
        @classmethod
        def today(cls):
            return cls(2026, 10, 14)

    bridge_releases._dt = type("_Dt", (), {"date": _Day})

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
    root.setProperty("width", 1280)
    root.setProperty("height", 900)

    def q(expr: str):
        e = QQmlExpression(QQmlEngine.contextForObject(root), root, expr)
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

    def badges() -> dict:
        return json.loads(q(_BADGES))

    loads = {"n": 0}
    bridge.releasesLoaded.connect(lambda *_a: loads.__setitem__("n", loads["n"] + 1))

    failures: list[str] = []
    settle()
    q(PARK_LOGIN_QML)
    q("bootOverlay.done = true")
    q("bootContentShown = 1")
    q("newCutoffClock.running = false")
    q('todayIso = "2026-10-14"')

    # The Settings tier starts at the top, with the Atmos setting off.
    bridge.applySettings({"quality_audio": "hi_res_lossless", "download_dolby_atmos": False})
    settle()
    if q("targetTier") != "HI-RES" or q("atmosOn"):
        print(f"the settings did not land: {q('targetTier')!r}, atmos {q('atmosOn')}", file=sys.stderr)
        return _EXIT_PRECONDITION

    store = bridge._releases
    bridge._waves_prefs["releases_window_months"] = "120"
    store.follow("ar1", "Artist One", "")
    rows = [
        _rel("hires", "Hi Res Album", "HI-RES"),
        _rel("lossless", "Lossless Album", "LOSSLESS"),
        _rel("high", "High Album", "HIGH"),
        # An Atmos-only release with no stereo twin in the feed (its title is
        # its own), so it stays a card of its own.
        _rel("atmosonly", "Spatial Only (Dolby Atmos)", "ATMOS"),
        # Stereo and Atmos under one id, how TIDAL ships new releases.
        _rel("both", "Both Album", "HI-RES", atmos=True),
        # A row stored before its tier was known.
        _rel("blank", "Blank Album", ""),
        # A stereo edition with its Atmos edition under its own id beside it:
        # the feed folds the two into one card.
        _rel("folded", "Folded Album", "HI-RES"),
        _rel("foldedtwin", "Folded Album (Dolby Atmos)", "ATMOS"),
    ]
    store.record_check("ar1", rows)
    q("openReleases()")
    if not pump(lambda: len(badges()) >= len(rows) - 1):
        print(f"the feed never rendered every card: {sorted(badges())}", file=sys.stderr)
        return _EXIT_PRECONDITION
    settle(300)

    def check(step: str, want: dict) -> None:
        got = badges()
        for rid, word in want.items():
            b = got.get(rid)
            if b is None:
                failures.append(f"{step}: card {rid} is not on screen")
            elif b["q"] != word:
                failures.append(f"{step}: card {rid} reads {b['q']!r}, wanted {word!r}")

    # 1. Under a Hi-Res setting each card states what its release offers.
    check(
        "Hi-Res setting",
        {
            "hires": "HI-RES",
            "lossless": "LOSSLESS",
            "high": "HIGH",
            "atmosonly": "ATMOS",
            "both": "HI-RES",
            "blank": None,
        },
    )

    # 2. At rest the mark is a 16px dot 8px into the cover's top-right
    # corner, and the card is the feed's card size.
    for rid, b in badges().items():
        if b["cardH"] != b["feedH"]:
            failures.append(f"card {rid} is {b['cardH']} tall, the feed's cards are {b['feedH']}")
        if b["q"] is None:
            continue
        if b["h"] != 16 or b["w"] != 16 or b["y"] != 8 or b["x"] + b["w"] != b["artW"] - 8:
            failures.append(f"card {rid}'s mark sits at {b}, wanted a 16px dot 8px into the cover's top-right corner")

    # 3. A Lossless setting caps the Hi-Res cards, Atmos-only stays ATMOS, and
    # the cards re-word without the feed being read again.
    before = loads["n"]
    bridge.applySettings({"quality_audio": "high_lossless"})
    settle()
    check(
        "Lossless setting",
        {
            "hires": "LOSSLESS",
            "lossless": "LOSSLESS",
            "high": "HIGH",
            "atmosonly": "ATMOS",
            "both": "LOSSLESS",
            "folded": "LOSSLESS",
        },
    )
    if loads["n"] != before:
        failures.append("a new audio quality reloaded the feed instead of re-wording its cards")

    # 4. The Atmos setting puts ATMOS on a release carrying Atmos beside
    # stereo (what a download of it lands as), and nowhere else.
    bridge.applySettings({"download_dolby_atmos": True})
    settle()
    check("Atmos on", {"hires": "LOSSLESS", "both": "ATMOS", "atmosonly": "ATMOS", "high": "HIGH", "folded": "ATMOS"})

    # 5. A High setting caps every lossless card at HIGH.
    bridge.applySettings({"quality_audio": "low_320k", "download_dolby_atmos": False})
    settle()
    check("High setting", {"hires": "HIGH", "lossless": "HIGH", "high": "HIGH", "both": "HIGH", "atmosonly": "ATMOS"})
    bridge.applySettings({"quality_audio": "low_96k"})
    settle()
    check("Low setting", {"hires": "LOW", "high": "LOW", "atmosonly": "ATMOS", "blank": None})
    bridge.applySettings({"quality_audio": "high_lossless"})
    settle()

    # 6. The library mark takes the other top corner, on the same line, and
    # the two never meet; the quality mark does not move for it.
    for rid, how in (("hires", "full"), ("lossless", "partial")):
        q(_LIB_PILL % (rid, how))
        settle(100)
        got = q(_LIB_PILL % (rid, how))
        if got in ("", "hidden"):
            failures.append(f"a {how} library answer put no library mark on card {rid}")
            continue
        lib_right, lib_y, x, y, w, h = (int(v) for v in got.split(","))
        b = badges()[rid]
        if (x, y) != (b["x"], b["y"]) or lib_y != y or lib_right >= x:
            failures.append(
                f"beside a {how} library mark (right {lib_right}, y {lib_y}) the quality mark sits at {x},{y} {w}x{h}"
            )

    # 7. Opened, each mark grows into its word, the word shows, and its
    # capitals sit on the dot's center line (the glyphs are centered there
    # too, so the two read as one piece). Closed again, both are dots.
    for rid, lib_word, qual_word in (("hires", "IN LIBRARY", "LOSSLESS"), ("lossless", "PARTIAL", "LOSSLESS")):
        opened = json.loads(q(_OPENED % (rid, "true")))
        settle(400)
        opened = json.loads(q(_OPENED % (rid, "true")))
        for key, want in (("lib", lib_word), ("qual", qual_word)):
            m = opened[key]
            if m["word"] != want or m["w"] <= 16 or m["wordOpacity"] != 1:
                failures.append(
                    f"card {rid}'s {key} mark opened as {m}, wanted the word {want!r} shown in a wider mark"
                )
            if abs(m["off"]) > 1:
                failures.append(f"card {rid}'s {key} mark's capitals sit {m['off']:.2f}px off the mark's center")
        q(_OPENED % (rid, "false"))
        settle(400)
        closed = json.loads(q(_OPENED % (rid, "false")))
        if closed["lib"]["w"] != 16 or closed["qual"]["w"] != 16 or closed["qual"]["wordOpacity"] != 0:
            failures.append(f"card {rid}'s marks did not close back to dots: {closed}")

    # 8. The marks stay up while the card's download controls are up: the
    # top edge is clear of the strip.
    opacity = """(function() {
        var b = null
        ;(function walk(it) {
            if (b || !it) return
            if (it.upcoming !== undefined && it.card !== undefined && "" + it.card.id === "high") {
                ;(function inner(k) {
                    if (b || !k) return
                    if (k.objectName === "artQualBadge") { b = k; return }
                    for (var i = 0; i < k.children.length; i++) inner(k.children[i])
                })(it)
                return
            }
            var k = it.children || []
            for (var i = 0; i < k.length; i++) walk(k[i])
        })(relFeedList.contentItem)
        return b ? b.opacity : -1
    })()"""
    if q(opacity) != 1:
        failures.append(f"the mark is not fully shown at rest (opacity {q(opacity)})")
    q('dlHolders["high"] = { st: "running" }; dlHoldersGen++')
    settle(400)
    if q(opacity) != 1:
        failures.append(f"the mark stepped aside under the download controls (opacity {q(opacity)})")
    q('delete dlHolders["high"]; dlHoldersGen++')
    settle(400)

    shots = os.environ.get("WAVES_SHOT_DIR", "")
    if shots:
        settle(300)
        root.grabWindow().save(str(Path(shots) / "releases_quality_badge.png"))

    if failures:
        print("\n".join(failures), file=sys.stderr)
        return _EXIT_REGRESSED
    print("ok")
    return _EXIT_OK


if __name__ == "__main__" and "--run-scenario" in sys.argv:
    sys.exit(_run_scenario())
