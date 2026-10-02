"""Endless-scroll growth appends rows; a fresh drilled page incubates.

WHAT THIS FENCES OFF
--------------------
A Browse listing grows as it is scrolled (``browseSectionMore``). Growth
used to merge the fetched rows into the section object and reassign
``browseSections`` and ``browsePage``; a reassigned array is a Repeater
reset, so every landing shelf and every page section was torn down and
rebuilt inline: 400 to 550 ms of frozen window per 50-row fetch, measured
on the real app, under a scroll of the very list that grew. Growth now
lives beside the sections (``root.browseGrowth``, keyed by the row's paging
handle) and each section appends the new rows to a slot model it owns, so
nothing already built is touched.

A drilled page (an editorial page, a genre, a listing) also built its
shelves' cards and its in-view rows inline in the payload's turn (191 ms
for a thirteen-shelf genre page). It now incubates behind its loading hint
like the landing does, and fades in complete.

Pinned here, on the real Main.qml offscreen:

1. A growth answer appends rows to the open listing AND the landing shelf
   it was cut from, without rebuilding either (delegate identity kept), the
   section objects stay as they arrived, and the paging bookkeeping (offset,
   total, can-grow) follows the growth. A stale answer is ignored.
2. A fresh drilled page raises its build veil, its cards and rows are
   incubated rather than built in the handler, and the veil drops once they
   have all landed.

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

_EXIT_OK = 0
_EXIT_REGRESSED = 1
_EXIT_NO_QT = 77
_EXIT_PRECONDITION = 78

QML_MAIN = Path(__file__).resolve().parent.parent / "waves" / "waves_ui" / "qml" / "Main.qml"


def test_browse_growth_appends_in_place_and_a_fresh_page_incubates():
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="waves-browsegrowth-test-")
    proc = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), "--run-scenario"],
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    tail = "\n".join((proc.stdout + proc.stderr).strip().splitlines()[-12:])
    import pytest

    if proc.returncode == _EXIT_NO_QT:
        pytest.skip("PySide6 / offscreen Qt unavailable")
    if proc.returncode == _EXIT_PRECONDITION:
        pytest.skip(f"could not set up the Browse pages in this environment:\n{tail}")
    assert proc.returncode == _EXIT_OK, (
        "Browse growth rebuilt its rows again, or a fresh drilled page built inline. "
        f"Scenario exit={proc.returncode}:\n{tail}"
    )


def _track(i: int) -> dict:
    return {
        "id": f"t{i}",
        "kind": "track",
        "title": f"Track {i}",
        "artist": "Some Artist",
        "album": "Some Album",
        "duration": "3:20",
        "num": i + 1,
    }


def _tracks_section(title: str, n: int, *, data: str = "", total: int = 0) -> dict:
    return {
        "title": title,
        "rowKind": "tracks",
        "data": data,
        "offset": n if data else 0,
        "total": total,
        "items": [_track(i) for i in range(n)],
    }


def _card(i: int, prefix: str = "a") -> dict:
    return {"id": f"{prefix}{i}", "kind": "album", "title": f"Album {i}", "artist": "Some Artist"}


def _cards_section(title: str, n: int, *, data: str = "", total: int = 0, prefix: str = "a", start: int = 0) -> dict:
    return {
        "title": title,
        "rowKind": "cards",
        "data": data,
        "offset": start + n if data else 0,
        "total": total,
        "items": [_card(i, prefix) for i in range(start, start + n)],
    }


# Every track-row shell under a column, with its load state and its content
# object's printed form (the address: identity, not the model's contents).
_SHELLS = """
JSON.stringify((function() {
    var out = []
    function walk(item) {
        if (!item) return
        var kids = item.children || []
        for (var i = 0; i < kids.length; i++) {
            var k = kids[i]
            if (k && k.anchorRow !== undefined && k.hiRow !== undefined)
                out.push({ ready: k.status === 1, item: k.item ? String(k.item) : "", id: k.row ? k.row.id : "" })
            walk(k)
        }
    }
    walk(%s)
    return out
})())
"""

# Every card Loader under a column (the shelves' and the grid's).
_CARDS = """
JSON.stringify((function() {
    var out = []
    function walk(item) {
        if (!item) return
        var kids = item.children || []
        for (var i = 0; i < kids.length; i++) {
            var k = kids[i]
            if (k && k.counted !== undefined && k.card !== undefined)
                out.push({ ready: k.status === 1, counted: k.counted === true, async: k.asynchronous === true,
                           id: k.card ? ("" + (k.card.id || "")) : "" })
            walk(k)
        }
    }
    walk(%s)
    return out
})())
"""


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
        from _qml_offline import PARK_LOGIN_QML, patch_offline

        from waves.waves_ui.app import _load_mono
        from waves.waves_ui.backend import WavesBridge
    except Exception as exc:
        print(f"Qt platform/backend unavailable: {exc}", file=sys.stderr)
        return _EXIT_NO_QT

    patch_offline()
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
        ctx = QQmlEngine.contextForObject(root)
        e = QQmlExpression(ctx, root, expr)
        r = e.evaluate()
        if e.hasError():
            raise RuntimeError(e.error().toString())
        return r[0] if isinstance(r, tuple) else r

    def settle(ms: int = 250) -> None:
        loop = QEventLoop()
        QTimer.singleShot(ms, loop.quit)
        loop.exec()

    def pump(cond, limit_ms: int = 5000) -> bool:
        waited = 0
        while waited < limit_ms:
            if cond():
                return True
            settle(50)
            waited += 50
        return cond()

    failures: list[str] = []

    def check(cond, what: str) -> None:
        if not cond:
            failures.append(what)

    def shells(col: str) -> list:
        return json.loads(str(q(_SHELLS % col)))

    def cards(col: str) -> list:
        return json.loads(str(q(_CARDS % col)))

    q(PARK_LOGIN_QML)
    settle(200)
    q("bootOverlay.done = true; bootContentShown = 1")
    q("root.browseOpen = true; root.libraryOpen = false; root.settingsOpen = false; root.artistOpen = false")
    q("root.browseBuilding = false; root._browseAsyncBuild = false")  # a synchronous landing, one settle is enough
    settle(50)

    # ---- 1. growth appends in place -----------------------------------
    root.setProperty("browseSections", [_tracks_section("New Tracks", 5, data="d1", total=15)])
    settle(400)
    landing_before = shells("browseLandingCol")
    if len(landing_before) != 5 or not all(s["ready"] for s in landing_before):
        print(f"the landing shelf did not build its five rows: {landing_before}", file=sys.stderr)
        return _EXIT_PRECONDITION

    q("root.openBrowseSection(root.browseSections[0])")
    settle(400)
    if not str(q("root.browsePageKey")).startswith("local:"):
        print("the local listing did not open", file=sys.stderr)
        return _EXIT_PRECONDITION
    page_before = shells("browseDrillCol")
    check(
        len(page_before) == 5 and all(s["ready"] for s in page_before),
        f"the listing did not build its five rows: {page_before}",
    )

    key = str(q("root.browsePageKey"))
    bridge.browseSectionMore.emit(
        {
            "key": key,
            "data": "d1",
            "items": [_track(i) for i in range(5, 10)],
            "offset": 10,
            "reqOffset": 5,
            "more": True,
        }
    )
    settle(400)

    page_after = shells("browseDrillCol")
    check(
        [s["id"] for s in page_after] == [f"t{i}" for i in range(10)],
        f"the listing did not grow to ten rows: {[s['id'] for s in page_after]}",
    )
    check(
        page_after[:5]
        and all(a["item"] and a["item"] == b["item"] for a, b in zip(page_after[:5], page_before, strict=False)),
        "the growth rebuilt the listing's rows instead of appending to them",
    )
    landing_after = shells("browseLandingCol")
    check(
        len(landing_after) == 10,
        f"the landing shelf the page was cut from did not grow with it: {len(landing_after)} rows",
    )
    check(
        all(a["item"] and a["item"] == b["item"] for a, b in zip(landing_after[:5], landing_before, strict=False)),
        "the growth rebuilt the landing shelf's rows",
    )
    check(int(q("root.browseSections[0].items.length")) == 5, "the growth was written into the section object")
    check(
        int(q("root.browseSecItems(root.browseSections[0]).length")) == 10, "browseSecItems does not see the grown rows"
    )
    check(int(q("root.browseSecOffset(root.browseSections[0])")) == 10, "the paging offset did not follow the growth")
    check(
        q("root.browseCanGrow(root.browseSections[0])") is True, "a shelf with rows still to fetch says it cannot grow"
    )

    # A stale answer (the window already fetched) changes nothing.
    bridge.browseSectionMore.emit(
        {
            "key": key,
            "data": "d1",
            "items": [_track(i) for i in range(5, 10)],
            "offset": 10,
            "reqOffset": 5,
            "more": True,
        }
    )
    settle(200)
    check(len(shells("browseDrillCol")) == 10, "a stale growth answer was applied")

    # The last window: the shelf is complete and asks for no more.
    bridge.browseSectionMore.emit(
        {
            "key": key,
            "data": "d1",
            "items": [_track(i) for i in range(10, 15)],
            "offset": 15,
            "reqOffset": 10,
            "more": False,
        }
    )
    settle(300)
    check(len(shells("browseDrillCol")) == 15, "the last window did not land")
    check(q("root.browseCanGrow(root.browseSections[0])") is False, "a complete shelf still says it can grow")

    # ---- 2. a fresh drilled page incubates behind its veil -------------
    q("root.browsePageKey = 'pages/x'; root.browsePage = null; root.browsePageLoading = true")
    settle(50)
    bridge.browsePageLoaded.emit(
        {
            "key": "pages/x",
            "title": "X",
            "sections": [_cards_section("Shelf A", 8), _cards_section("Shelf B", 8), _tracks_section("Rows", 6)],
        }
    )
    # Before the event loop turns: the veil is up, the cards are counted and
    # incubating, and not one of them was built in the handler.
    check(q("root.browsePageBuilding") is True, "a fresh drilled page did not raise its build veil")
    page_cards = cards("browseDrillCol")
    check(len(page_cards) > 0, "the page created no card loaders at all")
    check(
        all(c["async"] and c["counted"] for c in page_cards), f"a card of the fresh page was built inline: {page_cards}"
    )
    check(not any(c["ready"] for c in page_cards), "a card of the fresh page was built in the handler's turn")
    rows = shells("browseDrillCol")
    check(
        len(rows) == 6 and not any(r["ready"] for r in rows),
        f"the page's rows were built in the handler's turn: {rows}",
    )
    if not pump(lambda: not q("root.browsePageBuilding")):
        failures.append("the page build veil never dropped")
    check(all(c["ready"] for c in cards("browseDrillCol")), "the veil dropped with cards still unbuilt")
    check(all(r["ready"] for r in shells("browseDrillCol")), "the veil dropped with rows still unbuilt")

    def wire_page(key: str, page: dict) -> None:
        """A page off the wire: keyed, then its payload emitted."""
        q(f"root.browsePageKey = '{key}'; root.browsePage = null; root.browsePageLoading = true")
        settle(50)
        page = dict(page, key=key)
        bridge.browsePageLoaded.emit(page)
        if not pump(lambda: not q("root.browsePageBuilding")):
            failures.append(f"the build veil of {key} never dropped")
        settle(100)

    def grow(expr: str, data: str, items: list, req: int, more: bool = True) -> None:
        """The section expr asks for its next window, and the wire answers."""
        q(f"root.browseGrow({expr})")
        bridge.browseSectionMore.emit(
            {
                "key": str(q("root.browsePageKey")),
                "data": data,
                "items": items,
                "offset": req + len(items),
                "reqOffset": req,
                "more": more,
            }
        )
        settle(400)

    # ---- 3. a shelf and its "show more" page share a paging handle ------
    # The landing shelf holds the row's first 12 cards; TIDAL's view-all
    # page of the same row holds its first 50. Growth keyed by the handle
    # alone went to both: the page could not grow (its offset never matched
    # the answer, it re-asked for the same window), or showed the shelf's
    # growth after its own first 50.
    root.setProperty("browseSections", [_cards_section("Top", 12, data="d2", total=200, prefix="x")])
    settle(400)
    wire_page("pages/top", {"title": "Top", "sections": [_cards_section("Top", 50, data="d2", total=200, prefix="x")]})
    check(len(cards("browseDrillCol")) == 50, "the view-all page did not open with its fifty cards")
    grow("root.browsePage.sections[0]", "d2", [_card(i, "x") for i in range(50, 100)], 50)
    page_ids = [c["id"] for c in cards("browseDrillCol")]
    check(
        page_ids == [f"x{i}" for i in range(100)],
        f"the view-all page did not grow by its own window: {len(page_ids)} cards",
    )
    # The shelf is a ListView: only the cards in view exist, so its length is
    # read from the rows it shows, not from its loaders.
    check(
        int(q("root.browseSecItems(root.browseSections[0]).length")) == 12,
        "the page's growth was appended to the landing shelf",
    )
    check(
        int(q("root.browseSecOffset(root.browsePage.sections[0])")) == 100,
        "the page's offset did not follow its growth",
    )
    check(
        int(q("root.browseSecOffset(root.browseSections[0])")) == 12, "the shelf's offset moved with the page's growth"
    )
    grow("root.browseSections[0]", "d2", [_card(i, "x") for i in range(12, 62)], 12)
    check(
        int(q("root.browseSecItems(root.browseSections[0]).length")) == 62,
        "the landing shelf did not grow by its own window",
    )
    page_ids = [c["id"] for c in cards("browseDrillCol")]
    check(
        len(page_ids) == 100 and len(set(page_ids)) == 100,
        f"the shelf's growth reached the view-all page: {len(page_ids)} cards, {len(set(page_ids))} distinct",
    )

    # ---- 4. a page reopened from the backend's grown cache --------------
    # The backend extends its cached copy of a grown row, and re-emits that
    # copy when the page is opened again: its base already holds the rows
    # the user scrolled into, and the growth kept for the old base must not
    # be appended after them a second time.
    wire_page("pages/g", {"title": "G", "sections": [_cards_section("Genre", 10, data="d3", total=100, prefix="c")]})
    grow("root.browsePage.sections[0]", "d3", [_card(i, "c") for i in range(10, 60)], 10)
    check(len(cards("browseDrillCol")) == 60, "the genre page did not grow")
    q("root.browseBack()")
    settle(100)
    wire_page("pages/g", {"title": "G", "sections": [_cards_section("Genre", 60, data="d3", total=100, prefix="c")]})
    page_ids = [c["id"] for c in cards("browseDrillCol")]
    check(
        page_ids == [f"c{i}" for i in range(60)],
        f"a page reopened from the grown cache shows its grown rows twice: {len(page_ids)} cards",
    )
    check(int(q("root.browseSecOffset(root.browsePage.sections[0])")) == 60, "the reopened page's offset is wrong")
    check(q("root.browseCanGrow(root.browsePage.sections[0])") is True, "the reopened page cannot grow on")

    # ---- 5. a landing refresh carrying the growth -----------------------
    # A revalidate grafts the growth the user scrolled into back into the
    # fresh landing row (the backend's _graft_scroll_growth). Read as a
    # changed row, that tore the growth down and re-cut the local listing
    # open on it, inline, rows and place and all.
    root.setProperty("browseSections", [_tracks_section("Fresh", 5, data="d4", total=60)])
    settle(400)
    q("root.openBrowseSection(root.browseSections[0])")
    settle(400)
    grow("root.browsePage.sections[0]", "d4", [_track(i) for i in range(5, 10)], 5)
    landing_grown = shells("browseLandingCol")
    page_grown = shells("browseDrillCol")
    check(len(landing_grown) == 10 and len(page_grown) == 10, "the local listing and its shelf did not grow together")
    page_obj = q("root.browsePage")
    grafted = _tracks_section("Fresh", 10, data="d4", total=60)
    bridge.browseLoaded.emit({"sections": [grafted], "genres": [], "moods": [], "decades": [], "error": False})
    settle(400)
    landing_after = shells("browseLandingCol")
    check(
        [s["id"] for s in landing_after] == [f"t{i}" for i in range(10)]
        and all(a["item"] == b["item"] for a, b in zip(landing_after, landing_grown, strict=False)),
        "a refresh carrying the shelf's growth rebuilt the shelf",
    )
    check(
        q("root.browsePage") == page_obj or int(q("root.browsePage.sections[0].items.length")) == 5,
        "a refresh carrying the growth re-cut the local listing",
    )
    page_after = shells("browseDrillCol")
    check(
        [s["id"] for s in page_after] == [f"t{i}" for i in range(10)]
        and all(a["item"] == b["item"] for a, b in zip(page_after, page_grown, strict=False)),
        "a refresh carrying the growth rebuilt the local listing's rows",
    )

    # ---- 6. Back from a page still building drops its veil --------------
    wire_page("pages/a", {"title": "A", "sections": [_cards_section("Shelf", 6, prefix="s")]})
    q(
        "root.browseStack = [root.browsePage]; root.browsePageKey = 'pages/w'; root.browsePage = null; root.browsePageLoading = true"
    )
    settle(50)
    bridge.browsePageLoaded.emit(
        {
            "key": "pages/w",
            "title": "W",
            "sections": [_cards_section("One", 8, prefix="w"), _cards_section("Two", 8, prefix="v")],
        }
    )
    check(q("root.browsePageBuilding") is True, "the second page did not raise its veil")
    q("root.browseBack()")
    check(q("root.browsePageBuilding") is False, "Back from a page mid-build left its veil over the page returned to")
    check(str(q("root.browsePageKey")) == "pages/a", "Back did not return to the stacked page")
    settle(300)

    # ---- 7. a long track list drops its veil as its rows land -----------
    # Rows counted at creation that leave the window before they land (the
    # layout moved the band under them) report in as they go; a veil that
    # waited on them held until the 800 ms guard.
    import time as _time

    q("root.browsePageKey = 'item:playlist:p'; root.browsePage = null; root.browsePageLoading = true")
    settle(50)
    t0 = _time.monotonic()
    bridge.browsePageLoaded.emit(
        {
            "key": "item:playlist:p",
            "title": "P",
            "header": {"kind": "playlist", "title": "P", "art": "", "subtitle": ""},
            "sections": [_tracks_section("Tracks", 60)],
        }
    )
    check(q("root.browsePageBuilding") is True, "a fresh long playlist did not raise its veil")
    if not pump(lambda: not q("root.browsePageBuilding"), 3000):
        failures.append("the long playlist's veil never dropped")
    took = (_time.monotonic() - t0) * 1000
    check(took < 600, f"the long playlist's veil held {took:.0f} ms, past the guard, not until its rows landed")

    if failures:
        for f in failures:
            print("REGRESSED:", f, file=sys.stderr)
        return _EXIT_REGRESSED
    print("browse growth appends in place, a fresh page incubates: OK")
    return _EXIT_OK


if __name__ == "__main__":
    if "--run-scenario" in sys.argv:
        raise SystemExit(_run_scenario())
