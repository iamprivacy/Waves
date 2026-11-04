"""Browse's art cards say what the Releases feed says, capped the same way.

The feed's cards wrap a title to three lines (then an ellipsis) and credits
to two (the names past them fold into "+N more"), with a mono line over the
title for an album: NEW, the kind, the E, the date. Browse's art cards kept
one elided title line over one elided caption line, so the same album read
two ways. An album card now carries the feed's own words (ReleaseText), and
any other card wraps its title to the feed's cap over its caption.

Words that run to different heights need a shelf or grid that makes room for
them: each is laid out at its tallest card's words, measured before any card
is built (artCardTextH), so nothing is cut off and the tallest card leaves no
blank under its words. A grid sizes each row to its own tallest card.

Runs in a SUBPROCESS like the other Main.qml scenarios.
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


def test_art_cards_wrap_and_cap_their_words_as_the_feed_does():
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    sandbox = tempfile.mkdtemp(prefix="waves-art-card-words-")
    env["XDG_CONFIG_HOME"] = sandbox
    env["HOME"] = sandbox
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
    assert proc.returncode == _EXIT_OK, f"Browse's art card words are wrong:\n{tail}"


_CROWD = [{"id": f"ar{i}", "name": f"Featured Artist Number {i}"} for i in range(9)]


def _cards() -> list[dict]:
    def album(cid: str, title: str, artists: list[dict]) -> dict:
        return {
            "id": cid,
            "kind": "album",
            "title": title,
            "artist": artists[0]["name"],
            "artist_id": artists[0]["id"],
            "artists": artists,
            "date": "2019-05-03",
            "year": "2019",
            "rtype": "single",
            "explicit": True,
            "tracks": 3,
            "art": "",
        }

    long_title = "A Title That Runs On " * 6
    return [
        album("al-long", long_title.strip(), [{"id": "a1", "name": "Solo Artist"}]),
        album("al-crowd", "Crowded", _CROWD),
        album("al-short", "Short", [{"id": "a2", "name": "Another Artist"}]),
        {
            "id": "pl-long",
            "kind": "playlist",
            "title": "A Playlist Whose Name Goes On And On Well Past One Line " * 2,
            "tracks": 40,
            "art": "",
        },
        album("al-5", "Five", [{"id": "a5", "name": "Five Artist"}]),
        album("al-6", "Six", [{"id": "a6", "name": "Six Artist"}]),
        album("al-7", "Seven", [{"id": "a7", "name": "Seven Artist"}]),
        album("al-8", "Eight", [{"id": "a8", "name": "Eight Artist"}]),
        album("al-9", "Nine", [{"id": "a9", "name": "Nine Artist"}]),
    ]


_HERO_SHELF = 284

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

# Every art card on the shown Browse pane: its slot, its words and the view
# holding it (a shelf's ListView, or the grid's Loader for that card).
_PROBE = (
    "JSON.stringify((function(pane) {" + _COLLECT + " return collect(pane, function(k) {"
    "   if (k.bare === undefined || k.qualWord === undefined || !k.card || !k.visible) return null;"
    "   function one(name) { var f = collect(k, function(b) {"
    "       return b.objectName === name && b.visible ? b : null; }, []); return f.length ? f[0] : null }"
    "   var words = one('artCardWords'), title = one('releaseTitle') || one('artCardTitle');"
    "   var flow = one('artistFlow'), more = one('artistFlowMore');"
    "   var shelf = k.parent; while (shelf && shelf.orientation === undefined) shelf = shelf.parent;"
    "   return { id: '' + k.card.id, h: k.height, slotH: k.parent.height,"
    "            y: k.mapToItem(pane, 0, 0).y, x: k.mapToItem(pane, 0, 0).x,"
    "            wordsBottom: words ? words.y + words.height : -1,"
    "            lines: title ? title.lineCount : -1, cut: title ? title.truncated : false,"
    "            flowH: flow ? flow.height : -1, more: more ? '' + more.text : '',"
    "            lineH: more ? more.height : -1, shelfH: shelf ? shelf.height : -1 }"
    " }, []) })(%s))"
)


def _run_scenario() -> int:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    try:
        from PySide6.QtCore import QEventLoop, QTimer, QUrl
        from PySide6.QtGui import QGuiApplication
        from PySide6.QtQml import QQmlApplicationEngine, QQmlEngine, QQmlExpression
        from PySide6.QtQuick import QQuickWindow  # noqa: F401 (types the root window, for grabWindow)
    except Exception as exc:
        print(f"Qt unavailable: {exc}", file=sys.stderr)
        return _EXIT_NO_QT

    app = QGuiApplication.instance() or QGuiApplication([])
    try:
        from waves.waves_ui.backend import WavesBridge
    except Exception as exc:
        print(f"Qt platform/backend unavailable: {exc}", file=sys.stderr)
        return _EXIT_NO_QT

    engine = QQmlApplicationEngine()
    bridge = WavesBridge(tidal=None)
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
    root.setProperty("height", 1000)
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
    q("root.todayIso = '2019-05-10'")
    q("root.openBrowse()")
    q("root.browseStyle = 'art'")
    cards = _cards()
    section = {"title": "ALBUMS", "rowKind": "cards", "data": "", "items": cards}
    # The landing's first art row is the big-cover hero, which shows no
    # words; the row under test is the second.
    lead = {"title": "LEAD", "rowKind": "cards", "data": "", "items": cards[4:]}
    root.setProperty("browseSections", [lead, section])
    settle(900)

    bad: list[str] = []

    def probe(pane: str) -> dict:
        return {r["id"]: r for r in json.loads(q(_PROBE % pane))}

    # A shelf builds only the cards in view; the four that probe the caps
    # lead the row so they always are.
    probes = {"al-long", "al-crowd", "al-short", "pl-long"}

    def check(label: str, seen: dict, want: int) -> None:
        if len(seen) < want or not probes <= set(seen):
            bad.append(f"{label}: built {len(seen)} of {len(cards)} cards: {sorted(seen)}")
            return
        for cid, m in seen.items():
            if m["wordsBottom"] < 0:
                bad.append(f"{label}: {cid} shows no words")
            elif m["wordsBottom"] > m["h"] + 0.5:
                bad.append(f"{label}: {cid}'s words run {m['wordsBottom'] - m['h']:.1f}px past its card")
        long_, crowd, pl = seen["al-long"], seen["al-crowd"], seen["pl-long"]
        if not (long_["lines"] == 3 and long_["cut"]):
            bad.append(f"{label}: a long album title is not cut at three lines: {long_}")
        if not (1 < pl["lines"] <= 3 and pl["cut"]):
            bad.append(f"{label}: a long playlist title does not wrap to its three-line cap: {pl}")
        if not crowd["more"].startswith("+") or crowd["flowH"] > 2 * crowd["lineH"] + 0.5:
            bad.append(f"{label}: a crowd of credits is not folded at two lines: {crowd}")
        if seen["al-short"]["lines"] != 1:
            bad.append(f"{label}: a short album title is not one line: {seen['al-short']}")

    # The landing's art shelf: one height for all, the tallest card's.
    shelf = {k: v for k, v in probe("browseLanding").items() if v["shelfH"] != _HERO_SHELF}
    check("shelf", shelf, len(probes))
    if probes <= set(shelf):
        tallest = max(m["wordsBottom"] for m in shelf.values())
        for cid, m in shelf.items():
            if m["shelfH"] < m["h"]:
                bad.append(f"shelf: {cid} is {m['h']}px in a {m['shelfH']}px shelf")
        if abs(tallest - shelf["al-long"]["h"]) > 1.0:
            bad.append(f"shelf: cards are {shelf['al-long']['h']}px for words {tallest:.1f}px tall")
    shot = os.environ.get("ART_CARD_SHOT", "")
    if shot:
        root.grabWindow().save(shot)

    # A drilled page: a grid whose rows are each their own tallest card.
    q("root.openBrowseSection(" + json.dumps(section) + ")")
    settle(1200)
    grid = probe("browseDrill")
    check("grid", grid, len(cards))
    rows: dict[float, list] = {}
    for m in grid.values():
        rows.setdefault(round(m["y"]), []).append(m)
    if len(grid) == len(cards) and len(rows) < 2:
        bad.append(f"grid: expected the cards to run over more than one row: {sorted(rows)}")
    for y, row in rows.items():
        tallest = max(m["wordsBottom"] for m in row)
        for m in row:
            if abs(m["slotH"] - tallest) > 1.0:
                bad.append(f"grid row at {y}: {m['id']}'s slot is {m['slotH']}px for words {tallest:.1f}px tall")
    shot = os.environ.get("ART_CARD_GRID_SHOT", "")
    if shot:
        root.grabWindow().save(shot)

    for line in bad:
        print(line, file=sys.stderr)
    return _EXIT_REGRESSED if bad else _EXIT_OK


if __name__ == "__main__" and "--run-scenario" in sys.argv:
    sys.exit(_run_scenario())
