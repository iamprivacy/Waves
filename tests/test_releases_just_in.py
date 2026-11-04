"""The Releases feed's arrivals, on screen: Just in, and how they are seen.

What arrived since the user last looked sits in its own section above the
dated ones, on a tinted panel, each card framed in green with JUST IN over its
title. The scenario proves:

- the arrivals leave their date sections for Just in, whose head says since
  when they came ("2 arrived since 10-12-2026");
- every section runs newest first: by release day, and on one day the
  latest found first;
- the mono line over a title sets its parts apart (JUST IN · EP · E), and
  the date sits on its right;
- each row is as tall as its tallest card's words, worked out before the
  cards are built: long titles and long artist lists wrap, up to a cap (a
  title stops at three lines with an ellipsis, the credits at two with
  "+N more"), so a name that runs to a sentence or two cannot stretch its
  row, every cover in a row starts on one line, and hovering a cut title
  shows it in full;
- with the default ("seen"), arrivals stay across visits until opened or
  MARK AS SEEN is pressed; opening one marks it alone;
- set to go after a visit, they are gone once the pane is left.

Runs in a SUBPROCESS like the other Main.qml scenarios: building the bridge
installs process-global handlers that must not leak into the suite.
"""

from __future__ import annotations

import datetime
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


def test_arrivals_sit_under_just_in_until_seen() -> None:
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    sandbox = tempfile.mkdtemp(prefix="waves-releases-just-in-")
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
    assert proc.returncode == _EXIT_OK, f"the feed's arrivals are wrong:\n{tail}"


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

