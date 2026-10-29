"""The Releases pane's search box, on screen.

A glyph in the pane's header opens into a box with the cursor in it (a click,
or Cmd+F); Esc clears it, a second Esc closes it. Each page keeps its own
search. The scenario proves:

- the feed's search reaches every release on record, past the window the
  feed browses, with the years before this one a section each, and an answer
  for text the box no longer holds is ignored;
- emptying the box brings the feed back as it was;
- the Artists page filters the follows by name, accents folded ("beyonce"
  finds "Beyoncé"), and the Excluded page by name or a held album's title;
- at the window's narrowest the open box fits beside the chips or takes the
  chips' line while they step aside, never overlapping them.

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


def test_the_releases_search_box_filters_every_page() -> None:
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    sandbox = tempfile.mkdtemp(prefix="waves-releases-search-")
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
    assert proc.returncode == _EXIT_OK, f"the Releases search is wrong on screen:\n{tail}"


# The feed's sections as "label:count", and its card ids in list order.
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
_IDS = """
JSON.stringify((function() {
    var out = []
    for (var i = 0; i < releasesModel.count; i++) {
        var cards = _relCards[releasesModel.get(i).uid] || []
        for (var j = 0; j < cards.length; j++) out.push("" + cards[j].id)
    }
    return out
})())
"""
# The followed names the Artists page shows, in order.
_FOLLOW_NAMES = """
JSON.stringify((function() {
    var a = []
    for (var i = 0; i < releasesFollowedModel.count; i++) a.push("" + _relFollowById[releasesFollowedModel.get(i).uid].name)
    return a
})())
"""
# Where an item sits in the window: "x,y,right".
# The feed's Albums chip.
_ALBUMS_CHIP = "(function () { var k = relTabsFlow.children; for (var i = 0; i < k.length; i++) if (k[i].modelData && k[i].modelData[0] === 'album') return k[i] })()"

_BOX = """(function () {
    var p = %s.mapToItem(null, 0, 0)
    return [Math.round(p.x), Math.round(p.y), Math.round(p.x + %s.width)].join(",")
})()"""


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
        from PySide6.QtCore import QEventLoop, QPoint, Qt, QTimer, QUrl
        from PySide6.QtGui import QGuiApplication
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

    def heads() -> list[str]:
        return json.loads(q(_HEADS))

    def ids() -> list[str]:
        return json.loads(q(_IDS))

    def box(item: str) -> tuple[int, int, int]:
        x, y, right = (int(v) for v in q(_BOX % (item, item)).split(","))
        return x, y, right

    def click_glyph() -> None:
        x, y, _r = box("relSearch")
        QTest.mouseClick(root, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(x + 15, y + 15))
        settle(300)

    def type_text(text: str) -> None:
        for ch in text:
            QTest.keyClick(root, ch)
        settle(450)  # past the pause the box waits for

    failures: list[str] = []
    settle()
    q(PARK_LOGIN_QML)
    q("bootOverlay.done = true")
    q("bootContentShown = 1")
    q("newCutoffClock.running = false")
    q('todayIso = "2026-10-14"')

    # Three follows; one of them has a release from 1997, years past the
    # one-year window the feed browses.
    store = bridge._releases
    bridge._waves_prefs["releases_window_months"] = "12"
    for aid, name in (("bj", "Björk"), ("be", "Beyoncé"), ("ot", "Other Artist")):
        store.follow(aid, name, "")
    store.record_check(
        "bj",
        [
            _rel("bj1", "2026-10-10", "Fossora Two", "Björk"),
            _rel("bj2", "2026-03-02", "Utopia Live", "Björk"),
            _rel("bj3", "1997-09-22", "Homogenic", "Björk"),
        ],
    )
    store.record_check("be", [_rel("be1", "2026-09-01", "Cowboy Carter Two", "Beyoncé")])
    store.record_check("ot", [_rel("ot1", "2026-10-02", "Something", "Other Artist", "single")])

    q("openReleases()")
    if not pump(lambda: q("releasesModel.count") > 0 and not q("releasesLoadingMore")):
        print("the feed never arrived", file=sys.stderr)
        return _EXIT_PRECONDITION
    settle(400)
    browsing = ids()
    if "bj3" in browsing or "bj1" not in browsing:
        print(f"the feed did not browse the window: {browsing}", file=sys.stderr)
        return _EXIT_PRECONDITION

    # 1. The glyph opens the box with the cursor in it.
    if q("relSearch.open") or q("relSearch.width") != 30:
        failures.append("the search starts open, not as a glyph")
    click_glyph()
    if not q("relSearch.open") or not q("relSearchField.activeFocus"):
        failures.append("clicking the glyph did not open the box ready to type")
    if q("relSearchField.placeholderText") != "Search releases…":
        failures.append(f"the feed's box reads {q('relSearchField.placeholderText')!r}")

    # 2. The feed's search reaches past the window, the old years by year.
    type_text("bjork")
    if not pump(lambda: q("releasesFinding") and q("relFindHave")):
        failures.append("the feed search never answered")
    if ids() != ["bj1", "bj2", "bj3"]:
        failures.append(f"the feed search shows {ids()}, wanted bj1, bj2, bj3")
    if heads() != ["This week:1", "March 2026:1", "1997:1"]:
        failures.append(f"the search's sections read {heads()}")
    # The filter is asked with the search (its limit counts under it).
    asked = bridge._rel_find_gen
    q('releasesFilter = "single"')
    if bridge._rel_find_gen != asked + 1:
        failures.append("a filter change did not ask the store again")
    if not pump(lambda: q("relFindHave") and ids() == []):
        failures.append(f"a filter change did not ask the search again: {ids()}")
    q('releasesFilter = "all"')
    if not pump(lambda: q("relFindHave") and ids() == ["bj1", "bj2", "bj3"]):
        failures.append(f"back to All the search shows {ids()}")
    type_text(" homog")
    pump(lambda: ids() == ["bj3"])
    if ids() != ["bj3"]:
        failures.append(f"a second word did not narrow the search: {ids()}")
    # An answer for text the box no longer holds is not this search.
    bridge.releasesSearched.emit("bjork", [_rel("zz", "2026-10-01", "Stale", "Björk")], False)
    settle(100)
    if ids() != ["bj3"]:
        failures.append("a stale search answer replaced the results")
    # A word nothing holds says so.
    q("relSearchField.text = 'nothing like it'")
    q("relNeedleApply()")
    if not pump(lambda: q("relFindHave") and q("releasesModel.count") == 0):
        failures.append("a search with no match did not empty the feed")
    elif not q("relEmptyHead.visible") or "nothing like it" not in q("relEmptyHead.text"):
        failures.append(f"the no-match line reads {q('relEmptyHead.text')!r}")

    # 3. Esc clears, and the feed is back as it was; a second Esc closes.
    QTest.keyClick(root, Qt.Key.Key_Escape)
    settle(200)
    if q("relSearchField.text") != "" or q("releasesFinding"):
        failures.append("Esc did not clear the search")
    if ids() != browsing:
        failures.append(f"clearing the search left {ids()}, wanted {browsing}")
    if not q("relSearch.open"):
        failures.append("the first Esc closed the box as well")
    QTest.keyClick(root, Qt.Key.Key_Escape)
    settle(300)
    if q("relSearch.open") or q("relSearchField.activeFocus"):
        failures.append("a second Esc did not close the box")

    # 4. Cmd+F opens it again, ready to type.
    QTest.keyClick(root, Qt.Key.Key_F, Qt.KeyboardModifier.ControlModifier)
    settle(300)
    if not q("relSearch.open") or not q("relSearchField.activeFocus"):
        failures.append("Cmd+F did not open the box ready to type")

    # 5. The Artists page filters the follows, accents folded; each page
    # keeps its own search.
    q('releasesSetView("manage")')
    if not pump(lambda: q("releasesFollowedHave") and q("releasesFollowedModel.count") == 3):
        failures.append("the Artists page never listed the follows")
    if q("relSearchField.placeholderText") != "Search artists…":
        failures.append(f"the Artists box reads {q('relSearchField.placeholderText')!r}")
    q("relSearchField.forceActiveFocus()")
    type_text("beyonce")
    if json.loads(q(_FOLLOW_NAMES)) != ["Beyoncé"]:
        failures.append(f"the Artists search shows {json.loads(q(_FOLLOW_NAMES))}")
    q('releasesSetView("feed")')
    settle(200)
    if q("relSearchField.text") != "" or q("releasesFinding"):
        failures.append("the Artists search followed onto the feed")
    q('releasesSetView("manage")')
    settle(200)
    if q("relSearchField.text") != "beyonce" or json.loads(q(_FOLLOW_NAMES)) != ["Beyoncé"]:
        failures.append("the Artists search was not there on the way back")
    q("relSearchField.text = 'zzz'")
    q("relNeedleApply()")
    settle(100)
    if q("releasesFollowedModel.count") != 0 or not q("relFollowNoMatch.visible"):
        failures.append("a name no follow holds still lists some")

    # 6. The Excluded page filters by name or by an album the library holds.
    q('releasesSetView("excluded")')
    bridge.excludedLoaded.emit(
        [
            {"name": "Zed", "key": "zed", "albums": 1, "tracks": 3, "held": [{"title": "Homogenic Covers"}]},
            {"name": "Ann", "key": "ann", "albums": 1, "tracks": 2, "held": [{"title": "Other"}]},
        ]
    )
    settle(200)
    q("relSearchField.forceActiveFocus()")
    type_text("homog")
    if q("releasesExcludedModel.count") != 1 or q("releasesExcludedShown[0].name") != "Zed":
        failures.append("the Excluded search did not find the artist by a held album")
    if q("releasesExcludedRows.length") != 2:
        failures.append("the Excluded search dropped rows from the page's own list")
    q("relSearchField.text = ''")
    settle(100)
    if q("releasesExcludedModel.count") != 2:
        failures.append("emptying the Excluded search did not bring every row back")

    # 7. At the narrowest window the open box never overlaps the chips.
    q('releasesSetView("feed")')
    root.setProperty("width", int(q("minimumWidth")))
    settle(300)
    if q("relFlowW(relTabsFlow)") > q("relTabsFlow.width") or q("relTabsFlow.childrenRect.height") > 30:
        failures.append("the kind chips do not fit one line at the narrowest window")
    q("relSearch.show()")
    settle(400)
    sx, _sy, sright = box("relSearch")
    _cx, _cy, cright = box("relChipsBox")
    if sright > cright:
        failures.append(f"the open box reaches past its line ({sright} > {cright})")
    # Searching, the chips stay beside the box as plain words, so a search
    # can still be narrowed by kind.
    if q("relChipsBox.cover"):
        failures.append("at the narrowest window the open box hid the kind chips")
    if q(_ALBUMS_CHIP + ".words") is not True:
        failures.append("the kind chips did not turn into words while searching")
    else:
        type_text("b")
        settle(300)
        ax, ay, _ar = box(_ALBUMS_CHIP)
        QTest.mouseClick(root, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(ax + 8, ay + 15))
        settle(200)
        if q("releasesFilter") != "album" or not q("relSearch.open"):
            failures.append("clicking Albums beside the open box did not narrow the search")
        q('releasesFilter = "all"')
        q("relSearchField.text = ''")
        q("relSearch.show()")
        settle(300)
    if q("relChipsBox.cover"):
        if q("relTabsFlow.opacity") != 0:
            failures.append("the chips did not step aside under a box that takes their line")
    else:
        tx, _ty, _tr = box("relTabsFlow")
        chips_end = tx + int(q("relFlowW(relTabsFlow)"))
        if chips_end > sx:
            failures.append(f"the open box overlaps the chips ({chips_end} > {sx})")
        if q("relTabsFlow.childrenRect.height") > 30:
            failures.append("the chips wrapped beside the open box")
    if q("relSearch.width") < 150:
        failures.append(f"the open box is only {q('relSearch.width')} px wide")

    # 8. A second click on the magnifier folds the open box away for good,
    # empty or holding a word (the field's focus loss must not reopen it).
    q("relSearch.collapse()")
    settle(300)
    for word in ("", "b"):
        click_glyph()
        if word:
            type_text(word)
            settle(400)
        click_glyph()
        settle(300)
        if q("relSearch.open"):
            failures.append(f"a second magnifier click left the box open (holding {word!r})")

    noise = [w for w in warnings if "relSearch" in w or "relFind" in w or "Needle" in w]
    if noise:
        failures.append("QML warnings: " + "; ".join(noise[:3]))

    if failures:
        print("\n".join(failures), file=sys.stderr)
        return _EXIT_REGRESSED
    print("ok")
    return _EXIT_OK


if __name__ == "__main__" and "--run-scenario" in sys.argv:
    sys.exit(_run_scenario())
