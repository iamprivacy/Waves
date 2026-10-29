"""The Releases pane's scroll, its timeline hint and its section rules, on screen.

The scenario proves:

- a fast swipe whose momentum arrives after its end (macOS does that on a
  busy frame) carries on from where it was, never snapping back to where
  the swipe began (a Flickable alone does, Qt 6.11);
- the feed and the Artists page each keep their place across the Releases /
  Artists switch, as Browse's pages do, while a new shape of the Artists page
  (Roster, Shelves or Ledger) opens at its top;
- a timeline period with more than four releases shows four in its hint and
  the rest scroll inside it, with soft edges only on a side that has rows
  past it; a swipe past the list's end stays in the hint (the page under it
  does not scroll, the hint stays up), and a release scrolled into view
  opens with a real click;
- a feed section head's rule is as heavy as the headline's bar and runs on
  from the release count, in line with its letters;
- a row whose cards are still being built behind its asynchronous Loader
  wears card-shaped stand-ins (a hard flick outruns the loaders, and bare
  water where results just were reads as the feed losing them), gone once
  the real row is ready.

Runs in a SUBPROCESS like the other Main.qml scenarios: building the bridge
installs process-global handlers that must not leak into the suite.
"""

from __future__ import annotations

import itertools
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


def test_releases_pages_scroll_their_own_way() -> None:
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    sandbox = tempfile.mkdtemp(prefix="waves-releases-scroll-")
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
    assert proc.returncode == _EXIT_OK, f"the Releases scroll is wrong on screen:\n{tail}"


# Every Releases scroller, as "name=contentY" (a * marks the one on screen).
_YS = """
JSON.stringify([relFeedList, relFollowRoster, relFollowShelves, relFollowLedger].map(function (f) {
    return f.objectName + "=" + Math.round(f.contentY - f.originY) + (f.visible ? "*" : "")
}))
"""

# Where an item sits in the window: "x,y,right".
_BOX = """(function () {
    var p = %s.mapToItem(null, 0, 0)
    return [Math.round(p.x), Math.round(p.y), Math.round(p.x + %s.width)].join(",")
})()"""

# Whether the hint's top or bottom fade shows.
_TIP_FADE = """
(function() {
    var kids = relTipList.children
    for (var i = 0; i < kids.length; i++) if (kids[i].objectName === "%s") return kids[i].visible
    return null
})()
"""

# The newest mark of the busy artist's timeline, in window pixels.
_BUSY_PIN = """
(function() {
    function find(it, out) {
        if (it.objectName === "relTimeline" && it.visible && it.marks && it.marks.length > 0) out.push(it)
        var kids = it.children || []
        for (var i = 0; i < kids.length; i++) find(kids[i], out)
        return out
    }
    var tls = find(relFollowLedger.contentItem, [])
    for (var j = 0; j < tls.length; j++) {
        var m = tls[j].marks[tls[j].marks.length - 1]
        if (("" + m.r.id).indexOf("zz") !== 0) continue
        var tl = tls[j]
        var p = tl.mapToItem(null, tl.xOf(m.dn), tl.baseY - 12)
        return Math.round(p.x) + "," + Math.round(p.y)
    }
    return ""
})()
"""

# The hint row nearest the list's bottom edge: "x,y,id" of its center.
_TIP_LAST_SHOWN = """
(function() {
    var at = relTipList.indexAt(10, relTipList.contentY + relTipList.height - 30)
    var it = relTipList.itemAtIndex(at)
    if (!it) return ""
    var p = it.mapToItem(null, it.width / 2, it.height / 2)
    return Math.round(p.x) + "," + Math.round(p.y) + "," + it.modelData.id
})()
"""

