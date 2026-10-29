"""The Releases pane, on screen: one continuous scroll of sections.

Today, This week, This month, then one section per earlier month
("September 2026"); what is dated ahead sits under its own Upcoming chip
(soonest first), never in All. Each card says when (TODAY,
YESTERDAY, TOMORROW, or MM-DD-YYYY), what kind (ALBUM, EP, SINGLE), and carries a
dot when it arrived since the last visit. The scenario proves:

- the sections, their order and their counts, from releases put in the real
  store and loaded through the real bridge;
- the chip words and the dot, read off the cards that render;
- opening the pane clears the tab's badge;
- a new release rebuilds only the rows it shifts (an untouched row keeps its
  delegate);
- the feed reads three months at a time and loads the earlier ones as it
  is scrolled to its end;
- the kind filter, and a day rollover (and a month one) re-bucket the rows the
  pane already holds, with no bridge call;
- Back from another tab returns to Releases; the FOLLOWING view lists the
  follows a page at a time (more as it is scrolled near its end) and drops
  an unfollowed artist at once; the artist page's toggle
  follows the store, and its TIDAL side settles when a favorites write lands;
- the follow dialog lists only the answer to the text in its field, follows
  from a row (the row flips in place), opens the artist from a row, and
  closes on Esc's path;
- with the MusicBrainz setting on, a release group TIDAL lacks is a red
  dot on the ledger's timeline and the hint names it with a NOT ON TIDAL
  chip; off, the marks are gone.

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


def test_the_releases_pane_buckets_and_follows_the_clock() -> None:
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    sandbox = tempfile.mkdtemp(prefix="waves-releases-pane-")
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
    assert proc.returncode == _EXIT_OK, f"the Releases pane is wrong on screen:\n{tail}"


# The feed list's section headers, in order, as "label:count".
_HEADS = """
JSON.stringify((function() {
    var out = []
    for (var i = 0; i < releasesModel.count; i++) {
        var it = releasesModel.get(i)
        if (it.rowKind === "head") out.push(it.label + ":" + it.cnt)
    }
    return out
})())
"""

# Card ids in list order, per section label.
_ORDER = """
JSON.stringify((function() {
    var out = {}, cur = ""
    for (var i = 0; i < releasesModel.count; i++) {
        var it = releasesModel.get(i)
        if (it.rowKind === "head") { cur = it.label; out[cur] = []; continue }
        var cards = _relCards[it.uid] || []
        for (var j = 0; j < cards.length; j++) out[cur].push("" + cards[j].id)
    }
    return out
})())
"""

# A roster card's latest-release line: its date and kind chips and its title.
_CARD_LATEST = """
(function() {
    var c = relFollowRoster.itemAtIndex(%d)
    function find(it) {
        if (!it) return null
        if (it.objectName === "raLatest") return it
        var kids = it.children || []
        for (var i = 0; i < kids.length; i++) { var f = find(kids[i]); if (f) return f }
        return null
    }
    var row = find(c)
    if (!row) return ""
    var out = []
    for (var i = 0; i < row.children.length; i++) {
        var k = row.children[i]
        if (k.visible && typeof k.text === "string" && k.text !== "") out.push(k.text)
    }
    return out.join(" | ")
})()
"""

# The first ledger timeline with pins: where its newest pin sits in the
# window, and how many pins it draws.
# The first card row wholly past the load distance (2.5 views below the
# top of the view) and short of the unload distance; -1 when none.
_FEED_ROW_BEYOND = """(function () {
    var v = relFeedList
    for (var i = 0; i < relFeedRows.count; i++) {
        var it = relFeedRows.itemAt(i)
        if (it && it.rowKind === "row" && it.y > v.contentY + v.height * 2.7 && it.y < v.contentY + v.height * 3.8) return i
    }
    return -1
})()"""

# The followed rows, in model order, are in name order.
_NAMES_IN_ORDER = """(function () {
    var a = []
    for (var i = 0; i < releasesFollowedModel.count; i++) a.push("" + _relFollowById[releasesFollowedModel.get(i).uid].name)
    var b = a.slice().sort(function (x, y) { return x.localeCompare(y) })
    return a.length > 1 && a.join("|") === b.join("|")
})()"""
_LEDGER_PIN = """
(function() {
    function find(it, out) {
        if (it.objectName === "relTimeline" && it.visible && it.marks && it.marks.length > 0) out.push(it)
        var kids = it.children || []
        for (var i = 0; i < kids.length; i++) find(kids[i], out)
        return out
    }
    var tls = find(relFollowLedger.contentItem, [])
    if (!tls.length) return ""
    var tl = tls[0], m = tl.marks[tl.marks.length - 1]
    var p = tl.mapToItem(null, tl.xOf(m.dn), tl.baseY - 12)
    return [Math.round(p.x), Math.round(p.y), m.r.id, ("" + m.r.day).slice(0, 4)].join(",")
})()
"""

# What the first ledger timeline's right end reads: NOW at rest, the year
# under the pointer while it scrubs.
_LEDGER_END_WORD = """
(function() {
    function find(it, out) {
        if (it.objectName === "relTimeline" && it.visible && it.marks && it.marks.length > 0) out.push(it)
        var kids = it.children || []
        for (var i = 0; i < kids.length; i++) find(kids[i], out)
        return out
    }
    var tls = find(relFollowLedger.contentItem, [])
    return tls.length ? tls[0].endWord : ""
})()
"""

# Every ledger timeline's carved first year against the year its leftmost
# column scrubs to: "" when they all agree, else the first that does not.
_LEDGER_LEFT_EDGE = """
(function() {
    function find(it, out) {
        if (it.objectName === "relTimeline" && it.visible && it.marks && it.marks.length > 0) out.push(it)
        var kids = it.children || []
        for (var i = 0; i < kids.length; i++) find(kids[i], out)
        return out
    }
    var tls = find(relFollowLedger.contentItem, [])
    for (var i = 0; i < tls.length; i++) {
        var tl = tls[i]
        tl.scrubX = tl.c0 * tl.pitch + 1
        var y = tl.scrubYear
        tl.scrubX = -1
        if (y !== tl.firstYear) return tl.firstYear + " starts at " + y
    }
    return tls.length ? "" : "none"
})()
"""

# The first Shelves row with releases: how many its shelf holds against the
# row's own count, whether it scrolls, where it sits (its center in window
# pixels) and how far it has scrolled, how much of the card is left under
# its tiles, and the list's own scroll.
_SHELF_STRIP = """
JSON.stringify((function() {
    function find(it, out) {
        if (it.objectName === "relShelfStrip" && it.count > 0) out.push(it)
        var kids = it.children || []
        for (var i = 0; i < kids.length; i++) find(kids[i], out)
        return out
    }
    var ss = find(relFollowShelves.contentItem, [])
    if (!ss.length) return null
    var st = ss[0], card = st.parent, tile = st.itemAtIndex(0)
    var c = st.mapToItem(null, st.width / 2, st.height / 2)
    return { count: st.count, total: card.parent.row.total, scrolls: st.contentWidth > st.width,
             x: Math.round(c.x), y: Math.round(c.y), contentX: st.contentX,
             room: tile ? Math.round(card.height - tile.mapToItem(card, 0, tile.height).y) : -1,
             listY: relFollowShelves.contentY, moving: st.moving }
})())
"""

# That row's library counts made as long as they get (or, with "back", back
# to what the row really holds); a frame later, do they still fit the head's
# column?
_SHELF_BADGE_FIT = """
(function(back) {
    function find(it) {
        if (it.objectName === "relShelfStrip" && it.count > 0) return it
        var kids = it.children || []
        for (var i = 0; i < kids.length; i++) { var f = find(kids[i]); if (f) return f }
        return null
    }
    function badges(it) {
        if (it.fitWidth !== undefined && it.fitWidth > 0 && it.pageCountPx !== undefined) return it
        var kids = it.children || []
        for (var i = 0; i < kids.length; i++) { var f = badges(kids[i]); if (f) return f }
        return null
    }
    var st = find(relFollowShelves.contentItem)
    var b = st ? badges(st.parent) : null
    if (!b) return "no badges"
    if (back === "set") b.presence = { present: true, albums: 1234, tracks: 56789 }
    else if (back === "back") b._resolvePresence(false)
    return b.visible && b.width > 0 && b.width <= b.fitWidth + 0.5 ? "" : b.width + " against " + b.fitWidth
})("%s")
"""

# The Ledger's column heads that show, left to right.
_LEDGER_HEADS = """
(function() {
    var h = relFollowLedger.headerItem, out = []
    for (var i = 0; i < h.children.length; i++) {
        var c = h.children[i]
        if (c.visible && c.sortKey !== undefined) out.push([c.x, c.text])
    }
    out.sort(function (a, b) { return a[0] - b[0] })
    return out.map(function (e) { return e[1] }).join("|")
})()
"""

# The first ledger timeline carrying MusicBrainz marks: the center of the
# bar holding the mark "mb:g-lost" in window pixels, and whether the
# timeline draws a red dot; "" when there is none.
_LEDGER_MB_MARK = """
(function() {
    function find(it, out) {
        if (it.objectName === "relTimeline" && it.visible && it.missing && it.missing.length > 0) out.push(it)
        var kids = it.children || []
        for (var i = 0; i < kids.length; i++) find(kids[i], out)
        return out
    }
    function red(it) {
        if (it.width === 3 && it.height === 3 && it.color !== undefined && it.visible && Qt.colorEqual(it.color, root.red)) return true
        var kids = it.children || []
        for (var i = 0; i < kids.length; i++) if (red(kids[i])) return true
        return false
    }
    var tls = find(relFollowLedger.contentItem, [])
    if (!tls.length) return ""
    var tl = tls[0], bar = null
    for (var i = 0; i < tl.lit.length; i++) if (tl.lit[i].ids.indexOf("mb:g-lost") >= 0) bar = tl.lit[i]
    if (!bar) return ""
    var p = tl.mapToItem(null, (bar.c0 + bar.c1 + 1) * tl.pitch / 2, tl.baseY - 12)
    return [Math.round(p.x), Math.round(p.y), red(tl) ? 1 : 0].join(",")
})()
"""

# The first ledger timeline, made narrow and handed a long career (an album
# a year since 1990), a busy year (eight singles and a release TIDAL lacks)
# and a release still to come: whether every release sits in a bar, bars
# never share a column, the busy year's red dot shows, and the bar still to
# come lies inside the grid past NOW.
_LEDGER_LONG_CAREER = """
JSON.stringify((function() {
    function find(it, out) {
        if (it.objectName === "relTimeline" && it.visible && it.marks && it.marks.length > 0) out.push(it)
        var kids = it.children || []
        for (var i = 0; i < kids.length; i++) find(kids[i], out)
        return out
    }
    var tls = find(relFollowLedger.contentItem, [])
    if (!tls.length) return null
    var tl = tls[0], w = tl.width, oldR = tl.releases, oldM = tl.missing, rel = []
    for (var y = 1990; y <= 2025; y++) rel.unshift({ id: "y" + y, title: "Y" + y, day: y + "-06-01", rtype: "album" })
    for (var s = 0; s < 8; s++) rel.unshift({ id: "s" + s, title: "S" + s, day: "2025-0" + (s + 1) + "-02", rtype: "single" })
    var ahead = root._isoDay(new Date(Date.now() + 20 * 86400000))
    rel.unshift({ id: "soon", title: "Soon", day: ahead, rtype: "single" })
    tl.width = 298
    tl.releases = rel
    tl.missing = [{ id: "mb:busy", rgid: "busy", title: "Busy", day: "2025-07-07", rtype: "album", mb: true }]
    var covered = {}, overlap = 0, prevEnd = -1, aheadBar = null, busyRed = false
    var bars = tl.lit.slice().sort(function (a, b) { return a.c0 - b.c0 })
    for (var i = 0; i < bars.length; i++) {
        var b = bars[i]
        if (b.c0 <= prevEnd) overlap++
        prevEnd = Math.max(prevEnd, b.c1)
        for (var j = 0; j < b.ids.length; j++) covered[b.ids[j]] = true
        if (b.ahead) aheadBar = b
        if (b.ids.indexOf("mb:busy") >= 0 && b.dots.indexOf(4) >= 0) busyRed = true
    }
    var out = {
        bars: bars.length, overlap: overlap, busyRed: busyRed,
        uncovered: tl.marks.filter(function (m) { return !covered["" + m.r.id] }).length,
        aheadInGrid: !!aheadBar && aheadBar.c0 > tl.nowCol && aheadBar.c1 <= tl.c1 - 1
    }
    tl.releases = oldR; tl.missing = oldM; tl.width = w
    return out
})())
"""

# How many visible ledger timelines carry MusicBrainz marks.
_LEDGER_MB_COUNT = """
(function() {
    function find(it, out) {
        if (it.objectName === "relTimeline" && it.visible && it.missing && it.missing.length > 0) out.push(it)
        var kids = it.children || []
        for (var i = 0; i < kids.length; i++) find(kids[i], out)
        return out
    }
    return find(relFollowLedger.contentItem, []).length
})()
"""

# Whether the hint shows a NOT ON TIDAL chip, and whether any of its rows
# offers a click (the pointing hand).
_TIP_MB = """
JSON.stringify((function() {
    var chip = false, hand = false
    function walk(it) {
        if (it.objectName === "relTipMbChip" && it.visible && it.text === "NOT ON TIDAL") chip = true
        if (it.cursorShape === Qt.PointingHandCursor && it.containsMouse !== undefined && it.width > 200 && it.enabled) hand = true
        var kids = it.children || []
        for (var i = 0; i < kids.length; i++) walk(kids[i])
    }
    walk(relTip)
    return { chip: chip, hand: hand, visible: relTip.visible }
})())
"""

# The first release in the timeline hint, its center in window pixels.
_TIP_ROW = """
(function() {
    var out = null
    function walk(it) {
        if (!out && it.cursorShape === Qt.PointingHandCursor && it.containsMouse !== undefined && it.width > 200) out = it
        var kids = it.children || []
        for (var i = 0; i < kids.length; i++) walk(kids[i])
    }
    walk(relTip)
    if (!out || !relTip.visible) return ""
    var p = out.mapToItem(null, out.width / 2, out.height / 2)
    return Math.round(p.x) + "," + Math.round(p.y)
})()
"""

# What a group strip in the hint reads (false when it does not show).
_TIP_STRIP = """
(function() {
    var it = %s
    if (!it || !it.visible) return false
    var hit = false
    ;(function walk(o) {
        if (o.objectName === "relTipStripText" && o.visible) hit = o.text
        var k = o.children || []
        for (var i = 0; hit === false && i < k.length; i++) walk(k[i])
    })(it)
    return hit
})()
"""

# Where a group strip's label sits in the hint's list, as "x,y".
_TIP_STRIP_AT = """
(function() {
    var it = %s
    var hit = ""
    ;(function walk(o) {
        if (o.objectName === "relTipStripText" && o.visible) {
            var p = o.mapToItem(relTipList, 0, 0)
            hit = Math.round(p.x) + "," + Math.round(p.y)
        }
        var k = o.children || []
        for (var i = 0; hit === "" && i < k.length; i++) walk(k[i])
    })(it)
    return hit
})()
"""

# The center of a headline word (Releases or Artists) in window pixels.
_HEADLINE = """
(function() {
    function find(it, name) {
        if (!it) return null
        if (it.objectName === name) return it
        var kids = it.children || []
        for (var i = 0; i < kids.length; i++) { var f = find(kids[i], name); if (f) return f }
        return null
    }
    var h = find(releasesTitle, "relHeadline_%s")
    if (!h) return ""
    var p = h.mapToItem(null, h.width / 2, h.height / 2)
    return Math.round(p.x) + "," + Math.round(p.y)
})()
"""

# Whether a rendered release card shows this length on its still.
_VIDEO_LENGTH = """
(function() {
    function walk(it) {
        if (!it) return false
        if (it.visible && typeof it.text === "string" && it.text === "%s") return true
        var kids = it.children || []
        for (var i = 0; i < kids.length; i++) if (walk(kids[i])) return true
        return false
    }
    return walk(relFeedList.contentItem)
})()
"""

# Every rendered release card: its chips and its dot.
_CARDS = """
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
            var d = find(it, "releaseDateChip"), k = find(it, "releaseKindChip"), dot = find(it, "releaseFreshDot")
            out["" + it.card.id] = { date: d ? d.text : null, accent: d ? d.accent : null,
                                     kind: k && k.visible ? k.text : "", dot: !!(dot && dot.visible), w: it.width }
            return
        }
        var kids = it.children || []
        for (var i = 0; i < kids.length; i++) walk(kids[i])
    }
    walk(relFeedList.contentItem)
    return out
})())
"""


# The line under each close name of the review's fifth row, in order.
_CANDIDATE_WHYS = """(function() {
    var out = []
    function walk(it) {
        if (it.objectName === "importCandidateWhy") out.push(it.text)
        for (var i = 0; i < it.children.length; i++) walk(it.children[i])
    }
    walk(importList.itemAtIndex(4))
    return JSON.stringify(out)
})()"""

# Press the FOLLOW / FOLLOWING button of the dialog's first row.
_CLICK_ROW_BTN = """(function() {
    function find(it) {
        if (it.objectName === "followRowBtn") return it
        for (var i = 0; i < it.children.length; i++) { var r = find(it.children[i]); if (r) return r }
        return null
    }
    find(followList.itemAtIndex(0)).clicked()
    return true
})()"""


def _rel(rid: str, day: str, rtype: str, *, explicit: bool = False) -> dict:
    return {
        "kind": "album",
        "id": rid,
        "title": f"Release {rid}",
        "artist": "Artist One",
        "artist_id": "ar1",
        "artists": [{"id": "ar1", "name": "Artist One"}],
        "art": "",
        "date": day,
        "listed": "",
        "year": day[:4],
        "rtype": rtype,
        "explicit": explicit,
        "tracks": 1 if rtype == "single" else 10,
        "duration_sec": 200,
        "quality": "LOSSLESS",
    }


def _run_scenario() -> int:  # noqa: C901
    here = Path(__file__).resolve()
    sys.path.insert(0, str(here.parent.parent))
    sys.path.insert(0, str(here.parent))
    try:
        from PySide6.QtCore import QEventLoop, QTimer, QUrl
        from PySide6.QtGui import QGuiApplication
        from PySide6.QtQml import QQmlApplicationEngine, QQmlEngine, QQmlExpression
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

    # The bridge's calendar agrees with the pane's: the feed's pages count
    # back from the same day the scenario pins todayIso to.
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
    root.setProperty("height", 1600)

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

    def heads() -> list[str]:
        return json.loads(q(_HEADS))

    def order() -> dict:
        return json.loads(q(_ORDER))

    def cards() -> dict:
        return json.loads(q(_CARDS))

    loads = {"n": 0}
    bridge.releasesLoaded.connect(lambda *_a: loads.__setitem__("n", loads["n"] + 1))

    failures: list[str] = []
    settle()
    q(PARK_LOGIN_QML)
    q("bootOverlay.done = true")
    q("bootContentShown = 1")
    # The scenario owns the calendar: the minute clock must not move it.
    q("newCutoffClock.running = false")
    q('todayIso = "2026-10-14"')

    # One followed artist: the first complete catalog is the silent
    # baseline, the next check brings one genuinely new release.
    store = bridge._releases
    # The widest window: the August 2025 row is inside it whatever the real
    # date (the pane reads the feed through the window setting).
    bridge._waves_prefs["releases_window_months"] = "120"
    store.follow("ar1", "Artist One", "")
    base = [
        _rel("u_late", "2026-10-20", "album"),
        _rel("u_soon", "2026-10-16", "single"),
        _rel("tom", "2026-10-15", "ep"),
        _rel("today", "2026-10-14", "single", explicit=True),
        _rel("week", "2026-10-10", "album"),
        _rel("month", "2026-10-02", "ep"),
        _rel("sept", "2026-09-30", "album"),
        _rel("old", "2025-08-01", "single"),
    ]
    store.record_check("ar1", base)
    fresh = store.record_check("ar1", [*base, _rel("fresh", "2026-10-13", "single")])
    if [r["id"] for r in fresh] != ["fresh"]:
        print(f"the store did not report the new release: {fresh}", file=sys.stderr)
        return _EXIT_PRECONDITION

    # 1. The badge counts what is unseen, and opening the pane clears it.
    q("releasesCounts()")
    if q("releasesUnseen") != 1:
        failures.append(f"the tab's badge reads {q('releasesUnseen')} before the visit, wanted 1")
    q("openReleases()")
    # The first page reaches back to July; a list that short asks for the
    # earlier pages by itself, down to August 2025.
    if not pump(lambda: q("releasesModel.count") > 0 and not q("releasesHasMore") and not q("releasesLoadingMore")):
        print("the feed never arrived", file=sys.stderr)
        return _EXIT_PRECONDITION
    settle(400)
    if q("releasesUnseen") != 0:
        failures.append(f"the badge still reads {q('releasesUnseen')} with the pane open")
    if q("navOrigin") != "releases" or not q("releasesOpen"):
        failures.append("openReleases did not put the pane up under its own tab")

    # 2. Sections, order, counts. All leaves out what is dated ahead.
    want = ["Today:1", "This week:2", "This month:1", "September 2026:1", "August 2025:1"]
    if heads() != want:
        failures.append(f"sections {heads()}, wanted {want}")
    got = order()
    if got.get("This week") != ["fresh", "week"]:
        failures.append(f"This week runs {got.get('This week')}, wanted newest first")

    # 3. The chips and the dot on the cards that render.
    def check_cards(expect):
        c = cards()
        for rid, (date_word, accent, kind, dot) in expect.items():
            card = c.get(rid)
            if card is None:
                failures.append(f"card {rid} did not render (rendered: {sorted(c)})")
                continue
            seen = (card["date"], card["accent"], card["kind"], card["dot"])
            if seen != (date_word, accent, kind, dot):
                failures.append(f"card {rid} reads {seen}, wanted {(date_word, accent, kind, dot)}")

    check_cards(
        {
            "today": ("TODAY", False, "SINGLE", False),
            "fresh": ("YESTERDAY", False, "SINGLE", True),
            "week": ("10-10-2026", False, "ALBUM", False),
        }
    )
    if "tom" in cards():
        failures.append("All shows a release dated ahead")

    # 3b. Upcoming is its own chip: every kind dated ahead, soonest first.
    q('releasesFilter = "upcoming"')
    settle(300)
    if heads() != ["Upcoming:3"]:
        failures.append(f"Upcoming shows {heads()}, wanted ['Upcoming:3']")
    if order().get("Upcoming") != ["tom", "u_soon", "u_late"]:
        failures.append(f"Upcoming runs {order().get('Upcoming')}, wanted soonest first")
    check_cards({"tom": ("TOMORROW", True, "EP", False), "u_soon": ("10-16-2026", True, "SINGLE", False)})
    q('releasesFilter = "all"')
    settle(300)

    # 4. A new release today rebuilds the rows it shifts, nothing else.
    q('relFeedList.itemAtIndex(3).objectName = "keptRow"')
    store.record_check("ar1", [*base, _rel("fresh", "2026-10-13", "single"), _rel("fresh2", "2026-10-14", "album")])
    before = loads["n"]
    bridge.loadReleases()
    if not pump(lambda: loads["n"] > before and "Today:2" in heads()):
        failures.append(f"the second release today never showed: {heads()}")
    if q("relFeedList.itemAtIndex(3).objectName") != "keptRow":
        failures.append("a This week row untouched by the new release was rebuilt")

    # 5. The kind filter re-buckets what the pane holds.
    q('releasesFilter = "single"')
    want = ["Today:1", "This week:1", "August 2025:1"]
    if heads() != want:
        failures.append(f"Singles shows {heads()}, wanted {want}")
    q('releasesFilter = "all"')

    # 5b. A music video stands among the releases under All and alone under
    # Videos, with a VIDEO chip and its length on the still; with the Music
    # videos setting on Hide, the chip and the card go.
    clip = {"id": "v1", "title": "Clip", "artist": "Artist One", "artist_id": "ar1", "date": "2026-10-11"}
    store.record_videos("ar1", [{**clip, "duration_sec": 215, "quality": "1080p"}])
    before = loads["n"]
    bridge.releasesChanged.emit()
    if not pump(lambda: loads["n"] > before and "This week:3" in heads()):
        failures.append(f"a music video never joined All: {heads()}")
    settle(300)
    check_cards({"v1": ("10-11-2026", False, "VIDEO", False)})
    # Every shown release date reads MM-DD-YYYY: an album card's caption and
    # an artist page row too; a bare year stays a year.
    shown = [q('cardSubMeta({kind: "album", date: "2003-03-07"})'), q('dateText("2019")'), q('dateText("")')]
    if shown != ["03-07-2003", "2019", ""]:
        failures.append(f"release dates read {shown}, wanted MM-DD-YYYY")
    if cards().get("v1", {}).get("w") != 356:
        failures.append(f"the video card is not 16:9 at the cover's height: {cards().get('v1')}")
    if not q(_VIDEO_LENGTH % "3:35"):
        failures.append("the video card does not show its length on the still")
    q('releasesFilter = "video"')
    if heads() != ["This week:1"] or order().get("This week") != ["v1"]:
        failures.append(f"Videos shows {heads()} {order()}, wanted the one video")
    if q("relSearchField.placeholderText") != "Search music videos\u2026":
        failures.append(f"the search under Videos reads {q('relSearchField.placeholderText')!r}")
    bridge._waves_prefs["releases_videos"] = "hide"
    before = loads["n"]
    bridge.releasesChanged.emit()
    if not pump(lambda: loads["n"] > before and "This week:2" in heads()):
        failures.append(f"hidden, the music video stayed in the feed: {heads()}")
    if q("releasesVideosOn") or q("releasesFilter") != "all":
        failures.append("hidden, the Videos chip stayed (or kept the filter)")

    # 6. The day rolls over: re-bucketed in place, no bridge call.
    held = loads["n"]
    q('todayIso = "2026-10-15"')
    want = ["Today:1", "This week:4", "This month:1", "September 2026:1", "August 2025:1"]
    if heads() != want:
        failures.append(f"after the rollover {heads()}, wanted {want}")
    q('todayIso = "2026-11-03"')
    want = ["October 2026:8", "September 2026:1", "August 2025:1"]
    if heads() != want:
        failures.append(f"after the month rollover {heads()}, wanted {want}")
    settle(300)
    if loads["n"] != held:
        failures.append("a rollover asked the bridge for the feed again")
    q('todayIso = "2026-10-14"')

    # 7. Back from another tab lands on Releases.
    q("openSearch()")
    if q("releasesOpen"):
        failures.append("Search left the Releases pane up")
    q("navBack()")
    if not (q("releasesOpen") and q("navOrigin") == "releases"):
        failures.append("Back from Search did not return to Releases")

    # 7b. A feed longer than the window reads three months, then the earlier
    # ones as it is scrolled to its end.
    bulk = [_rel(f"b{n:03d}", f"2026-{7 + n % 4:02d}-{1 + n % 28:02d}", "album") for n in range(120)]
    every = [*base, _rel("fresh", "2026-10-13", "single"), _rel("fresh2", "2026-10-14", "album"), *bulk]
    store.record_check("ar1", [*every, _rel("spring", "2026-03-03", "ep")])
    q("openSearch()")
    q("navBack()")
    if not pump(lambda: q("releasesMonths") == 3 and not q("releasesLoadingMore") and "This week" in " ".join(heads())):
        failures.append(f"the feed did not come back on its first page (months {q('releasesMonths')})")
    if not q("releasesHasMore") or any(h.startswith(("March 2026", "August 2025")) for h in heads()):
        failures.append(f"the first page read past July: {heads()} (more {q('releasesHasMore')})")
    if q("relFeedList.contentY") != q("relFeedList.originY"):
        failures.append("a new visit did not start at the top of the feed")
    # Rows built afresh at the top load their cards near the view only: a
    # new shell sits at y 0 until the column places it, and must not read as
    # near (which loaded every row's cards up to four views down).
    q("releasesModel.clear()")
    q("releasesApply()")
    settle(300)
    if not q("relFeedRows.itemAt(1).shown"):
        failures.append("the first card row on screen never loaded its cards")
    beyond = q(_FEED_ROW_BEYOND)
    if beyond < 0:
        failures.append("the feed is too short to check where cards load")
    elif q(f"relFeedRows.itemAt({beyond}).shown"):
        failures.append(f"row {beyond}, past the load distance, loaded its cards on arrival")
    q("relFeedList.contentY = relFeedList.originY")
    settle(100)
    # Upcoming never pages: every release dated ahead is on the first page.
    q('releasesFilter = "upcoming"')
    settle(200)
    q("releasesMaybeMore()")
    settle(300)
    if q("releasesMonths") != 3 or q("releasesLoadingMore"):
        failures.append(f"Upcoming paged through the window (months {q('releasesMonths')})")
    q('releasesFilter = "all"')
    settle(200)
    q("relFeedList.contentY = relFeedList.originY + relFeedList.contentHeight - relFeedList.height")
    if not pump(lambda: "August 2025:1" in heads() and not q("releasesHasMore"), 10000):
        failures.append(f"scrolling to the end never loaded the earlier months: {heads()}")
    if "March 2026:1" not in heads():
        failures.append(f"the earlier page skipped March: {heads()}")
    store.record_check("ar1", every)
    q("openReleases()")

    # 8. The FOLLOWING view lists the follows; unfollow drops at once. It
    # opens from the Artists headline beside Releases, and the green bar
    # under the headline moves to it.
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest

    if q("releasesTitle.text") != "Releases":
        failures.append(f"the feed is titled {q('releasesTitle.text')!r}")
    head = q(_HEADLINE % "manage")
    if not head:
        failures.append("there is no Artists headline beside Releases")
        q('releasesSetView("manage")')
    else:
        hx, hy = (int(v) for v in head.split(","))
        QTest.mouseClick(root, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(hx, hy))
        settle(400)
        if q("releasesView") != "manage":
            failures.append(f"clicking the Artists headline opened {q('releasesView')!r}")
        if q("rhBar.x") != q("releasesTitle.onItem.x") or q("releasesTitle.onItem.objectName") != "relHeadline_manage":
            failures.append("the headline bar did not move under Artists")
    if not pump(lambda: q("releasesFollowedHave") and q("releasesFollowedRows.length") == 1):
        failures.append("the FOLLOWING view never listed the followed artist")
    # The roster is the default shape. A reload (every sweep step sends one)
    # keeps it where the user scrolled it and keeps the cards it built.
    if q("releasesFollowView") != "roster" or not q("relFollowRoster.visible"):
        failures.append(f"the FOLLOWING view did not open as the roster ({q('releasesFollowView')})")
    for n in range(80):
        bridge._releases.follow(f"zz{n:03d}", f"Grid Artist {n:03d}", "")
    q("waves.loadFollowed()")
    if not pump(lambda: q("releasesFollowedModel.count") == 81):
        failures.append(f"the grid never listed 81 follows ({q('releasesFollowedModel.count')})")
    q("relFollowRoster.contentY = Math.min(240, relFollowRoster.contentHeight - relFollowRoster.height)")
    settle(100)
    held_y = q("relFollowRoster.contentY")
    first = q("relFollowRoster.indexAt(10, relFollowRoster.contentY + 20)")
    q(f"relFollowRoster.itemAtIndex({first}).objectName = 'gridCellKept'")
    q("waves.loadFollowed()")
    settle(400)
    if not held_y or q("relFollowRoster.contentY") != held_y:
        failures.append(f"a reload moved the roster ({held_y} -> {q('relFollowRoster.contentY')})")
    if (
        q(f"relFollowRoster.itemAtIndex({first}) ? relFollowRoster.itemAtIndex({first}).objectName : ''")
        != "gridCellKept"
    ):
        failures.append("a reload rebuilt the roster's cards")
    # Scrolled deep, left for the feed and come back to: the page keeps its
    # loaded pages and its place (as Browse's pages do), though the reload
    # finds the rows unchanged and sends nothing.
    q("releasesFollowedLimit = 1000")
    q("releasesFollowedSet(releasesFollowedRows)")
    settle(100)
    q("relFollowRoster.contentY = relFollowRoster.originY + 600")
    settle(100)
    kept_y = q("relFollowRoster.contentY")
    q('releasesSetView("feed")')
    settle(100)
    q('releasesSetView("manage")')
    settle(300)
    if q("releasesFollowedModel.count") != 81:
        failures.append(
            f"back on Artists from the feed, {q('releasesFollowedModel.count')} follows shown, wanted all 81 kept"
        )
    if q("relFollowRoster.contentY") != kept_y:
        failures.append(
            f"back on Artists from the feed, the page sits at {q('relFollowRoster.contentY')} (left at {kept_y})"
        )
    # Scrolled deep, off to another tab and back: the page comes back at its
    # top, never cut back to its first page under a deep scroll.
    settle(400)
    q(
        "relFollowRoster.contentY = relFollowRoster.originY + Math.max(0, relFollowRoster.contentHeight - relFollowRoster.height)"
    )
    settle(100)
    deep = q("relFollowRoster.contentY - relFollowRoster.originY")
    q("openSearch()")
    settle(100)
    q("navBack()")
    settle(300)
    if not deep or q("relFollowRoster.contentY") != q("relFollowRoster.originY"):
        failures.append(
            f"back on Artists from another tab, the page sits at {q('relFollowRoster.contentY')} (was {deep} deep)"
        )
    # A timeline entry's day with no today to compare: the plain date, and a
    # MusicBrainz year or month as known.
    dates = [
        q('relEntryDate({day: "2024-05-06", mb: true, shown: "2024-05-06"}, "")'),
        q('relEntryDate({day: "2024-01-01", mb: true, shown: "2024"}, "")'),
        q('relEntryDate({day: "2024-05-01", mb: true, shown: "2024-05"}, "2026-10-15")'),
    ]
    if dates != ["05-06-2024", "2024", "05-2024"]:
        failures.append(f"timeline entry dates read {dates}")
    # 8b. The view names itself; the corner switch shows the follows as
    # shelves or a ledger and is remembered; newest release first puts the
    # one artist with a release at the top and the cards say it; hovering a
    # timeline pin shows the release in the hint.
    if q("releasesTitle.text") != "Artists":
        failures.append(f"the FOLLOWING view is titled {q('releasesTitle.text')!r}")
    if not q("relViewSwitch.visible"):
        failures.append("the corner switch is not shown on the FOLLOWING view")
    for shape, view in (("shelves", "relFollowShelves"), ("ledger", "relFollowLedger")):
        q(f'releasesSetFollowView("{shape}")')
        settle(300)
        # A page of them at least, like the roster (more come as it is
        # scrolled, or at once when short rows leave the view unfilled).
        if not q(f"{view}.visible") or q("relFollowRoster.visible") or q(f"{view}.count") < 60:
            failures.append(f"the {shape} shape did not show a page of the follows ({q(f'{view}.count')})")
        if q('waves.wavesPref("releases_follow_view")') != shape:
            failures.append(f"the {shape} shape was not remembered")
    # The shelves: every release on a shelf that scrolls sideways, a wheel
    # turned sideways over it moves the shelf and one turned down moves the
    # list; the tiles end inside the card with room under the date, and the
    # library counts shrink to fit the head instead of crossing its rule.
    from PySide6.QtCore import QPointF
    from PySide6.QtGui import QGuiApplication, QWheelEvent

    def wheel(x: int, y: int, dx: int, dy: int) -> None:
        ev = QWheelEvent(
            QPointF(x, y),
            QPointF(root.mapToGlobal(QPoint(x, y))),
            QPoint(0, 0),
            QPoint(dx, dy),
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
            Qt.ScrollPhase.NoScrollPhase,
            False,
        )
        QGuiApplication.sendEvent(root, ev)

    q('releasesSetFollowView("shelves")')
    # Every release comes with the reload the switch asks for.
    pump(lambda: (json.loads(q(_SHELF_STRIP) or "null") or {}).get("count", 0) > 1, 4000)
    shelf = json.loads(q(_SHELF_STRIP) or "null")
    if not shelf:
        failures.append("the shelves drew no shelf of releases")
    else:
        if shelf["count"] != shelf["total"] or not shelf["scrolls"]:
            failures.append(f"the shelf does not scroll through every release ({shelf})")
        if shelf["room"] < 12:
            failures.append(f"a shelf tile ends {shelf['room']} px from its card's edge")
        # Sideways, the shelf eases along as Browse's do; read it at rest.
        wheel(shelf["x"], shelf["y"], -240, 0)
        settle(50)
        pump(lambda: not json.loads(q(_SHELF_STRIP))["moving"], 3000)
        moved = json.loads(q(_SHELF_STRIP))
        if moved["contentX"] <= shelf["contentX"] or moved["listY"] != shelf["listY"]:
            failures.append(f"a sideways wheel over the shelf did not scroll it ({shelf} -> {moved})")
        wheel(shelf["x"], shelf["y"], 0, -240)
        settle(100)
        down = json.loads(q(_SHELF_STRIP) or "null")
        if down and (down["listY"] <= moved["listY"] or down["contentX"] != moved["contentX"]):
            failures.append(f"a wheel turned down over the shelf did not scroll the list ({moved} -> {down})")
        q("relFollowShelves.contentY = relFollowShelves.originY")
        settle(100)
        q(_SHELF_BADGE_FIT % "set")
        settle(50)
        fit = q(_SHELF_BADGE_FIT % "read")
        if fit:
            failures.append(f"the shelf's library counts do not fit its head ({fit})")
        q(_SHELF_BADGE_FIT % "back")
    # The ledger: too narrow for the latest release and a readable timeline,
    # the latest release gives way and its date heads the column; a wide
    # window has room for all of it.
    q('releasesSetFollowView("ledger")')
    settle(300)
    heads = q(_LEDGER_HEADS)
    if (
        q("relFollowLedger.showLatest")
        or q("relFollowLedger.wAct") < 360
        or "LATEST RELEASE" in heads
        or "|LATEST|" not in heads
    ):
        failures.append(f"a narrow ledger did not give the latest release's room to the timeline ({heads})")
    none_yet = """(function() {
        function find(it) {
            if (it.text === "NONE YET" && it.visible) return true
            var kids = it.children || []
            for (var i = 0; i < kids.length; i++) if (find(kids[i])) return true
            return false
        }
        return find(relFollowLedger.contentItem)
    })()"""
    if not q(none_yet):
        failures.append("a narrow ledger says nothing for a follow with no release on record")
    root.setProperty("width", 1900)
    settle(300)
    heads = q(_LEDGER_HEADS)
    if (
        not q("relFollowLedger.showLatest")
        or q("relFollowLedger.wAct") < 360
        or "LATEST RELEASE" not in heads
        or "|WHEN|" not in heads
    ):
        failures.append(f"a wide ledger did not show the latest release and the timeline ({heads})")
    root.setProperty("width", 1280)
    settle(300)
    q('releasesSetFollowView("roster")')
    settle(200)
    # ar1's upcoming album (10/28) is the newest on record, then this single.
    store.record_check("zz005", [_rel("late5", "2026-10-12", "single")])
    q('releasesSetFollowSort("recent")')
    q("waves.loadFollowed()")
    if not pump(lambda: q("releasesFollowedModel.count") > 1 and q("releasesFollowedModel.get(1).uid") == "zz005"):
        failures.append("newest release first did not order the artists by their latest release")
    if q("releasesFollowedModel.get(0).uid") != "ar1":
        failures.append(f"the artist with an upcoming release is not first ({q('releasesFollowedModel.get(0).uid')})")
    settle(200)
    lines = [q(_CARD_LATEST % i) for i in (0, 1, 2)]
    if not lines[0].startswith("10-28-2026 | ALBUM | ") or not lines[1].startswith("10-12-2026 | SINGLE | "):
        failures.append(f"the roster cards do not say their latest release ({lines!r})")
    if lines[2] != "No release on record yet":
        failures.append(f"a follow with nothing on record reads {lines[2]!r}")
    # The ledger's timeline: a real hover over the newest pin of the first
    # row shows that release in the hint, and leaving hides it.
    q('releasesSetFollowView("ledger")')
    settle(400)
    pin = q(_LEDGER_PIN)
    if not pin:
        failures.append("the ledger drew no timeline pins")
    else:
        px, py, newest, newest_year = pin.split(",")
        px, py = int(px), int(py)
        if q(_LEDGER_END_WORD) != "NOW":
            failures.append(f"a resting timeline ends on {q(_LEDGER_END_WORD)!r}, not NOW")
        QTest.mouseMove(root, QPoint(px - 30, py))
        settle(50)
        QTest.mouseMove(root, QPoint(px, py))
        settle(300)
        if not q("relTip.visible") or q("relTipRows.length") < 1:
            failures.append("hovering a timeline pin showed no hint")
        elif not q(f"relTipRows.some(function (r) {{ return r.id === '{newest}' }})"):
            failures.append(f"the hint does not show the newest pin's release {newest!r}")
        if q(_LEDGER_END_WORD) != newest_year:
            failures.append(f"scrubbing the newest release reads {q(_LEDGER_END_WORD)!r}, not {newest_year}")
        QTest.mouseMove(root, QPoint(5, py))
        settle(200)
        if q("relTip.visible"):
            failures.append("the hint stayed up after the pointer left the timeline")
        if q(_LEDGER_END_WORD) != "NOW":
            failures.append("the timeline did not go back to NOW when the pointer left")
        # A busy period lists its albums first, then EPs, singles and
        # videos, each oldest first; the date line still spans the period.
        q(
            "relTipShow(relFollowLedger, 10, 10, 20, ["
            "{id: 's2', title: 'S2', day: '2024-08-03', rtype: 'single'},"
            "{id: 'v1', title: 'V1', day: '2024-08-03', rtype: 'single', kind: 'video'},"
            "{id: 'a2', title: 'A2', day: '2024-08-02', rtype: 'album'},"
            "{id: 's1', title: 'S1', day: '2024-08-01', rtype: 'single'},"
            "{id: 'e1', title: 'E1', day: '2024-08-02', rtype: 'ep'},"
            "{id: 'a1', title: 'A1', day: '2024-07-30', rtype: 'album'}])"
        )
        order = q("relTipRows.map(function (r) { return r.id }).join(',')")
        if order != "a1,a2,e1,s1,s2,v1":
            failures.append(f"the hint lists {order}, wanted albums, EPs, singles, videos, each oldest first")
        date_line = q(
            "(function () { var hit = ''; (function walk(o) { if (o.objectName === 'relTipDate') hit = o.text;"
            " var k = o.children || []; for (var i = 0; i < k.length; i++) walk(k[i]) })(relTip); return hit })()"
        )
        if not str(date_line).startswith("07-30-2024 \u2013 08-03-2024"):
            failures.append(f"the hint's date line reads {date_line!r}, wanted the period oldest to newest")
        # Each kind sits under a strip naming it with its count; the strip
        # of the group in view stays pinned at the top while the list scrolls.
        groups = q("relTipGroups.map(function (g) { return g.group + ':' + g.n }).join(',')")
        if groups != "album:2,ep:1,single:2,video:1":
            failures.append(f"the hint's groups read {groups!r}")
        settle(100)
        if q("relTipList.count") != 10:
            failures.append(f"the hint lists {q('relTipList.count')} items, wanted 6 releases under 4 strips")
        first_strip = q(_TIP_STRIP % "relTipList.itemAtIndex(0)")
        if first_strip != "ALBUMS  ·  2":
            failures.append(f"the hint's first strip reads {first_strip!r}")
        if q(_TIP_STRIP % "relTipPinned") is not False:
            failures.append("the pinned strip shows before the list has scrolled")
        q("relTipList.contentY = relTipList.originY + 2 * (relTipHeadH + 8) + 3 * (relTipRowH + 8) + 6")
        settle(100)
        pinned = q(_TIP_STRIP % "relTipPinned")
        if pinned != "SINGLES  ·  2":
            failures.append(f"scrolled into the singles, the pinned strip reads {pinned!r}")
        # At the handover the pinned strip lies exactly over the one in the
        # list: the label neither steps up nor sideways.
        q("relTipList.contentY = relTipList.originY + 4 + (relTipHeadH + 8) + 2 * (relTipRowH + 8)")
        settle(100)
        inline_at = q(_TIP_STRIP_AT % "relTipList.itemAtIndex(3)")
        pinned_at = q(_TIP_STRIP_AT % "relTipPinned")
        if not inline_at or inline_at != pinned_at:
            failures.append(f"at the handover the pinned label sits at {pinned_at!r}, the list's at {inline_at!r}")
        # The next strip pushes the pinned one up as it comes, and takes
        # over only once it reaches the top itself, so no label jumps.
        q("relTipList.contentY = relTipList.originY + 4 + 2 * (relTipHeadH + 8) + 3 * (relTipRowH + 8) - 10")
        settle(100)
        pushed = q("relTipPinned.y")
        if q(_TIP_STRIP % "relTipPinned") != "EPS  ·  1" or pushed != 10 - q("relTipHeadH"):
            failures.append(
                f"10 px above the singles, the pinned strip reads {q(_TIP_STRIP % 'relTipPinned')!r} at y {pushed}"
            )
        q(
            "relTipShow(relFollowLedger, 10, 10, 20, [{id: 's1', day: '2024-08-01', rtype: 'single'},"
            " {id: 's2', day: '2024-08-02', rtype: 'single'}])"
        )
        settle(100)
        if q("relTipList.count") != 2 or q("relTipList.heads"):
            failures.append("a period of one kind should list its releases with no strips")
        # A hint too short to scroll still keeps the wheel off the page.
        q(
            "relTipShow(relFollowLedger, 200, 400, 410, [{id: 's1', day: '2024-08-01', rtype: 'single'},"
            " {id: 's2', day: '2024-08-02', rtype: 'single'}])"
        )
        settle(100)
        page_y = q("relFollowLedger.contentY")
        if q("relFollowLedger.contentHeight <= relFollowLedger.height"):
            failures.append("the ledger is too short to tell a wheel leaking past the hint")
        hint = q(
            "(function () { var p = relTipBox.mapToItem(null, 30, 12); return Math.round(p.x) + ',' + Math.round(p.y) })()"
        )
        hx, hy = (int(v) for v in hint.split(","))
        wheel(hx, hy, 0, -240)
        settle(150)
        if q("relTipList.scrolls") or q("relFollowLedger.contentY") != page_y:
            failures.append(
                f"a wheel over a hint that cannot scroll scrolled the page to {q('relFollowLedger.contentY')}"
            )
        q("relTipHide()")
        # The warm pool holds covers by the memory they take: a long run of
        # small ones (the Artists list's 88 px) keeps its first, and big ones
        # stay inside the budget.
        q("for (var i = 0; i < 1200; i++) warmArt('qrc:/probe/small' + i + '.png', 88, 88)")
        if not q("_warmSeen['qrc:/probe/small0.png@88'] === true"):
            failures.append("a run of small covers pushed the first out of the warm pool")
        q("for (var i = 0; i < 400; i++) warmArt('qrc:/probe/big' + i + '.png', 960, 960)")
        if not q("typeof warmArtBudget === 'number' && _warmBytes <= warmArtBudget"):
            failures.append("the warm pool is not held to a memory budget")
        if q("_warmSeen['qrc:/probe/big399.png@960'] !== true"):
            failures.append("the newest big cover is not in the warm pool")
        edge = q(_LEDGER_LEFT_EDGE)
        if edge:
            failures.append(f"a timeline's left edge is not its carved first year ({edge})")
        # A release in the hint opens with a real click: the pointer goes
        # from the pin up onto the hint and clicks the release there.
        QTest.mouseMove(root, QPoint(px - 30, py))
        settle(50)
        QTest.mouseMove(root, QPoint(px, py))
        settle(300)
        tip_row = q(_TIP_ROW)
        first_id = q("relTipRows.length ? relTipRows[0].id : ''")
        if not tip_row:
            failures.append("the hint offered no release to pick")
        else:
            tx, ty = (int(v) for v in tip_row.split(","))
            for yy in range(py, ty, -4 if ty < py else 4):
                QTest.mouseMove(root, QPoint(px, yy))
                settle(16)
            # Off the timeline and onto its hint (its date line, then the
            # release), the timeline keeps the year the pointer left from.
            hint = q(
                "(function () { var p = relTipBox.mapToItem(null, 30, 12); return Math.round(p.x) + ',' + Math.round(p.y) })()"
            )
            QTest.mouseMove(root, QPoint(*(int(v) for v in hint.split(","))))
            settle(100)
            if q(_LEDGER_END_WORD) != newest_year:
                failures.append(
                    f"on the hint's date line the timeline reads {q(_LEDGER_END_WORD)!r}, not {newest_year}"
                )
            QTest.mouseMove(root, QPoint(tx, ty))
            settle(200)
            if q(_LEDGER_END_WORD) != newest_year:
                failures.append(
                    f"on a release in the hint the timeline reads {q(_LEDGER_END_WORD)!r}, not {newest_year}"
                )
            QTest.mouseClick(root, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(tx, ty))
            settle(300)
            if q("releasesOpen") or q("browsePageKey") != f"item:album:{first_id}":
                failures.append(f"clicking the release in the hint did not open it ({q('browsePageKey')!r})")
            q("openReleases()")
            settle(200)
            if q("releasesView") != "manage":
                q('releasesSetView("manage")')
            settle(300)
    # The MusicBrainz marks: with the setting on, a release group TIDAL
    # lacks is a red dot on the ledger's timeline, its hint names it with a
    # NOT ON TIDAL chip, no cover and nothing to click; off, the marks go.
    bridge._waves_prefs["releases_musicbrainz"] = True
    store.record_mb(
        "ar1",
        "mbid-ar1",
        [{"rgid": "g-lost", "title": "Lost One", "date": "2024-03-03", "rtype": "album", "release_id": ""}],
    )
    q('releasesSetFollowView("ledger")')
    q("waves.loadFollowed()")
    if not pump(lambda: q(_LEDGER_MB_MARK) != ""):
        failures.append("the ledger timeline drew no mark for the release TIDAL lacks")
    else:
        mx, my, red = (int(v) for v in q(_LEDGER_MB_MARK).split(","))
        if not red:
            failures.append("the mark for a release TIDAL lacks is not red")
        QTest.mouseMove(root, QPoint(mx - 30, my))
        settle(50)
        QTest.mouseMove(root, QPoint(mx, my))
        settle(300)
        tip = json.loads(q(_TIP_MB))
        if not tip["visible"] or not q(
            "relTipRows.some(function (r) { return r.mb === true && r.title === 'Lost One' })"
        ):
            failures.append("hovering the mark did not list the release TIDAL lacks in the hint")
        elif not tip["chip"] or tip["hand"]:
            failures.append(f"the hint's row for a release TIDAL lacks is wrong ({tip})")
        QTest.mouseMove(root, QPoint(5, my))
        settle(200)
    bridge._waves_prefs["releases_musicbrainz"] = False
    q("waves.loadFollowed()")
    if not pump(lambda: q(_LEDGER_MB_COUNT) == 0):
        failures.append("switching MusicBrainz off left the marks on the timeline")
    long_career = json.loads(q(_LEDGER_LONG_CAREER) or "null")
    if not long_career or long_career["uncovered"] or long_career["overlap"]:
        failures.append(f"a long career on a narrow timeline hides releases from the hint ({long_career})")
    elif not long_career["busyRed"] or not long_career["aheadInGrid"]:
        failures.append(f"the busy year or the bar still to come is drawn wrong ({long_career})")
    q('releasesSetFollowView("roster")')
    # Back to by name: the kept rows are re-sorted at once, not left newest
    # first until the next reload.
    q('releasesSetFollowSort("name")')
    settle(50)
    if not q(_NAMES_IN_ORDER):
        failures.append("switching back to by name kept the newest-first order")
    q("waves.loadFollowed()")
    pump(lambda: q("releasesFollowedModel.get(0).uid") != "zz005")
    q('releasesSetView("excluded")')
    bridge.excludedLoaded.emit([{"name": "Thin One", "key": "thin one", "albums": 1, "tracks": 2}])
    settle(200)
    if q("releasesTitle.text") != "Excluded artists" or q("relExcludedRoster.count") != 1:
        failures.append(f"the EXCLUDED page did not list the artist under the floor ({q('relExcludedRoster.count')})")
    # The shelves are a letter head and a row of tiles, each its own delegate.
    for shape, view, rows in (("shelves", "relExcludedShelves", 2), ("ledger", "relExcludedLedger", 1)):
        q(f'releasesSetFollowView("{shape}")')
        settle(200)
        if not q(f"{view}.visible") or q(f"{view}.count") != rows or q("relExcludedRoster.visible"):
            failures.append(f"the EXCLUDED page did not take the {shape} shape ({q(f'{view}.count')})")
    q('releasesSetFollowView("roster")')
    # A reload (a FOLLOW landing, the floor moving) keeps the list where the
    # user scrolled it, and the last row can scroll clear of the corner switch.
    thin = [{"name": f"Thin {n:03d}", "key": f"thin {n:03d}", "albums": 1, "tracks": 2} for n in range(60)]
    bridge.excludedLoaded.emit(thin)
    settle(200)
    q("relExcludedRoster.contentY = Math.min(300, relExcludedRoster.contentHeight - relExcludedRoster.height)")
    settle(100)
    held_y = q("relExcludedRoster.contentY")
    bridge.excludedLoaded.emit(thin[:3] + thin[4:])
    settle(200)
    if q("relExcludedRoster.count") != 59 or not held_y or q("relExcludedRoster.contentY") != held_y:
        failures.append(f"a reload moved the EXCLUDED list ({held_y} -> {q('relExcludedRoster.contentY')})")
    if q("relExcludedRoster.footerItem.height") < q("relViewSwitch.height") + 14:
        failures.append("the EXCLUDED list cannot scroll its last row clear of the corner switch")
    q('importDialog.startNames(["Thin One"], true)')
    if not q("importDialog.open") or not q("importDialog.manualPicks"):
        failures.append("FOLLOW on the EXCLUDED page did not open the review for the name")
    q("importDialog.discard()")
    # The tray holds library runs' names: picked there, they are library follows.
    q("importDialog.reviewTray()")
    if q("importDialog.manualPicks"):
        failures.append("the tray review kept the EXCLUDED page's hand-follow origin")
    q("importDialog.discard()")
    q('releasesSetView("manage")')
    settle(100)

    # A long list shows a page at a time: enough pages to fill the view,
    # then one more each time it is scrolled near its end.
    for n in range(80, 280):
        bridge._releases.follow(f"zz{n:03d}", f"Grid Artist {n:03d}", "")
    q("waves.loadFollowed()")
    if not pump(lambda: 0 < q("releasesFollowedModel.count") < 281 and not q("relFollowMoreCheck.running")):
        failures.append(f"the roster did not page a long follow list ({q('releasesFollowedModel.count')} of 281)")
    shown = q("releasesFollowedModel.count")
    if shown % 60 != 0:
        failures.append(f"the roster showed {shown} follows, not whole pages")
    for _ in range(8):
        q("relFollowRoster.contentY = relFollowRoster.originY + relFollowRoster.contentHeight - relFollowRoster.height")
        settle(150)
    if q("releasesFollowedModel.count") != 281:
        failures.append(f"scrolling to the end never showed every follow ({q('releasesFollowedModel.count')})")
    for n in range(280):
        bridge._releases.unfollow(f"zz{n:03d}")
    q("waves.loadFollowed()")
    pump(lambda: q("releasesFollowedModel.count") == 1)
    bridge.unfollowArtist("ar1")
    if q("releasesFollowedRows.length") != 0 or q("releasesFollowedModel.count") != 0:
        failures.append("an unfollowed artist stayed in the FOLLOWING view")
    q('releasesSetView("feed")')

    # 9. The artist page's toggle follows the store.
    q('artistData = { id: "ar7", name: "Seven", art: "" }')
    if q("artistFollowed"):
        failures.append("an artist nobody follows reads FOLLOWING")
    bridge.followArtist("ar7", "Seven", "")
    if not q("artistFollowed"):
        failures.append("following from the artist page did not flip the toggle")
    # Its TIDAL side: a queued change reads as pending, a landed write as a
    # favorite (the ALSO FOLLOW ON TIDAL button keys on both).
    if q("artistTidalFav") or q("artistTidalPending"):
        failures.append("a fresh follow read as a TIDAL favorite or a pending change")
    bridge._rel_tidal_out = {"ar7": True}
    q("artistTidalRead()")
    if not q("artistTidalPending"):
        failures.append("a queued favorites change did not read as pending")
    bridge._rel_tidal_out = {}
    bridge._releases.mark_tidal_fav("ar7", True)
    bridge.followTidalChanged.emit("ar7", True)
    if not q("artistTidalFav") or q("artistTidalPending"):
        failures.append("a landed favorites write did not settle the artist page")
    bridge.unfollowArtist("ar7")
    if q("artistFollowed"):
        failures.append("unfollowing did not flip the toggle back")

    # 10. The follow dialog: only the answer to the text in the field lands,
    # a row's FOLLOW follows and flips in place, a row click opens the artist.
    q("followDialog.show()")
    if not q("followDialog.open") or q("followDialog.rows.length") != 0:
        failures.append("the follow dialog did not open empty")
    q('followField.text = "seven"')
    # Signed out in this harness, the bridge answers at once with a failure.
    if not pump(lambda: q("followDialog.failed") and not q("followDialog.busy")):
        failures.append("a failed artist search did not say so")
    rows = [{"id": "ar7", "name": "Seven", "art": ""}, {"id": "ar8", "name": "Seventy", "art": ""}]
    bridge.artistSearchResults.emit("sev", rows[:1], True)
    if q("followDialog.rows.length") != 0:
        failures.append("an answer to an older needle replaced the rows")
    bridge.artistSearchResults.emit("seven", rows, True)
    if not pump(lambda: q("followList.count") == 2 and q("followList.itemAtIndex(0) !== null")):
        failures.append("the follow dialog did not list the answer")
    elif q("followList.itemAtIndex(0).on"):
        failures.append("an artist nobody follows reads FOLLOWING in the dialog")
    else:
        q(_CLICK_ROW_BTN)
        if not bridge.isFollowed("ar7"):
            failures.append("FOLLOW in the dialog did not follow")
        elif not q("followList.itemAtIndex(0).on") or q("followList.itemAtIndex(1).on"):
            failures.append("the dialog row did not flip to FOLLOWING (or flipped its neighbor)")
        q(_CLICK_ROW_BTN)
        if bridge.isFollowed("ar7") or q("followList.itemAtIndex(0).on"):
            failures.append("FOLLOWING in the dialog did not unfollow")
    q('followDialog.openArtist("ar8")')
    if q("followDialog.open"):
        failures.append("opening an artist from the dialog left it open")
    q("followDialog.show()")
    if q("followField.text") != "" or q("followDialog.needle") != "":
        failures.append("the follow dialog reopened with the last search")
    q("followDialog.close()")
    # The same name searched again (SEARCH twice from the review) asks again.
    q('followDialog.show("Same Name")')
    q("followDialog.close()")
    q('followDialog.show("Same Name")')
    if not q("followDebounce.running") and not q("followDialog.busy"):
        failures.append("showing the follow dialog with the same name did not search")
    q("followDialog.close()")
    # A follow made behind the artist page's back (the favorites walk) reaches its toggle.
    q('artistData = { id: "ar9", name: "Nine", art: "" }')
    bridge._releases.sync_tidal_favorites([{"id": "ar9", "name": "Nine"}], partial=True)
    bridge.releasesChanged.emit()
    if not pump(lambda: q("artistFollowed")):
        failures.append("the artist page toggle missed a follow made by the favorites walk")

    # 11. The library import: the review lists what the run found, matches
    # ticked, close names picked by hand, a parked review survives a search,
    # and confirming follows the picks without a single TIDAL write.
    q("importDialog.show()")
    if q("importDialog.stage") != "start" or not q("importDialog.open"):
        failures.append("the import dialog did not open on its start screen")
    q("importDialog.start()")
    # Signed out in this harness, the bridge refuses at once.
    if q("importDialog.stage") != "error" or q("importDialog.error") != "not-signed-in":
        failures.append("a signed-out import did not say so")
    q("importDialog.close()")
    q("importDialog.show()")
    q('importDialog.stage = "scan"')
    bridge.artistImportProgress.emit(3, 10, "scan")
    if q("importDialog.done") != 3 or q("importDialog.total") != 10:
        failures.append("the import progress did not reach the dialog")
    payload = {
        "matched": [
            {
                "name": "Caribou",
                "norm": "caribou",
                "albums": 2,
                "reason": "tag",
                "artist": {"id": "im1", "name": "Caribou", "art": ""},
            },
            {
                "name": "Nils Frahm",
                "norm": "nils frahm",
                "albums": 1,
                "reason": "",
                "artist": {"id": "im2", "name": "Nils Frahm", "art": ""},
            },
        ],
        "fuzzy": [
            {
                "name": "Bleachers",
                "norm": "bleachers",
                "albums": 1,
                "candidates": [
                    {
                        "id": "im3",
                        "name": "Bleachers",
                        "art": "",
                        "conf": 100,
                        "owned": ["Hollow", "Nightfall"],
                        "owned_n": 3,
                        "checked": True,
                    },
                    {"id": "im4", "name": "Bleacher", "art": ""},
                ],
            },
        ],
        "unmatched": [{"name": "Nobody Here", "norm": "nobody here", "albums": 1}],
        "skipped": 0,
        "tagged": 1,
    }
    bridge._releases.tray_record([{"norm": "nobody here", "name": "Nobody Here", "reason": "unmatched"}])
    bridge.artistImportReady.emit(payload)
    if q("importDialog.stage") != "review" or q("importDialog.rows.length") != 7:
        failures.append(f"the review did not list the run (rows {q('importDialog.rows.length')})")
    # Under each close name, what the library says: the score and the
    # titles of yours it has; a candidate from before the library weighed in
    # (or one it could not read) says so.
    if pump(lambda: q("importList.itemAtIndex(4) !== null")):
        whys = json.loads(q(_CANDIDATE_WHYS))
        if whys != ["100% \u00b7 Hollow, Nightfall +1", "Not checked"]:
            failures.append(f"the close names read {whys}")
    else:
        failures.append("the close-names row never rendered")
    if q("importDialog.pickCount") != 2:
        failures.append("the matches did not start ticked")
    q('importDialog.setPick("bleachers", importDialog.rows[4].candidates[0])')
    if q("importDialog.pickCount") != 3:
        failures.append("picking a close name did not count")
    q("importDialog.setAllMatched(false)")
    if q("importDialog.pickCount") != 1:
        failures.append("NONE did not untick the matches (or touched the hand pick)")
    if q("importDialog.matchPickCount") != 0:
        failures.append("ALL / NONE counted the hand pick as a ticked match")
    q("importDialog.setAllMatched(true)")
    q('importDialog.drop("nobody here")')
    if q("importDialog.rows.length") != 5 or "nobody here" not in bridge._releases.tray_dismissed_norms():
        failures.append("FORGET did not drop the name and its empty section")
    q('importDialog.park("Bleachers")')
    if q("importDialog.open") or not q("followDialog.open") or q("followField.text") != "Bleachers":
        failures.append("SEARCH did not hand the name to the follow dialog")
    q("followDialog.close()")
    if not q("importDialog.open") or q("importDialog.pickCount") != 3:
        failures.append("the parked review did not come back as it was left")
    # The review is left open: the tab's IMPORT brings it back rather than
    # starting a run over it.
    q("importDialog.close()")
    q("importDialog.startMore()")
    if q("importDialog.stage") != "review" or q("importDialog.pickCount") != 3:
        failures.append("IMPORT replaced a review the user had left open")
    # Opening an artist from that search shows the artist, not the review;
    # the review waits and comes back from the pane as it was left.
    q('importDialog.park("Bleachers")')
    q('followDialog.openArtist("im3")')
    if q("importDialog.open") or q("followDialog.open"):
        failures.append("opening an artist from SEARCH brought the review back over it")
    q("importDialog.show()")
    if not q("importDialog.open") or q("importDialog.pickCount") != 3:
        failures.append("the review was lost after opening an artist from SEARCH")
    q("importDialog.confirm()")
    if q("importDialog.stage") != "done" or q("importDialog.followedNow") != 3:
        failures.append("confirming the review did not follow the picks")
    if not all(bridge.isFollowed(a) for a in ("im1", "im2", "im3")) or bridge.isFollowed("im4"):
        failures.append("the wrong artists were followed")
    if bridge._rel_tidal_out or bridge._rel_tidal_inflight:
        failures.append("a library import queued a TIDAL favorites write")
    if q("releasesFollowed") != bridge.followedCount():
        failures.append("the followed count did not move with the import")
    q("importDialog.close()")
    q("importDialog.show()")
    if q("importDialog.stage") != "start":
        failures.append("the import dialog did not start over after a confirm")
    # FORGET in a long review keeps the list where the user scrolled it.
    big = [{"name": f"Name {i:02d}", "norm": f"name {i:02d}", "albums": 1} for i in range(40)]
    q('importDialog.stage = "scan"')
    bridge.artistImportReady.emit({"matched": [], "fuzzy": [], "unmatched": big, "skipped": 0, "tagged": 0})
    settle(200)
    q("importList.contentY = Math.min(300, importList.contentHeight - importList.height)")
    settle(100)
    held_y = q("importList.contentY")
    first = q("importList.indexAt(10, importList.contentY + 10)")
    q(f"importList.itemAtIndex({first}).objectName = 'reviewRowKept'")
    q('importDialog.drop("name 39")')
    settle(200)
    if not held_y or q("importList.contentY") != held_y:
        failures.append(f"FORGET moved the review ({held_y} -> {q('importList.contentY')})")
    if q(f"importList.itemAtIndex({first}) ? importList.itemAtIndex({first}).objectName : ''") != "reviewRowKept":
        failures.append("FORGET rebuilt the review's rows")
    if q("importDialog.rows[0].n") != 39:
        failures.append(f"the NOT FOUND header still counts {q('importDialog.rows[0].n')} after a FORGET")
    q("importDialog.discard()")

    # 1c. Being on the pane marks arrivals seen only while the user is
    # LOOKING (window up and focused): a Waves parked on the Releases pane
    # behind another app's window must not eat the badge, and focus coming
    # back acknowledges what it counted (user report 2026-10-26).
    q("openReleases()")
    settle(300)
    q("releasesCounts()")
    if q("releasesUnseen") != 0:
        print(f"the badge gate started dirty ({q('releasesUnseen')} unseen)", file=sys.stderr)
        return _EXIT_PRECONDITION
    store.follow("eyes", "Eyes Artist", "")
    eyes_base = [_rel("eyes0", "2026-09-01", "album")]
    store.record_check("eyes", eyes_base)
    q("releasesRefetch.interval = 30")
    q("looking = false")
    fresh_eyes = store.record_check("eyes", [*eyes_base, _rel("eyes1", "2026-10-11", "single")])
    if [r["id"] for r in fresh_eyes] != ["eyes1"]:
        print(f"the store did not report the eyes release: {fresh_eyes}", file=sys.stderr)
        return _EXIT_PRECONDITION
    bridge.releasesChanged.emit()
    settle(400)
    if q("releasesUnseen") != 1:
        failures.append(f"an arrival with nobody looking read {q('releasesUnseen')} on the badge, wanted 1")
    q("looking = true")
    settle(400)
    if q("releasesUnseen") != 0:
        failures.append(f"focus coming back left the badge at {q('releasesUnseen')}")

    if failures:
        print("\n".join(failures), file=sys.stderr)
        return _EXIT_REGRESSED
    print("ok")
    return _EXIT_OK


if __name__ == "__main__" and "--run-scenario" in sys.argv:
    sys.exit(_run_scenario())