# Every rendered release card: its mono line, its date, its frame, its
# words' height, and its row's measured words height.
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
            var k = find(it, "releaseKick"), d = find(it, "releaseDate"), f = find(it, "releaseFrame")
            var words = []
            for (var w = 0; w < (k ? k.children.length : 0); w++) {
                var row = k.children[w]
                for (var c = 0; c < (row.children || []).length; c++) {
                    var t = row.children[c]
                    if (t.visible && typeof t.text === "string") words.push(t.text)
                }
            }
            var r = it.parent
            while (r && r.th === undefined) r = r.parent
            var txt = find(it, "releaseTitle"), fl = find(it, "artistFlow"), more = find(it, "artistFlowMore")
            out["" + it.card.id] = {
                top: Math.round(it.parent.mapToItem(null, 0, 0).y), cut: txt ? txt.truncated : false,
                flowH: fl ? fl.height : -1, more: more && more.visible ? more.text : "",
                kick: words.join(""), date: d ? d.text : "", dateRight: d ? Math.round(d.x + d.width) : -1,
                width: Math.round(it.width), textW: txt ? Math.round(txt.parent.width) : -1,
                frame: !!(f && f.visible), textH: txt ? txt.parent.height : -1, th: r ? r.th : -1,
                row: r ? r.uid : "", titleLines: txt ? txt.lineCount : 0
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

# The Just in head: its count line, and whether MARK AS SEEN shows.
_JUST_IN_HEAD = """
JSON.stringify((function() {
    function find(it, name) {
        if (!it) return null
        if (it.objectName === name) return it
        var kids = it.children || []
        for (var i = 0; i < kids.length; i++) { var f = find(kids[i], name); if (f) return f }
        return null
    }
    for (var i = 0; i < relFeedRows.count; i++) {
        var row = relFeedRows.itemAt(i)
        if (!row || row.uid !== "h:new") continue
        var sec = find(row, "releasesSection"), btn = find(row, "relSeenBtn"), panel = relJustInPanel
        var count = ""
        for (var k = 0; k < sec.children.length; k++)
            if (sec.children[k].text !== undefined && ("" + sec.children[k].text).indexOf("arrived") >= 0) count = sec.children[k].text
        // The panel and every green frame lie wholly inside the feed's
        // clip (nothing cut at the sides), the panel runs from the head to
        // the section's last row, and each frame fills the cover it rides
        // on, cut to its corner (no seam, and the art never shows past it).
        var last = null
        for (var j = i + 1; j < relFeedRows.count; j++) {
            var it = relFeedRows.itemAt(j)
            if (!it || it.rowKind !== "row") break
            last = it
        }
        var fits = [], p = panel.mapToItem(relFeedClip, 0, 0)
        if (p.x < 0 || p.x + panel.width > relFeedClip.width) fits.push("panel x " + p.x + " w " + panel.width)
        var hb = row.mapToItem(relFeedClip, 0, row.height).y, lb = last ? last.mapToItem(relFeedClip, 0, last.height).y : -1
        if (p.y >= hb || p.y + panel.height < lb - 8) fits.push("panel y " + p.y + " h " + panel.height + " head " + hb + " last " + lb)
        function frames(it) {
            if (!it) return
            if (it.objectName === "releaseFrame" && it.visible) {
                var f = it.mapToItem(relFeedClip, 0, 0)
                if (f.x < 0 || f.x + it.width > relFeedClip.width) fits.push("frame x " + f.x)
                if (it.x !== 0 || it.y !== 0 || it.width !== it.parent.width || it.height !== it.parent.height
                        || it.radius !== it.parent.faceRadius)
                    fits.push("frame " + it.width + " on art " + it.parent.width)
            }
            var kids = it.children || []
            for (var k = 0; k < kids.length; k++) frames(kids[k])
        }
        for (var j = i + 1; last && j < relFeedRows.count && relFeedRows.itemAt(j) !== last; j++) frames(relFeedRows.itemAt(j))
        frames(last)
        return { count: count, button: !!(btn && btn.visible), panel: !!(panel && panel.visible), fits: fits }
    }
    return null
})())
"""

# Press MARK AS SEEN.
_PRESS_SEEN = """(function() {
    function find(it, name) {
        if (!it) return null
        if (it.objectName === name) return it
        var kids = it.children || []
        for (var i = 0; i < kids.length; i++) { var f = find(kids[i], name); if (f) return f }
        return null
    }
    for (var i = 0; i < relFeedRows.count; i++) {
        var row = relFeedRows.itemAt(i)
        if (row && row.uid === "h:new") { find(row, "relSeenBtn").clicked(); return true }
    }
    return false
})()"""

_LONG = "An Unusually Long Release Title That Has To Wrap Over Several Lines Under Its Cover"
_ABSURD = (
    "This Release Title Is Not A Title At All But Two Whole Sentences Of Liner Notes. "
    "It Goes On About The Studio, The Weather And The Band's Dog For Far Too Long"
)


def _rel(rid: str, day: str, rtype: str, *, explicit: bool = False, title: str = "", artists=None) -> dict:
    artists = artists or [{"id": "ar1", "name": "Artist One"}]
    return {
        "kind": "album",
        "id": rid,
        "title": title or f"Release {rid}",
        "artist": artists[0]["name"],
        "artist_id": "ar1",
        "artists": artists,
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

    from waves.waves_ui import bridge_releases

    class _Day(datetime.date):
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

    def head() -> dict | None:
        return json.loads(q(_JUST_IN_HEAD))

    failures: list[str] = []
    settle()
    q(PARK_LOGIN_QML)
    q("bootOverlay.done = true")
    q("bootContentShown = 1")
    q("newCutoffClock.running = false")
    q('todayIso = "2026-10-14"')

    store = bridge._releases
    bridge._waves_prefs["releases_window_months"] = "120"
    store.follow("ar1", "Artist One", "")
    many = [{"id": f"ar{n}", "name": f"A Remarkably Long Artist Name {n}"} for n in range(1, 5)]
    crowd = [{"id": f"cr{n}", "name": f"The Featured Ensemble Of Session Player {n}"} for n in range(1, 13)]
    base = [
        _rel("today", "2026-10-14", "single"),
        _rel("w2", "2026-10-11", "album"),
        _rel("week", "2026-10-10", "album", title=_LONG, artists=many),
        _rel("absurd", "2026-10-09", "album", title=_ABSURD, artists=crowd),
    ]
    store.record_check("ar1", base)
    store.record_check(
        "ar1",
        [*base, _rel("n1", "2026-10-12", "single"), _rel("n2", "2026-10-11", "ep", explicit=True)],
    )
    # The first catalog was found an hour before n2, and n2 an hour before
    # n1: on one day (w2 and n2 share 10-11) the later find leads.
    t0 = int(datetime.datetime(2026, 10, 12, 12, 0).timestamp())
    with store._lock:
        store._conn.execute("UPDATE releases SET detected_at = ?", (t0 - 3600,))
        store._conn.execute("UPDATE releases SET detected_at = ? WHERE release_id = 'n2'", (t0,))
        store._conn.execute("UPDATE releases SET detected_at = ? WHERE release_id = 'n1'", (t0 + 3600,))
        store._conn.commit()

    q("openReleases()")
    if not pump(lambda: "Just in:2" in heads() and not q("releasesLoadingMore")):
        print(f"the feed never showed the arrivals: {heads()}", file=sys.stderr)
        return _EXIT_PRECONDITION
    settle(400)

    # 1. The arrivals leave their sections for Just in, newest first.
    want = ["Just in:2", "Today:1", "This week:3"]
    if heads() != want:
        failures.append(f"sections {heads()}, wanted {want}")
    if order().get("Just in") != ["n1", "n2"]:
        failures.append(f"Just in runs {order().get('Just in')}, wanted newest first")
    h = head()
    if not h or h["count"] != "2 arrived since 10-12-2026" or not h["button"] or not h["panel"]:
        failures.append(f"the Just in head reads {h}")
    if h and h["fits"]:
        failures.append(f"the Just in panel or frames spill out: {h['fits']}")

    # 2. The mono line sets its parts apart, the date sits right, the frame
    # marks an arrival only.
    c = cards()
    expect = {"n2": "JUST IN · EP · E", "n1": "JUST IN · SINGLE", "today": "SINGLE"}
    for rid, kick in expect.items():
        if c.get(rid, {}).get("kick") != kick:
            failures.append(f"card {rid}'s line reads {c.get(rid, {}).get('kick')!r}, wanted {kick!r}")
    for rid, framed in (("n1", True), ("n2", True), ("today", False), ("week", False), ("absurd", False)):
        if c.get(rid, {}).get("frame") != framed:
            failures.append(f"card {rid} framed {c.get(rid, {}).get('frame')}, wanted {framed}")
    if c.get("n1", {}).get("date") != "10-12-2026" or c["n1"]["dateRight"] != c["n1"]["textW"]:
        failures.append(f"the date is not set right on its line: {c.get('n1')}")

    # 3. Every row is as tall as its tallest card's words, worked out ahead,
    # and a long title and artist list wrap in full.
    rows: dict[str, list[float]] = {}
    for rid, card in c.items():
        if card["th"] < 0 or card["textH"] > card["th"]:
            failures.append(f"card {rid}'s words ({card['textH']}) overrun its row ({card['th']})")
        rows.setdefault(card["row"], []).append(card["textH"])
        rows[card["row"]].append(card["th"])
    for uid, hs in rows.items():
        th = max(hs[1::2])
        if abs(max(hs[0::2]) - th) > 1:
            failures.append(f"row {uid} is laid out for {th} but its tallest card's words are {max(hs[0::2])}")
    if c.get("week", {}).get("titleLines", 0) < 3:
        failures.append(f"the long title did not wrap: {c.get('week')}")
    tops: dict[str, set[int]] = {}
    for card in c.values():
        tops.setdefault(card["row"], set()).add(card["top"])
    for uid, ys in tops.items():
        if len(ys) != 1:
            failures.append(f"row {uid}'s covers start at {sorted(ys)}, wanted one line")

    # 3b. A title or credit list that runs to sentences stops at the cap,
    # and so does its row.
    name_h = float(q('relMeasName.text = "A"; relMeasName.height'))
    ab = c.get("absurd", {})
    if ab.get("titleLines") != 3 or not ab.get("cut"):
        failures.append(f"the absurd title is not cut at three lines: {ab}")
    if not ab.get("more", "").startswith("+") or ab.get("flowH", 99) > 2 * name_h + 0.5:
        failures.append(f"the absurd credits are not cut at two lines with +N more: {ab} (a line is {name_h})")
    if ab.get("th", 999) > c.get("week", {}).get("th", 0) + 0.5 or ab.get("th", 999) > 110:
        failures.append(f"the absurd card's row grew to {ab.get('th')}, wanted it held at the cap")
    tip = q("""(function() {
        function find(it) {
            if (!it) return null
            if (it.objectName === "releaseTitle" && it.text.indexOf("Liner Notes") >= 0) return it
            var kids = it.children || []
            for (var i = 0; i < kids.length; i++) { var f = find(kids[i]); if (f) return f }
            return null
        }
        var t = find(relFeedList.contentItem)
        if (t) root.fullTipFor(t, t.text)
        return !!t
    })()""")
    if not tip or not pump(lambda: q("fullTip.opened") and q("fullTipText") == _ABSURD, 2000):
        failures.append("hovering the cut title did not show it in full")
    q("fullTipOff(fullTipOwner)")
    # A bubble whose owner dies under the pointer (a refetch rebuilds the
    # names) still closes when the pointer leaves whatever took its place.
    q("""(function() {
        var t = Qt.createQmlObject('import QtQuick; Text { width: 40; height: 12 }', relFeedList.contentItem)
        root.fullTipFor(t, "gone")
    })()""")
    if not pump(lambda: q("fullTip.opened") and q("fullTipText") == "gone", 2000):
        failures.append("the doomed owner's bubble never opened")
    q("fullTipOwner.destroy()")
    pump(lambda: q("fullTipOwner === null"), 1000)
    q("fullTipOff(relFeedList)")
    if not pump(lambda: not q("fullTip.opened") and not q("fullTip.visible"), 1000):
        failures.append("a bubble outlived its owner and stayed up after the pointer left")

    # 4. Seen by default: the arrivals stay across visits.
    q("openSearch()")
    settle(200)
    q("navBack()")
    if not pump(lambda: q("releasesOpen") and "Just in:2" in heads()):
        failures.append(f"leaving the pane spent the arrivals: {heads()}")

    # 5. Opening one marks it alone.
    q('releasesSaw("n2")')
    if not pump(lambda: "Just in:1" in heads()):
        failures.append(f"opening an arrival did not mark it seen: {heads()}")
    if store.arrivals() != ["n1"]:
        failures.append(f"the store still holds {store.arrivals()} after n2 was opened")
    src = QML_MAIN.read_text(encoding="utf-8")
    if 'root.releasesSaw("v:" + id)' not in src or 'if (kind === "album") root.releasesSaw("" + id)' not in src:
        failures.append("opening an album or a video no longer marks it seen")

    # 6. MARK AS SEEN puts the rest back in their sections.
    if not q(_PRESS_SEEN):
        failures.append("there is no MARK AS SEEN to press")
    if not pump(lambda: not any(x.startswith("Just in") for x in heads())):
        failures.append(f"MARK AS SEEN left Just in up: {heads()}")
    if store.arrivals():
        failures.append(f"the store still holds {store.arrivals()} after MARK AS SEEN")
    if order().get("This week") != ["n1", "n2", "w2", "week", "absurd"]:
        failures.append(f"This week runs {order().get('This week')}, wanted newest day first, then the latest find")

    # 7. Set to go after a visit: gone once the pane is left.
    bridge._waves_prefs["releases_arrivals"] = "visit"
    store.record_check(
        "ar1",
        [
            *base,
            _rel("n1", "2026-10-12", "single"),
            _rel("n2", "2026-10-11", "ep", explicit=True),
            _rel("n3", "2026-10-13", "album"),
        ],
    )
    q("openSearch()")
    settle(200)
    q("navBack()")
    if not pump(lambda: "Just in:1" in heads()):
        failures.append(f"an arrival did not show under Just in on a visit: {heads()}")
    q("openSearch()")
    settle(200)
    if store.arrivals():
        failures.append(f"leaving the pane kept {store.arrivals()} as arrivals")
    q("navBack()")
    settle(400)
    if any(x.startswith("Just in") for x in heads()):
        failures.append(f"Just in came back on the next visit: {heads()}")

    for f in failures:
        print(f, file=sys.stderr)
    return _EXIT_REGRESSED if failures else _EXIT_OK


if __name__ == "__main__" and "--run-scenario" in sys.argv:
    sys.exit(_run_scenario())