# The feed's first head rule: "height,color matches textHi,starts past the
# count,how far its middle sits above the count's baseline".
_HEAD_RULE = """
(function() {
    function find(it) {
        if (it.objectName === "relHeadRule" && it.visible) return it
        var kids = it.children || []
        for (var i = 0; i < kids.length; i++) { var r = find(kids[i]); if (r) return r }
        return null
    }
    var r = find(relFeedList.contentItem)
    if (!r) return ""
    var c = null, kids = r.parent.children
    for (var i = 0; i < kids.length; i++) if (kids[i].text !== undefined && /release/.test(kids[i].text)) c = kids[i]
    var rp = r.mapToItem(null, 0, 0), cp = c.mapToItem(null, 0, 0)
    var lift = cp.y + c.baselineOffset - (rp.y + r.height / 2)
    return [r.height, Qt.colorEqual(r.color, textHi), rp.x > cp.x + c.width, lift.toFixed(1)].join(",")
})()
"""


def _rel(rid: str, day: str, title: str, artist: str, rtype: str = "album") -> dict:
    return {
        "kind": "album",
        "id": rid,
        "title": title,
        "artist": artist,
        "artist_id": "a-" + rid,
        "artists": [{"id": "a-" + rid, "name": artist}],
        "art": "",
        "date": day,
        "listed": "",
        "year": day[:4],
        "rtype": rtype,
        "explicit": False,
        "tracks": 10,
        "duration_sec": 200,
        "quality": "LOSSLESS",
    }


