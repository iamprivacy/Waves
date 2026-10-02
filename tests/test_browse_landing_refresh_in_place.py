"""A refreshed Browse landing rebinds its sections in place.

WHAT THIS FENCES OFF
--------------------
The landing payload is revalidated about 1.4 s after the cached copy shows,
and the re-emit fires almost every launch now that the landing embeds For
You rows. Applying it used to reassign ``browseSections``, and a reassigned
array is a Repeater reset: every shelf torn down and all ~126 cards rebuilt,
~100 ms of frozen window with the landing on screen, and the whole landing
again behind the launch overlay, where the rebuild's incubation ticks
hitched the water (reported from livetesting).

The shelves' Repeater now takes a slot model (``browseSecSlots``) and each
slot reads its section from the array, so a refresh rebinds the sections
and their cards in place, a longer landing appends sections that incubate
on their own, and a shorter one drops its trailing slots.

Pinned here, on the real Main.qml offscreen:

1. A refresh with the same shape keeps every section and card item (delegate
   identity) and shows the new titles; no veil is raised.
2. A longer refresh keeps the sections it had and appends the new one, which
   incubates (asynchronous cards) and is not counted by any veil.
3. A shorter refresh keeps the leading sections and drops the rest.
4. A fresh build whose wayfinding chips did not change counts no tile
   shelves into its veil, so the veil drops when the cards land, not when the
   stall guard gives up.

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


def test_browse_landing_refresh_rebinds_in_place():
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="waves-browserefresh-test-")
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
        pytest.skip(f"could not set up the Browse landing in this environment:\n{tail}")
    assert (
        proc.returncode == _EXIT_OK
    ), f"a refreshed Browse landing rebuilt its shelves again. Scenario exit={proc.returncode}:\n{tail}"


def _cards_section(title: str, n: int, *, gen: str = "") -> dict:
    return {
        "title": title,
        "rowKind": "cards",
        "data": "",
        "items": [
            {"id": f"{gen}a{i}", "kind": "album", "title": f"{gen}Album {i}", "artist": "Some Artist"} for i in range(n)
        ],
    }


def _landing(sections: list) -> str:
    return json.dumps({"sections": sections, "genres": [], "moods": [], "decades": []})


# Every landing section under the column: its printed form (identity), the
# title it shows and whether it is a late (refresh-appended) section.
_SECTIONS = """
JSON.stringify((function() {
    var out = []
    function walk(item) {
        if (!item) return
        var kids = item.children || []
        for (var i = 0; i < kids.length; i++) {
            var k = kids[i]
            if (k && k.secIndex !== undefined && k.syncSlots !== undefined)
                out.push({ item: String(k), title: k.sec ? k.sec.title : "", late: k.late === true })
            walk(k)
        }
    }
    walk(browseLandingCol)
    return out
})())
"""

# Every card Loader under the column: identity of the built card, its card id
# and how it was created.
_CARDS = """
JSON.stringify((function() {
    var out = []
    function walk(item) {
        if (!item) return
        var kids = item.children || []
        for (var i = 0; i < kids.length; i++) {
            var k = kids[i]
            if (k && k.counted !== undefined && k.card !== undefined)
                out.push({ ready: k.status === 1, item: k.item ? String(k.item) : "",
                           id: k.card ? k.card.id : "", title: k.card ? k.card.title : "",
                           counted: k.counted === true, async: k.asynchronous === true })
            walk(k)
        }
    }
    walk(browseLandingCol)
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

    def sections() -> list:
        return json.loads(str(q(_SECTIONS)))

    def cards() -> list:
        return json.loads(str(q(_CARDS)))

    def same_items(after: list, before: list) -> bool:
        return bool(before) and all(a["item"] and a["item"] == b["item"] for a, b in zip(after, before, strict=False))

    q(PARK_LOGIN_QML)
    settle(200)
    q("bootOverlay.done = true; bootContentShown = 1")
    q("root.browseOpen = true; root.libraryOpen = false; root.settingsOpen = false; root.artistOpen = false")
    q("root.browseBuilding = false; root._browseAsyncBuild = false")  # a synchronous landing, one settle is enough
    settle(50)

    root.setProperty("browseSections", [_cards_section("Shelf A", 6), _cards_section("Shelf B", 6)])
    settle(400)
    secs_before = sections()
    cards_before = cards()
    if [s["title"] for s in secs_before] != ["Shelf A", "Shelf B"] or len(cards_before) != 12:
        print(f"the landing did not build its two shelves: {secs_before} / {len(cards_before)} cards", file=sys.stderr)
        return _EXIT_PRECONDITION
    if not all(c["ready"] for c in cards_before):
        print("the landing's cards did not build", file=sys.stderr)
        return _EXIT_PRECONDITION

    # ---- 1. same shape: everything rebinds in place --------------------
    q(
        f"root.applyBrowseLanding({_landing([_cards_section('Shelf A2', 6, gen='x'), _cards_section('Shelf B2', 6, gen='x')])})"
    )
    check(q("root.browseBuilding") is False, "a refresh of a built landing raised the build veil")
    settle(300)
    secs_after = sections()
    check(same_items(secs_after, secs_before), "the refresh rebuilt the landing's sections instead of rebinding them")
    check(
        [s["title"] for s in secs_after] == ["Shelf A2", "Shelf B2"],
        f"the rebound sections do not show the refreshed titles: {[s['title'] for s in secs_after]}",
    )
    cards_after = cards()
    check(same_items(cards_after, cards_before), "the refresh rebuilt the landing's cards instead of rebinding them")
    check(
        all(c["id"].startswith("xa") and c["title"].startswith("xAlbum") for c in cards_after),
        f"the rebound cards do not carry the refreshed items: {cards_after[:3]}",
    )
    check(not any(s["late"] for s in secs_after), "a rebound section reads as late")

    # ---- 2. a longer landing appends, and the new section incubates ----
    q(
        "root.applyBrowseLanding("
        + _landing(
            [
                _cards_section("Shelf A2", 6, gen="x"),
                _cards_section("Shelf B2", 6, gen="x"),
                _cards_section("Shelf C", 6),
            ]
        )
        + ")"
    )
    check(q("root.browseBuilding") is False, "a longer refresh raised the build veil")
    # The appended section incubates (its Loader is asynchronous), so it is
    # looked at once it exists; its cards must then be incubating, uncounted.
    if not pump(lambda: len(sections()) == 3):
        failures.append("the appended section never appeared")
    late_cards = [c for c in cards() if not c["id"].startswith("xa")]
    check(
        late_cards and all(c["async"] and not c["counted"] for c in late_cards),
        f"the appended section's cards were built inline or counted by a veil: {late_cards}",
    )
    if not pump(lambda: len([c for c in cards() if c["ready"]]) == 18):
        failures.append("the appended section's cards never landed")
    secs_grown = sections()
    check(
        len(secs_grown) == 3 and same_items(secs_grown[:2], secs_before), "a longer refresh rebuilt the sections it had"
    )
    check(
        secs_grown and secs_grown[-1]["title"] == "Shelf C" and secs_grown[-1]["late"],
        f"the appended section is wrong: {secs_grown[-1:]}",
    )
    check(same_items(cards()[:12], cards_before), "a longer refresh rebuilt the cards it had")

    # ---- 3. a shorter landing drops its tail ---------------------------
    q(f"root.applyBrowseLanding({_landing([_cards_section('Shelf A3', 6, gen='x')])})")
    settle(300)
    secs_cut = sections()
    check(
        len(secs_cut) == 1 and same_items(secs_cut, secs_before[:1]) and secs_cut[0]["title"] == "Shelf A3",
        f"a shorter refresh did not keep its first section in place: {secs_cut}",
    )
    check(len(cards()) == 6 and same_items(cards(), cards_before[:6]), "a shorter refresh rebuilt the cards it kept")

    # ---- 4. a fresh build with unchanged chips counts no tile shelves --
    root.setProperty("browseSections", [])
    settle(100)
    check(len(sections()) == 0, "emptying the landing left sections behind")
    q(f"root.applyBrowseLanding({_landing([_cards_section('Fresh A', 6), _cards_section('Fresh B', 6)])})")
    check(q("root.browseBuilding") is True, "a fresh build did not raise its veil")
    check(
        int(q("root._browseBuildTotal")) == 2,
        f"the fresh build counted tile shelves it never rebuilds: {q('root._browseBuildTotal')}",
    )
    if not pump(lambda: not q("root.browseBuilding")):
        failures.append("the fresh build's veil never dropped")
    check(
        int(q("root._browseBuildReady")) >= int(q("root._browseBuildTotal")),
        "the fresh build's veil was dropped by the stall guard, not by its loaders",
    )
    check([s["title"] for s in sections()] == ["Fresh A", "Fresh B"], "the fresh build did not show its sections")

    if failures:
        for f in failures:
            print("REGRESSED:", f, file=sys.stderr)
        return _EXIT_REGRESSED
    print("a refreshed Browse landing rebinds in place: OK")
    return _EXIT_OK


if __name__ == "__main__":
    if "--run-scenario" in sys.argv:
        raise SystemExit(_run_scenario())
