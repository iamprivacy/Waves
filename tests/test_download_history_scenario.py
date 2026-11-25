"""The Download history page, opened from the Queue drawer.

WHAT THIS FENCES OFF
--------------------
On the real Main.qml, with the real bridge reading a sandboxed ownership store:

1. The Queue drawer's HISTORY button opens the page: My Tidal's pane on the
   hidden "history" category, named "Download history", sort hidden.
2. With nothing recorded, the empty line shows.
3. Entries written to the store appear once the bridge says the list
   changed, newest first, and an entry expands to its per-track log.
4. CLEAR HISTORY asks first; confirming empties the list.
5. Pressing My Tidal from the history page lands on Home, never on the
   hidden category.

Runs in a SUBPROCESS like the other Main.qml scenarios. ``WAVES_HISTORY_SHOT_DIR``
saves screenshots of the drawer footer and the page for a visual check.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

_EXIT_OK = 0
_EXIT_REGRESSED = 1
_EXIT_NO_QT = 77
_EXIT_PRECONDITION = 78

QML_MAIN = Path(__file__).resolve().parent.parent / "waves" / "waves_ui" / "qml" / "Main.qml"


def test_download_history_page_opens_lists_expands_and_clears():
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="waves-history-test-")
    env["HOME"] = env["XDG_CONFIG_HOME"]  # nothing in the scenario may reach the real home
    proc = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), "--run-scenario"],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    # The scenario's own verdict lines first: stderr noise must not push them out.
    said = [line for line in proc.stdout.splitlines() if line.startswith(("FAIL:", "long history", "checks failed"))]
    tail = "\n".join(said + (proc.stdout + proc.stderr).strip().splitlines()[-12:])
    print("\n".join(line for line in said if line.startswith("long history")))  # the measured stalls, with -s
    import pytest

    if proc.returncode == _EXIT_NO_QT:
        pytest.skip("PySide6 / offscreen Qt unavailable")
    if proc.returncode == _EXIT_PRECONDITION:
        pytest.skip(f"could not set up the scenario in this environment:\n{tail}")
    assert proc.returncode == _EXIT_OK, f"Download history regressed. Scenario exit={proc.returncode}:\n{tail}"


def _run_scenario() -> int:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    try:
        from PySide6.QtCore import QCoreApplication, QEventLoop, QTimer, QUrl
        from PySide6.QtGui import QGuiApplication
        from PySide6.QtQml import QQmlApplicationEngine, QQmlEngine, QQmlExpression
        from PySide6.QtQuick import QQuickWindow
    except Exception as exc:
        print(f"Qt unavailable: {exc}", file=sys.stderr)
        return _EXIT_NO_QT

    from _qml_offline import PARK_LOGIN_QML, patch_offline

    patch_offline()

    app = QGuiApplication.instance() or QGuiApplication([])
    try:
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
    if not roots or not isinstance(roots[0], QQuickWindow):
        print("Main.qml failed to load", file=sys.stderr)
        return _EXIT_PRECONDITION
    root = roots[0]

    def q(expr: str):
        ctx = QQmlEngine.contextForObject(root)
        e = QQmlExpression(ctx, root, expr)
        r = e.evaluate()
        if e.hasError():
            raise RuntimeError(f"{expr}: {e.error().toString()}")
        r = r[0] if isinstance(r, tuple) else r
        return r.toVariant() if hasattr(r, "toVariant") else r

    def settle(ms: int = 150) -> None:
        loop = QEventLoop()
        QTimer.singleShot(ms, loop.quit)
        loop.exec()

    def shot(name: str) -> None:
        out = os.environ.get("WAVES_HISTORY_SHOT_DIR")
        if out:
            root.grabWindow().save(os.path.join(out, name))

    failures: list[str] = []

    def check(cond, what: str) -> None:
        if not cond:
            failures.append(what)

    root.resize(1280, 820)
    root.setVisible(True)
    settle(300)
    q("bootOverlay.done = true")
    q("bootContentShown = 1")
    q(PARK_LOGIN_QML)
    q("termsGate.visible = false")
    q("ffmpegGate.visible = false")
    bridge._logged_in = True
    bridge.loggedInChanged.emit()
    settle(100)
    q("termsGate.visible = false")
    q("ffmpegGate.visible = false")

    # 1. The drawer's HISTORY button opens the page.
    q("queueDrawer.open()")
    settle(500)
    check(bool(q("queueHistoryBtn.visible")), "HISTORY button not in the queue drawer")
    shot("history_queue_footer.png")
    q("queueHistoryBtn.clicked()")
    settle(400)
    check(bool(q("libraryOpen")) and q("libraryCategory") == "history", "HISTORY did not open the history page")
    check(q("currentNavLabel") == "Download history", "page not named Download history")
    check(bool(q("libTabsFlow.visible")) and bool(q("libSortBox.visible")), "filter chips or sort missing on history")
    check(bool(q("historyShown")), "history page not flagged as shown")
    check(not bool(q("queueDrawer.opened")), "drawer stayed open")

    # 2. Empty store: the empty line.
    check(bool(q("historyEmpty.visible")), "empty line not shown on an empty history")
    shot("history_empty.png")

    # 3. Two entries land; the page refreshes on the change nudge.
    store = bridge._ownership
    tracks = [
        {"id": "1", "num": 1, "title": "Opening", "status": "done", "quality": "LOSSLESS", "saved_at": 1795000000},
        {"id": "2", "num": 2, "title": "Middle", "status": "skipped", "quality": ""},
        {"id": "3", "num": 3, "title": "Closing", "status": "failed", "quality": ""},
    ]
    store.record_history(
        "L",
        1,
        {
            "kind": "album",
            "media_id": "100",
            "name": "Older Album",
            "artist": "Some Artist",
            "art": "",
            "status": "done",
            "landed": "LOSSLESS",
            "mix": "[]",
            "saved": 1,
            "total": 3,
            "tracks": tracks,
        },
    )
    store.record_history(
        "L",
        2,
        {
            "kind": "track",
            "media_id": "200",
            "name": "Newest Single",
            "artist": "Other Artist",
            "art": "",
            "status": "done",
            "landed": "HI-RES",
            "mix": "[]",
            "saved": 1,
            "total": 1,
            "tracks": [{"id": "200", "num": 1, "title": "Newest Single", "status": "done", "quality": "HI-RES"}],
        },
    )
    bridge.historyChanged.emit("")
    settle(400)
    check(q("libHistoryModel.count") == 2, f"expected 2 entries, got {q('libHistoryModel.count')}")
    check(q("libHistoryModel.get(0).name") == "Newest Single", "newest entry is not first")
    check(not bool(q("historyEmpty.visible")), "empty line still shown")
    check(bool(q("historyClearBtn.visible")) and bool(q("historyClearBtn.parent.visible")), "CLEAR HISTORY not shown")
    q("libHistoryList.itemAtIndex(1).expanded = true")
    settle(300)
    check(q("libHistoryList.itemAtIndex(1).trackList.length") == 3, "album entry did not expand to its log")
    check(bool(q("libHistoryList.itemAtIndex(1).trackList[0].savedDate")), "a saved track carries no saved date")
    check(q("libHistoryList.itemAtIndex(1).timeText").endswith("M"), "entry shows no time of day")
    check(not bool(q("libHistoryList.itemAtIndex(0).canExpand")), "a single track entry offers a chevron")
    shot("history_list_expanded.png")

    # 3b. The chips filter by kind (Home is every kind) and the box flips the order.
    check(q("libTabsFlow.children[0].on") is True, "Home chip not lit on the unfiltered history")
    q("historyFilter('album', false)")
    settle(300)
    check(q("libHistoryModel.count") == 1 and q("libHistoryModel.get(0).name") == "Older Album", "Albums filter wrong")
    check(q("libTabsFlow.children[1].on") is True and q("libTabsFlow.children[0].on") is False, "Albums chip not lit")
    shot("history_filtered.png")
    q("historyFilter('', true)")
    settle(300)
    check(q("libHistoryModel.get(0).name") == "Older Album" and q("libSortBox.currentIndex") == 1, "Oldest first wrong")
    q("historyFilter('', false)")
    settle(300)

    # 3c. A list scrolled past the first page keeps its rows when a row settles.
    for qid in range(3, 63):
        store.record_history("L", qid, {"kind": "track", "name": f"Filler {qid}", "saved": 1, "total": 1, "tracks": []})
    bridge.historyChanged.emit("")
    settle(400)
    q("historyLoad(libHistoryModel.count)")
    settle(400)
    check(q("libHistoryModel.count") == 62, f"second page not appended, got {q('libHistoryModel.count')}")
    # A download settling while the list is scrolled past the first page
    # adds its one row on top and keeps the rest.
    store.record_history("L", 63, {"kind": "track", "name": "Just Landed", "saved": 1, "total": 1, "tracks": []})
    bridge.historyChanged.emit("L:63")
    settle(400)
    check(q("libHistoryModel.count") == 63, f"a settled download left {q('libHistoryModel.count')} rows")
    check(q("libHistoryModel.get(0).name") == "Just Landed", "a settled download is not on top")
    # A late track rewrites its entry in place: same row, same spot.
    store.record_history(
        "L", 30, {"kind": "track", "name": "Rewritten", "saved": 1, "total": 1, "tracks": []}, keep_time=True
    )
    bridge.historyChanged.emit("L:30")
    settle(400)
    check(q("libHistoryModel.count") == 63, f"a rewritten entry left {q('libHistoryModel.count')} rows")
    check(q("libHistoryModel.get(0).name") == "Just Landed", "a rewritten entry jumped to the top")
    # Back from an album reopens the page; it keeps every row already shown.
    q("loadLib('history')")
    settle(400)
    check(q("libHistoryModel.count") == 63, f"reopening changed the list to {q('libHistoryModel.count')}")
    # The library refresh timer must not ask TIDAL for "history" (that
    # answered with an empty page and wiped the list).
    bridge._set_status("")
    q("libraryFreshTimer.triggered()")
    settle(400)
    check(q("waves.status") == "", f"refresh timer loaded history as a TIDAL category: {q('waves.status')!r}")
    check(q("libHistoryModel.count") == 63, f"refresh timer cut the list to {q('libHistoryModel.count')}")

    # 3d. The search box finds an entry by name, artist or track title, and
    # the sort hides with nothing to order.
    check(
        bool(q("historySearchBox.visible")) and q("libSortBox.opacity") == 1,
        "search or sort missing on a filled history",
    )
    store.record_history(
        "L",
        900,
        {
            "kind": "album",
            "name": "Björk Sessions",
            "artist": "Some Band",
            "saved": 1,
            "total": 2,
            "tracks": [
                {"id": "1", "title": "Hidden Gem", "status": "done"},
                {"id": "2", "title": "Other", "status": "failed"},
            ],
        },
    )
    for needle, why in (("BJÖRK", "name, any case"), ("some band", "artist"), ("gem hidden", "track title words")):
        q(f"historySearchField.text = '{needle}'")
        settle(600)
        check(
            q("libHistoryModel.count") == 1 and q("libHistoryModel.get(0).name") == "Björk Sessions",
            f"search by {why} found {q('libHistoryModel.count')} rows",
        )
    for needle in ("title", "done", "zzzz"):  # the log's own field names and words are not searched
        q(f"historySearchField.text = '{needle}'")
        settle(600)
        check(q("libHistoryModel.count") == 0, f"search for {needle!r} matched {q('libHistoryModel.count')} rows")
    check(bool(q("historyEmpty.visible")) and "matches" in q("historyEmpty.text"), "no line for a search with no match")
    check(
        bool(q("historySearchBox.visible")) and q("libSortBox.opacity") == 0,
        "sort shown, or search gone, with no match",
    )
    q("historySearchField.text = ''")
    settle(400)
    check(q("libHistoryModel.count") >= 50, "emptying the search did not bring the list back")

    # 3d2. The chips never wrap to make room for the search box. In a window
    # too narrow for both, the box folds to its magnifier; opened, it takes
    # the chips' room, and emptied and left it gives the room back.
    one_line = q("libTabsFlow.height")
    check(
        not bool(q("historySearchBox.folded")) and q("historySearchBox.width") == 170,
        "search box not full size in a wide window",
    )
    # The chips are one row at every width, tighten before the search box
    # folds, and are never cut off, down to the window's minimum width (and
    # past it, to 960, so the folded search is exercised too).
    floor = q("root.minimumWidth")
    check(floor <= 1200, f"window minimum width grew to {floor}: widen the sweep")
    for width in (1300, 1200, floor, 1000, 960):
        root.setWidth(width)
        settle(300)
        check(q("libTabsFlow.height") == one_line, f"the chips wrapped at {width}")
        check(
            q("libTabsFlow.width") <= q("libTabsFlow.parent.width") + 0.5,
            f"the chips were cut off at {width}: {q('libTabsFlow.width')} > {q('libTabsFlow.parent.width')}",
        )
        if bool(q("historySearchBox.folded")):
            check(bool(q("libTabsFlow.tight")), f"the search box folded before the chips tightened at {width}")
    check(
        bool(q("historySearchBox.folded")) and q("historySearchBox.width") == 40,
        "search box not folded at 960",
    )
    shot("history_narrow_folded.png")
    q("historySearchFoldMa.clicked(null)")
    settle(300)
    check(bool(q("historySearchField.activeFocus")), "the magnifier did not open the search box")
    check(
        not bool(q("libTabsFlow.parent.visible")) and q("historySearchBox.width") > 170,
        "an open search did not take the chips' room",
    )
    q("historySearchField.text = 'Björk'")
    settle(600)
    check(q("libHistoryModel.count") == 1, "the search opened from the magnifier did not search")
    shot("history_narrow_open.png")
    # Left with the search still in it, the box folds back (magnifier lit) and
    # the chips filter what the search found.
    q("historySearchField.focus = false")
    settle(400)
    check(
        bool(q("libTabsFlow.parent.visible")) and bool(q("historySearchBox.folded")),
        "a search left alone kept the chips covered",
    )
    check(q("libHistoryModel.count") == 1, "leaving the search box dropped the search")
    q("historyFilter('track', false)")
    settle(400)
    check(q("libHistoryModel.count") == 0, "the Tracks chip did not narrow the search")
    q("historyFilter('album', false)")
    settle(400)
    check(q("libHistoryModel.count") == 1, "the Albums chip lost the searched album")
    shot("history_narrow_search_kept.png")
    q("historyFilter('', false)")
    q("historySearchFoldMa.clicked(null)")
    settle(300)
    check(
        q("historySearchField.text") == "Björk" and not bool(q("libTabsFlow.parent.visible")),
        f"reopening did not show the search: text={q('historySearchField.text')!r} focus={q('historySearchField.activeFocus')} asked={q('historySearchBox.asked')} folded={q('historySearchBox.folded')}",
    )
    q("historySearchField.text = ''")
    q("historySearchField.focus = false")
    settle(400)
    check(
        bool(q("libTabsFlow.parent.visible")) and bool(q("historySearchBox.folded")),
        "a closed search did not give the chips back",
    )
    check(q("libTabsFlow.height") == one_line, "the chips came back wrapped")
    root.setWidth(1280)
    settle(300)
    check(
        not bool(q("historySearchBox.folded")) and q("libTabsFlow.height") == one_line,
        "the wide layout did not come back",
    )

    # 3e. A whole long history: every entry is reachable by scrolling, a
    # refresh keeps them all, and neither stalls the window.

    tracks = [{"id": str(n), "title": f"Track {n}", "status": "done", "saved_at": 1} for n in range(12)]
    with store._lock:
        for qid in range(1000, 4000):
            store._conn.execute(
                "INSERT INTO history (launch, qid, finished_at, kind, name, saved, total, tracks, search) VALUES ('B', ?, ?, 'album', ?, 12, 12, ?, ?)",
                (qid, qid, f"Bulk {qid}", json.dumps(tracks), f"bulk {qid}"),
            )
        store._conn.commit()
    total = len(store.history_page(0, 100000)[0])

    def pump(ms: int) -> float:
        """Run the event loop for ``ms`` and return the longest single stall."""
        worst, end = 0.0, time.monotonic() + ms / 1000
        while time.monotonic() < end:
            t0 = time.monotonic()
            QCoreApplication.processEvents()
            worst = max(worst, time.monotonic() - t0)
            time.sleep(0.002)
        return worst

    bridge.historyChanged.emit("")
    pump(400)
    worst_page = 0.0
    for _ in range(total // 50 + 5):
        if not q("libCatHasMore['history']"):
            break
        q("libHistoryList.positionViewAtEnd()")
        worst_page = max(worst_page, pump(120))
    check(q("libHistoryModel.count") == total, f"scrolling reached {q('libHistoryModel.count')} of {total} entries")
    check(worst_page < 0.1, f"loading a page stalled the window {worst_page * 1000:.0f} ms")
    store.record_history("B", 5000, {"kind": "album", "name": "Lands Last", "saved": 1, "total": 1, "tracks": []})
    bridge.historyChanged.emit("B:5000")
    worst_refresh = pump(800)
    check(
        q("libHistoryModel.count") == total + 1, f"a download settling left {q('libHistoryModel.count')} of {total + 1}"
    )
    check(
        worst_refresh < 0.1,
        f"a download settling over {total} shown entries stalled the window {worst_refresh * 1000:.0f} ms",
    )
    q("loadLib('history')")
    worst_reopen = pump(400)
    check(q("libHistoryModel.count") == total + 1, f"reopening a long list left {q('libHistoryModel.count')}")
    check(worst_reopen < 0.1, f"reopening a long list stalled the window {worst_reopen * 1000:.0f} ms")
    print(
        f"long history: {total} entries, worst page {worst_page * 1000:.0f} ms, worst refresh {worst_refresh * 1000:.0f} ms",
        flush=True,
    )

    # 3f. An album opened from a history row keeps QUEUE lit, not My Tidal.
    check(q("navOrigin") == "history", f"history page origin is {q('navOrigin')}")

    # 4. Clear asks first, then empties.
    q("historyClearBtn.clicked()")
    settle(150)
    check(bool(q("historyClearGate.visible")), "CLEAR HISTORY did not ask first")
    shot("history_clear_confirm.png")
    q("historyClearConfirm.clicked()")
    settle(500)
    check(not bool(q("historyClearGate.visible")), "confirm stayed open")
    check(q("libHistoryModel.count") == 0, "history not emptied")
    check(store.history_page(0, 50) == ([], False), "store not emptied")

    # 5. My Tidal from here lands on Home.
    q("openLibrary()")
    settle(200)
    check(q("libraryCategory") == "home", f"My Tidal landed on {q('libraryCategory')}")

    for f in failures:
        print("FAIL:", f, flush=True)
    print(f"checks failed: {len(failures)}", flush=True)
    return _EXIT_OK if not failures else _EXIT_REGRESSED


if __name__ == "__main__":
    raise SystemExit(_run_scenario())