def _run_scenario() -> int:  # noqa: C901
    here = Path(__file__).resolve()
    sys.path.insert(0, str(here.parent.parent))
    sys.path.insert(0, str(here.parent))
    try:
        from PySide6.QtCore import QEventLoop, QPoint, QPointF, Qt, QTimer, QUrl
        from PySide6.QtGui import QGuiApplication, QWheelEvent
        from PySide6.QtQml import QQmlApplicationEngine, QQmlEngine, QQmlExpression
        from PySide6.QtTest import QTest
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
    warnings: list[str] = []
    engine.warnings.connect(lambda ws: warnings.extend(w.toString() for w in ws))
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
    root.setProperty("height", 1200)

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

    def box(item: str) -> tuple[int, int, int]:
        x, y, right = (int(v) for v in q(_BOX % (item, item)).split(","))
        return x, y, right

    failures: list[str] = []
    settle()
    q(PARK_LOGIN_QML)
    q("bootOverlay.done = true")
    q("bootContentShown = 1")
    q("newCutoffClock.running = false")
    q('todayIso = "2026-10-14"')

    def ys() -> dict[str, int]:
        out = {}
        for part in json.loads(q(_YS)):
            name, y = part.rstrip("*").split("=")
            out[name] = int(y)
        return out

    def swipe(x: int, y: int, dy: int, steps: int, momentum: int = 0, late: bool = False) -> list[int]:
        """A trackpad swipe at (x, y): begin, `steps` updates of dy, then
        `momentum` easing events and the end. `late` sends the end as the
        fingers lift and the momentum after it, as macOS does when the
        momentum's first event is not queued yet on a busy frame. Returns
        the page's contentY after every event."""
        ys: list[int] = []

        def send(phase, d: int) -> None:
            ev = QWheelEvent(
                QPointF(x, y),
                QPointF(x, y),
                QPoint(0, d),
                QPoint(0, d * 2),
                Qt.MouseButton.NoButton,
                Qt.KeyboardModifier.NoModifier,
                phase,
                False,
                Qt.MouseEventSource.MouseEventSynthesizedBySystem,
            )
            QGuiApplication.sendEvent(root, ev)
            settle(16)
            ys.append(round(q("relFeedList.contentY")))

        send(Qt.ScrollPhase.ScrollBegin, 0)
        for _ in range(steps):
            send(Qt.ScrollPhase.ScrollUpdate, dy)
        if momentum and late:
            send(Qt.ScrollPhase.ScrollEnd, 0)
        for k in range(momentum):
            send(Qt.ScrollPhase.ScrollMomentum, round(dy * (1 - k / momentum)))
        send(Qt.ScrollPhase.ScrollEnd, 0)
        settle(500)
        return ys

    def swipe_over(item: str) -> None:
        x, y, right = box(item)
        swipe((x + right) // 2, y + 200, -60, 30)

    # Eighty follows, four releases each, and one artist with nine releases
    # in one quarter of this year, the newest of them all (first by recent).
    store = bridge._releases
    bridge._waves_prefs["releases_window_months"] = "12"
    for n in range(80):
        aid, name = f"a{n:02d}", f"Artist {n:02d}"
        store.follow(aid, name, "")
        store.record_check(
            aid,
            [_rel(f"{aid}r{k}", f"2026-{1 + (n + k) % 9:02d}-{1 + k:02d}", f"T{k}", name) for k in range(4)],
        )
    busy = [_rel(f"zz{k}", f"2026-09-{5 + 2 * k:02d}", f"Busy {k}", "Busy One", "single") for k in range(9)]
    store.follow("zz", "Busy One", "")
    store.record_check("zz", [*busy, _rel("zzold", "2019-05-05", "Old One", "Busy One")])

    q("openReleases()")
    if not pump(lambda: q("releasesModel.count") > 0 and not q("releasesLoadingMore")):
        print("the feed never arrived", file=sys.stderr)
        return _EXIT_PRECONDITION
    settle(400)

    # 1. The section rule is the headline bar's weight, in the text color.
    # It runs on from the count, through the middle of its lowercase letters
    # (a 12 px mono x-height is ~6.5 px, so its middle is ~3 px up).
    rule = q(_HEAD_RULE).split(",")
    if rule[:3] != ["3", "true", "true"] or not 2.0 <= float(rule[3]) <= 4.5:
        failures.append(f"the feed's section rule reads {rule!r}, wanted 3 px textHi in line with the count")

    # 2. A fast swipe whose momentum comes after its end (macOS on a busy
    # frame) carries on from where the fingers lifted, the same as one whose
    # momentum is on time: it never snaps back to where the swipe began.
    # Every card already here on the list first: a swipe that reached the
    # end of the first page would show the next one and give the second
    # swipe more room than the first.
    q("releasesFeedLimit = 100000")
    q("releasesReveal()")
    settle(300)
    fx, fy, fright = box("relFeedList")
    sx, sy = (fx + fright) // 2, fy + 300
    on_time = swipe(sx, sy, -80, 12, momentum=30)
    q("relFeedList.contentY = relFeedList.originY")
    settle(300)
    late = swipe(sx, sy, -80, 12, momentum=30, late=True)
    back = [(a, b) for a, b in itertools.pairwise(late) if b < a - 2]
    # Ending within a tenth of the on-time swipe's travel (they differ by
    # about one event's delta in how the momentum's first event is counted).
    if back or abs(late[-1] - on_time[-1]) > on_time[-1] / 10:
        failures.append(f"a swipe with late momentum snapped back {back[:2]}, ended {late[-1]} vs {on_time[-1]}")
    q("relFeedList.contentY = relFeedList.originY")
    settle(300)

    # 3. The feed and the Artists page each keep their place across the
    # switch (the Artists page opens at its top only the first time, it has
    # not been scrolled yet), and a new shape of the Artists page opens at
    # its top.
    swipe_over("relFeedList")
    feed_y = ys()["relFeedList"]
    if feed_y < 300:
        print(f"the feed did not scroll: {ys()}", file=sys.stderr)
        return _EXIT_PRECONDITION
    q('releasesSetView("manage")')
    if not pump(lambda: q("releasesFollowedModel.count") > 20):
        print("the Artists page never listed the follows", file=sys.stderr)
        return _EXIT_PRECONDITION
    settle(300)
    if ys()["relFollowRoster"] != 0:
        failures.append(f"the Artists page opened scrolled on its first visit: {ys()}")
    swipe_over("relFollowRoster")
    roster_y = ys()["relFollowRoster"]
    if roster_y < 300:
        failures.append(f"the Artists page did not scroll: {ys()}")
    q('releasesSetView("feed")')
    settle(300)
    if ys()["relFeedList"] != feed_y:
        failures.append(f"the feed lost its place across the switch: {ys()} (left at {feed_y})")
    q('releasesSetView("manage")')
    settle(300)
    if ys()["relFollowRoster"] != roster_y:
        failures.append(f"the Artists page lost its place across the switch: {ys()} (left at {roster_y})")
    for shape, view in (("ledger", "relFollowLedger"), ("shelves", "relFollowShelves"), ("roster", "relFollowRoster")):
        swipe_over("relFollowFlick()")
        q(f'releasesSetFollowView("{shape}")')
        settle(400)
        if ys()[view] != 0:
            failures.append(f"the {shape} shape opened scrolled: {ys()}")

    # 4. The hint for a busy period: four rows and a peek, the rest scrolled to.
    q('releasesSetFollowSort("recent")')
    q('releasesSetFollowView("ledger")')
    settle(600)
    pin = q(_BUSY_PIN)
    if not pin:
        failures.append("the ledger drew no timeline")
    else:
        px, py = (int(v) for v in pin.split(","))
        QTest.mouseMove(root, QPoint(px - 30, py))
        settle(50)
        QTest.mouseMove(root, QPoint(px, py))
        settle(300)
        if not q("relTip.visible") or q("relTipList.count") != 9:
            failures.append(f"the busy period's hint holds {q('relTipList.count')} releases, wanted 9")
        pitch = int(q("relTipRowH")) + 8
        want_h = 4 * pitch - 8 + 8 + int(q("relTipPeek"))
        if int(q("relTipList.height")) != want_h or not q("relTipList.scrolls"):
            failures.append(f"the hint is {q('relTipList.height')} px tall, wanted four rows and a peek ({want_h})")
        if q(_TIP_FADE % "relTipFadeTop") or not q(_TIP_FADE % "relTipFadeBottom"):
            failures.append("a hint at its top should fade only at its bottom")
        # Onto the hint, then swipe inside it to its end and past.
        tx, ty, tright = box("relTipList")
        for yy in range(py, ty + 60, -4 if ty + 60 < py else 4):
            QTest.mouseMove(root, QPoint(px, yy))
            settle(16)
        hx, hy = (tx + tright) // 2, ty + 60
        QTest.mouseMove(root, QPoint(hx, hy))
        settle(200)
        page_y = q("relFollowLedger.contentY")
        swipe(hx, hy, -40, 12)
        swipe(hx, hy, -40, 12)
        if not q("relTipList.atYEnd"):
            failures.append("swiping inside the hint did not reach its last release")
        if q("relFollowLedger.contentY") != page_y or not q("relTip.visible"):
            failures.append("a swipe past the hint's end scrolled the page under it")
        if not q(_TIP_FADE % "relTipFadeTop") or q(_TIP_FADE % "relTipFadeBottom"):
            failures.append("a hint at its end should fade only at its top")
        last = q(_TIP_LAST_SHOWN)
        if not last:
            failures.append("no release in view at the hint's end")
        else:
            lx, ly, lid = last.split(",")
            QTest.mouseMove(root, QPoint(int(lx), int(ly)))
            settle(150)
            QTest.mouseClick(root, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(int(lx), int(ly)))
            settle(300)
            if q("releasesOpen") or q("browsePageKey") != f"item:album:{lid}":
                failures.append(f"clicking a scrolled-to release did not open it ({q('browsePageKey')!r})")

    # 5. A row rebuilding behind its Loader wears the ghost until it is
    # ready. Dropping and raising `shown` is exactly what a far unload and
    # a flick back do; read in the same JS run, before the loader lands.
    q("openReleases()")
    q('releasesSetView("feed")')
    if not pump(lambda: q("releasesModel.count") > 0):
        print("the feed never came back for the ghost check", file=sys.stderr)
        return _EXIT_PRECONDITION
    settle(300)
    probe = q("""
        (function() {
            for (var i = 0; i < relFeedRows.count; i++) {
                var it = relFeedRows.itemAt(i)
                if (!it || it.rowKind !== "row") continue
                it.shown = false
                it.shown = true
                var g = null, l = null, kids = it.children
                for (var k = 0; k < kids.length; k++) {
                    if (kids[k].objectName === "relRowGhost") g = kids[k]
                    else if (kids[k].sourceComponent !== undefined) l = kids[k]
                }
                // The ghost's cover asks for exactly what Art pinned: same
                // url, square decode at twice the card's width.
                var art = null, card = it.cards[0] || ({})
                var cell = g && g.item ? g.item.children[0] : null
                if (cell) for (var c = 0; c < cell.children.length; c++)
                    if (cell.children[c].objectName === "relGhostArt") art = cell.children[c]
                var want = (card.kind === "video" ? relVideoW : relCardW) * 2
                return JSON.stringify({ghost: !!(g && g.active), status: l ? l.status : -1,
                                       drawn: g && g.item ? Math.round(g.item.width) : 0,
                                       art: !!art && ("" + art.source) === (card.art || "")
                                            && art.sourceSize.width === want && art.sourceSize.height === want})
            }
            return ""
        })()
        """)
    got = json.loads(probe) if probe else {}
    if not got.get("ghost") or got.get("status") == 1:
        failures.append(f"a rebuilding row shows no ghost cards: {probe!r}")
    elif not got.get("drawn"):
        failures.append(f"the ghost row drew nothing: {probe!r}")
    elif not got.get("art"):
        failures.append(f"the ghost's cover does not ask for the pixmap Art pinned: {probe!r}")
    # The row fades in over the ghost; dropping the ghost the frame the
    # row is ready blinks the covers out for the length of the fade.
    fading = """
        (function() {
            for (var i = 0; i < relFeedRows.count; i++) {
                var it = relFeedRows.itemAt(i)
                if (!it || it.rowKind !== "row") continue
                var g = null, l = null, kids = it.children
                for (var k = 0; k < kids.length; k++) {
                    if (kids[k].objectName === "relRowGhost") g = kids[k]
                    else if (kids[k].sourceComponent !== undefined) l = kids[k]
                }
                if (l && l.status === 1 && l.opacity < 1) return g && g.active ? "held" : "dropped"
                return l && l.status === 1 ? "done" : "wait"
            }
            return ""
        })()
        """
    seen = set()
    pump(lambda: seen.add(q(fading)) or "done" in seen, 1500)
    if "dropped" in seen:
        failures.append(f"the ghost left before its row finished fading in: {sorted(seen)!r}")
    settle(600)
    after = q("""
        (function() {
            for (var i = 0; i < relFeedRows.count; i++) {
                var it = relFeedRows.itemAt(i)
                if (!it || it.rowKind !== "row") continue
                var g = null, l = null, kids = it.children
                for (var k = 0; k < kids.length; k++) {
                    if (kids[k].objectName === "relRowGhost") g = kids[k]
                    else if (kids[k].sourceComponent !== undefined) l = kids[k]
                }
                return JSON.stringify({ghost: !!(g && g.active), status: l ? l.status : -1})
            }
            return ""
        })()
        """)
    got = json.loads(after) if after else {}
    if got.get("ghost") or got.get("status") != 1:
        failures.append(f"the ghost outlived its row (or the row never loaded): {after!r}")

    noise = [w for w in warnings if "relTip" in w or "relHead" in w or "relToTop" in w]
    if noise:
        failures.append("QML warnings: " + "; ".join(noise[:3]))

    if failures:
        print("\n".join(failures), file=sys.stderr)
        return _EXIT_REGRESSED
    print("ok")
    return _EXIT_OK


if __name__ == "__main__" and "--run-scenario" in sys.argv:
    sys.exit(_run_scenario())
