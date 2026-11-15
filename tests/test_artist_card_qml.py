"""The artist card on the real Main.qml.

WHAT THIS FENCES OFF
--------------------
Resting the pointer on an artist name that links to a page opens a card
(after 450 ms, never before) with a short summary and a Follow button. It
opens at once on a skeleton, asks the bridge for the summary once, and fills
in when artistSummaryReady lands; past 2.5 s with nothing it shows a short
note instead, and a late summary still fills it. It sits inside the window,
under 60% of its height, above a name near the bottom and below one near the
top. Moving to the next name switches it in about 200 ms. Follow reads
FOLLOW, FOLLOWING, UNFOLLOW under the pointer, and an outside follow change
reaches the open card; an unfollow takes that artist's releases off the feed
at once. The "..." menu plays a preview, downloads a track or
an album, and goes to the album page (the card closes, the track is
highlighted), or lights the track on the album already open. Esc closes
the menu first, then the card. A name still moving under the pointer (a
list coasting) waits to open, and the page of the card's artist landing
closes it. A name that goes away takes the card with it. Signed out, the
featured artists card open, or the switch off: no card. No binding loops.

Runs in a SUBPROCESS like the other Main.qml scenarios: building the bridge
installs process-global handlers that must not leak into the suite.
"""

from __future__ import annotations

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

_CREDITS = '[{name:"Base Notion",id:"11"},{name:"Kira Moll",id:"12"}]'


def _summary(aid: str, name: str) -> dict:
    return {
        "id": aid,
        "known": True,
        "name": name,
        "art": "",
        "bio": "A sample biography. " * 30,
        "albums": 3,
        "eps": 1,
        "singles": 4,
        "top": [
            {
                "id": f"{aid}0{k + 1}",
                "title": f"Top song {k}",
                "duration": "3:20",
                "duration_sec": 200,
                "album": f"Album {k}",
                "album_id": f"{aid}9{k}",
                "art": "",
            }
            for k in range(3)
        ],
        "releases": [
            {"id": f"{aid}8{k}", "title": f"Record {k}", "year": 2020 - k, "art": "", "rtype": "album"}
            for k in range(4)
        ],
        "popularity": -1,
    }


def test_the_artist_card_opens_fills_and_acts():
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    sandbox = tempfile.mkdtemp(prefix="waves-artist-card-test-")
    env["HOME"] = sandbox
    env["XDG_CONFIG_HOME"] = str(Path(sandbox) / ".config")
    proc = subprocess.run(  # (fixed argv: this file, one flag)
        [sys.executable, str(Path(__file__).resolve()), "--run-scenario"],
        env=env,
        capture_output=True,
        text=True,
        timeout=240,
    )
    tail = "\n".join((proc.stdout + proc.stderr).strip().splitlines()[-24:])
    import pytest

    if proc.returncode == _EXIT_NO_QT:
        pytest.skip("PySide6 / offscreen Qt unavailable")
    if proc.returncode == _EXIT_PRECONDITION:
        pytest.skip(f"could not set up the scenario in this environment:\n{tail}")
    assert proc.returncode == _EXIT_OK, f"the artist card regressed. Scenario exit={proc.returncode}:\n{tail}"


def _run_scenario() -> int:  # (one linear scenario)
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    try:
        from PySide6.QtCore import QEventLoop, QPoint, QPointF, Qt, QTimer, QUrl, qInstallMessageHandler
        from PySide6.QtGui import QGuiApplication
        from PySide6.QtQml import QQmlApplicationEngine, QQmlEngine, QQmlExpression
        from PySide6.QtQuick import QQuickItem
        from PySide6.QtTest import QTest
        from shiboken6 import getCppPointer, wrapInstance
    except Exception as exc:
        print(f"Qt unavailable: {exc}", file=sys.stderr)
        return _EXIT_NO_QT

    from _qml_offline import PARK_LOGIN_QML, patch_offline

    loops: list[str] = []
    errors: list[str] = []

    def on_message(_mode, _ctx, msg) -> None:
        if "Binding loop" in msg or "binding loop" in msg:
            loops.append(msg)
        if "TypeError" in msg or "ReferenceError" in msg:
            errors.append(msg)

    qInstallMessageHandler(on_message)
    patch_offline()
    app = QGuiApplication.instance() or QGuiApplication([])
    try:
        from waves.waves_ui.app import _load_mono
        from waves.waves_ui.backend import WavesBridge
    except Exception as exc:
        print(f"Qt platform/backend unavailable: {exc}", file=sys.stderr)
        return _EXIT_NO_QT

    summaries: dict[str, dict] = {}
    followed: set[str] = set()
    calls: list[tuple] = []

    def rec(name):
        return lambda self, *a: calls.append((name, *[str(x) for x in a]))

    WavesBridge._library_root = lambda self: ""  # type: ignore[method-assign]
    WavesBridge.loadBrowse = lambda self: None  # type: ignore[method-assign]
    WavesBridge.refreshBrowse = lambda self: None  # type: ignore[method-assign]
    WavesBridge.prefetchArtist = lambda self, *a: None  # type: ignore[method-assign]
    WavesBridge.artistSummary = lambda self, aid: dict(summaries.get(str(aid), {"id": str(aid), "known": False}))  # type: ignore[method-assign]
    WavesBridge.requestArtistCard = rec("request")  # type: ignore[method-assign]
    WavesBridge.isFollowed = lambda self, aid: str(aid) in followed  # type: ignore[method-assign]
    WavesBridge.followArtist = rec("follow")  # type: ignore[method-assign]
    WavesBridge.unfollowArtist = rec("unfollow")  # type: ignore[method-assign]
    WavesBridge.previewTrack = rec("preview")  # type: ignore[method-assign]
    WavesBridge.downloadTrack = rec("dl_track")  # type: ignore[method-assign]
    WavesBridge.downloadAlbum = rec("dl_album")  # type: ignore[method-assign]
    WavesBridge.openBrowseItem = rec("open_item")  # type: ignore[method-assign]
    WavesBridge.loadArtist = rec("load_artist")  # type: ignore[method-assign]
    WavesBridge.artistLibraryPresence = lambda self, name: (  # type: ignore[method-assign]
        {"present": True, "albums": 2, "tracks": 9} if name == "Base Notion" else {"present": False}
    )

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

    def q(expr: str):
        e = QQmlExpression(QQmlEngine.contextForObject(root), root, expr)
        r = e.evaluate()
        if e.hasError():
            raise RuntimeError(f"{expr}: {e.error().toString()}")
        if isinstance(r, tuple):
            r = r[0]
        return r.toVariant() if hasattr(r, "toVariant") else r

    def settle(ms: int = 150) -> None:
        loop = QEventLoop()
        QTimer.singleShot(ms, loop.quit)
        loop.exec()

    failures: list[str] = []

    def check(cond, what: str) -> None:
        if not cond:
            failures.append(what)

    def center(item: str) -> QPoint:
        x = q(f"{item}.mapToItem(null, {item}.width / 2, {item}.height / 2).x")
        y = q(f"{item}.mapToItem(null, {item}.width / 2, {item}.height / 2).y")
        return QPoint(int(x), int(y))

    def card_items():
        top = q("ahcCard.contentItem")
        if top is None:
            return
        # The expression hands back a plain QObject wrapper: view it as the
        # item it is to walk the visual tree.
        stack = [wrapInstance(getCppPointer(top)[0], QQuickItem)]
        while stack:
            it = stack.pop()
            yield it
            stack.extend(it.childItems())

    def find(name: str):
        for it in card_items():
            if it.objectName() == name:
                return it
        return None

    def at(name: str) -> QPoint:
        it = find(name)
        if it is None:
            raise RuntimeError(f"no {name} in the card")
        p = it.mapToScene(QPointF(it.width() / 2, it.height() / 2))
        return QPoint(int(p.x()), int(p.y()))

    def click(p: QPoint) -> None:
        QTest.mouseMove(root, p)
        settle(40)
        QTest.mouseClick(root, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, p)

    def name_offset() -> float:
        # The name's y inside the card (not the scene: the card itself may move).
        it = find("ahcName")
        if it is None:
            raise RuntimeError("no ahcName in the card")
        return float(it.mapToScene(QPointF(0, 0)).y()) - float(q("ahcCard.y"))

    def park() -> None:
        QTest.mouseMove(root, QPoint(5, 5))

    def name_at(i: int) -> str:
        return f"npArtistsRow.children[{i}].children[0]"

    root.resize(1280, 900)
    root.setVisible(True)
    settle(300)
    q("bootOverlay.done = true")
    q("bootContentShown = 1")
    q(PARK_LOGIN_QML)
    q("termsGate.visible = false")
    q("ffmpegGate.visible = false")

    def now_playing() -> None:
        # The bar's two names; a preview the card starts replaces them, so
        # the scenario puts them back after one.
        q('root.previewKind = "track"')
        q('root.previewId = "t1"')
        q("root.previewLoading = false")
        q("root.previewError = ''")
        q('root.previewNowTitle = "Sample track title"')
        q('root.previewNowArtist = "Base Notion, Kira Moll"')
        q('root.previewNowArtistId = "11"')
        q(f"root.previewNowArtists = {_CREDITS}")
        q("root.previewPlaying = true")
        q("root.previewDuration = 213000")
        q("root.previewPosition = 77000")
        settle(300)

    now_playing()
    if not q("nowPlaying.visible") or int(q("npArtist.shown")) < 2:
        print("the now-playing row did not show both names", file=sys.stderr)
        return _EXIT_PRECONDITION

    # 1. Signed out: resting on a name opens nothing.
    QTest.mouseMove(root, center(name_at(0)))
    settle(700)
    check(not q("ahcCard.visible"), "a card opened while signed out")
    park()
    settle(300)

    bridge._logged_in = True
    bridge.loggedInChanged.emit()
    settle(100)
    q("termsGate.visible = false")
    q("ffmpegGate.visible = false")
    settle(100)
    if not q("root.signedIn"):
        print("could not mark the bridge signed in", file=sys.stderr)
        return _EXIT_PRECONDITION

    # 2. Signed in: closed at 250 ms, open by 600 ms on the skeleton, one ask.
    calls.clear()
    QTest.mouseMove(root, center(name_at(0)))
    settle(250)
    check(not q("ahcCard.visible"), "the card opened before the 450 ms rest")
    settle(400)
    check(bool(q("ahcCard.visible")), "the card did not open after the rest")
    check(calls.count(("request", "11")) == 1, f"the card did not ask for its summary once: {calls}")
    check(bool(q("root.ahcSkeleton")) and find("ahcSkel").isVisible(), "an unknown artist did not open on the skeleton")
    check(q("root.ahcNameText") == "Base Notion", f"the card names {q('root.ahcNameText')!r}")
    # The skeleton is shaped like the card: the stats line, the picture and
    # every part of the body hold a placeholder of their own size.
    for part in ("ahcStatsGhost", "ahcPicGhost"):
        check(find(part) is not None and find(part).isVisible(), f"no {part} while loading")
    skel_h = float(q("ahcScroll.contentH"))
    skel_name_y = name_offset()
    skel_bottom = float(q("ahcCard.y + ahcCard.height"))

    # 3. The summary lands: the facts replace the skeleton, and the card
    # keeps its size (the old bar skeleton grew by some 250 px here), its
    # name where it was, and its edge on the name's side where it was.
    summaries["11"] = _summary("11", "Base Notion")
    bridge.artistSummaryReady.emit("11", summaries["11"])
    settle(400)
    check(bool(q("root.ahcKnown")) and not q("root.ahcSkeleton"), "the summary did not fill the card")
    full_h = float(q("ahcScroll.contentH"))
    check(abs(full_h - skel_h) <= 40, f"the card jumped from {skel_h} to {full_h} px when it filled")
    name_y = name_offset()
    check(abs(name_y - skel_name_y) <= 1, f"the name moved from {skel_name_y} to {name_y} when the card filled")
    check(
        abs(float(q("ahcCard.y + ahcCard.height")) - skel_bottom) <= 1,
        "the card's edge by the name moved when it filled",
    )
    for part in ("ahcStatsGhost", "ahcPicGhost", "ahcSkel"):
        check(find(part) is None or not find(part).isVisible(), f"{part} still shows once filled")
    check(q("root.ahcStatsText()") == "3 ALBUMS · 1 EP", f"the stats read {q('root.ahcStatsText()')!r}")
    check(find("ahcLib") is not None and find("ahcLib").isVisible(), "the in library chip is missing")
    check(find("ahcTrack_1101") is not None, "the top tracks did not show")
    check(find("ahcCover_1180") is not None, "the cover strip did not show")
    check(find("ahcBioMore") is not None and find("ahcBioMore").isVisible(), "a long bio has no MORE")

    # Inside the window, under 60% of it, above a name in the bar.
    cx, cy, cw, ch = (q(f"ahcCard.{k}") for k in ("x", "y", "width", "height"))
    check(
        cx >= 8 and cy >= 8 and cx + cw <= 1272 and cy + ch <= 892, f"the card leaves the window: {cx},{cy} {cw}x{ch}"
    )
    check(ch <= round(900 * 0.6) + 1, f"the card is taller than 60% of the window: {ch}")
    name_top = q(f"{name_at(0)}.mapToItem(null, 0, 0).y")
    check(q("root.ahcSide") == "above" and cy + ch <= name_top, "the card is not above a name in the bar")

    # 4. Popularity arrives on its own signal.
    bridge.artistMetaLoaded.emit("11", 64)
    settle(100)
    check(int(q("root.ahcPop")) == 64 and find("ahcPop").isVisible(), "the popularity meter did not show")

    # 5. Follow: FOLLOW, then UNFOLLOW under the pointer once followed.
    QTest.mouseMove(root, at("ahcFollow"))
    settle(400)
    check(bool(q("ahcCard.visible")), "moving from the name onto the card closed it")
    check(find("ahcFollowText").property("text") == "FOLLOW", "the button does not read FOLLOW")
    click(at("ahcFollow"))
    settle(80)
    check(("follow", "11", "Base Notion", "") in calls, f"FOLLOW did not follow: {calls}")
    followed.add("11")
    bridge.followStateChanged.emit("11", True)
    settle(150)
    check(
        find("ahcFollowText").property("text") == "UNFOLLOW",
        "a followed artist under the pointer does not read UNFOLLOW",
    )
    click(at("ahcFollow"))
    settle(80)
    check(("unfollow", "11") in calls, "UNFOLLOW did not unfollow")
    # The unfollowed artist's cards leave the releases feed (and a search
    # on it) at once, not after the reload debounce; another follow's stay.
    q(
        'root.releasesRows = [{id:"r1", via:"11", title:"Gone"}, {id:"r2", via:"12", title:"Kept"}],'
        ' root.relFindRows = [{id:"r1", via:"11", title:"Gone"}]'
    )
    followed.discard("11")
    bridge.followStateChanged.emit("11", False)
    check(
        [r["id"] for r in q("root.releasesRows")] == ["r2"] and len(q("root.relFindRows")) == 0,
        "an unfollowed artist's releases stayed on the releases page",
    )
    settle(150)

    # 6. The menu: play a preview, download, Esc closes the menu first.
    QTest.mouseMove(root, at("ahcTrack_1101"))
    settle(120)
    click(at("ahcMore_1101"))
    settle(120)
    check(bool(q("root.ahcMenuOpen")), "the ... button did not open the menu")
    click(at("ahcMenu_play"))
    settle(120)
    check(("preview", "1101") in calls, f"PLAY PREVIEW did not preview the track: {calls}")
    now_playing()
    click(at("ahcMore_1101"))
    settle(120)
    click(at("ahcMenu_down"))
    settle(120)
    check(("dl_track", "1101") in calls, "DOWNLOAD did not download the track")
    QTest.mouseMove(root, at("ahcCover_1180"))
    settle(120)
    click(at("ahcMore_1180"))
    settle(120)
    click(at("ahcMenu_down"))
    settle(120)
    check(("dl_album", "1180") in calls, "DOWNLOAD on a cover did not download the album")
    click(at("ahcMore_1101"))
    settle(120)
    root.requestActivate()
    QTest.keyClick(root, Qt.Key.Key_Escape)
    settle(120)
    check(not q("root.ahcMenuOpen") and bool(q("ahcCard.visible")), "the first Esc did not close only the menu")
    QTest.keyClick(root, Qt.Key.Key_Escape)
    settle(250)
    check(not q("ahcCard.visible"), "the second Esc did not close the card")

    # 7. GO TO ALBUM: the card closes, the album opens with the track lit.
    QTest.mouseMove(root, center(name_at(0)))
    settle(650)
    check(bool(q("ahcCard.visible")), "the card did not open again")
    QTest.mouseMove(root, at("ahcTrack_1101"))
    settle(120)
    click(at("ahcMore_1101"))
    settle(120)
    click(at("ahcMenu_album"))
    settle(300)
    check(not q("ahcCard.visible"), "GO TO ALBUM left the card open")
    check(q("browsePageKey") == "item:album:1190", f"GO TO ALBUM opened {q('browsePageKey')!r}")
    check(q("browseHighlightId") == "1101", "GO TO ALBUM did not highlight the track")
    park()
    settle(300)

    # 7b. GO TO ALBUM for the album already open: no second open and no Back
    # entry, but the track is still lit (it used to close the card and do
    # nothing at all).
    q('root.browseHighlightId = ""')
    opens = calls.count(("open_item", "album", "1190"))
    QTest.mouseMove(root, center(name_at(0)))
    settle(650)
    check(bool(q("ahcCard.visible")), "the card did not open over the album page")
    QTest.mouseMove(root, at("ahcTrack_1101"))
    settle(120)
    click(at("ahcMore_1101"))
    settle(120)
    click(at("ahcMenu_album"))
    settle(300)
    check(not q("ahcCard.visible"), "GO TO ALBUM on the open album left the card open")
    check(q("browseHighlightId") == "1101", "GO TO ALBUM on the open album lit nothing")
    check(calls.count(("open_item", "album", "1190")) == opens, "GO TO ALBUM fetched the open album again")
    park()
    settle(300)

    # 8. The next name switches the card in about 200 ms.
    summaries["12"] = _summary("12", "Kira Moll")
    QTest.mouseMove(root, center(name_at(0)))
    settle(650)
    QTest.mouseMove(root, center(name_at(1)))
    settle(300)
    check(bool(q("ahcCard.visible")) and q("root.ahcId") == "12", f"the card did not switch names: {q('root.ahcId')!r}")
    check(not (find("ahcLib") and find("ahcLib").isVisible()), "the in library chip stayed for an artist not on disk")
    park()
    settle(500)
    check(not q("ahcCard.visible"), "leaving the name did not close the card")

    # 8b. A gap between two names (a comma, a grid gutter): the pointer
    # takes longer than the card's switch to cross it, and the next name
    # still gets its card instead of the leave timer closing both.
    QTest.mouseMove(root, center(name_at(0)))
    settle(650)
    check(bool(q("ahcCard.visible")) and q("root.ahcId") == "11", "the card did not open before the gap check")
    park()
    settle(60)
    QTest.mouseMove(root, center(name_at(1)))
    settle(450)
    check(
        bool(q("ahcCard.visible")) and q("root.ahcId") == "12",
        f"crossing a gap to the next name closed the card for good (open {q('ahcCard.visible')}, {q('root.ahcId')!r})",
    )

    # 8c. A card scrolled down: the next card opens at its top.
    find("ahcScrollFl").setProperty("contentY", 40)
    park()
    settle(500)
    QTest.mouseMove(root, center(name_at(0)))
    settle(650)
    check(bool(q("ahcCard.visible")), "the card did not open for the scroll check")
    y0 = find("ahcScrollFl").property("contentY")
    check(y0 == 0, f"a new card opened scrolled ({y0} px down)")
    park()
    settle(500)

    # 8d. A short window and a name mid height: the card fits on neither
    # side, so it shrinks to the roomier one and never covers the name.
    root.resize(1280, 700)
    settle(200)
    q(
        '(function () { var t = Qt.createQmlObject(\'import QtQuick; Text { objectName: "midName"; x: 300; y: 300;'
        ' text: "Mid Name"; textFormat: Text.PlainText }\', root.contentItem);'
        ' root.artistCardFor(t, "11", "Base Notion", ""); return true })()'
    )
    settle(650)
    check(bool(q("ahcCard.visible")), "the card did not open for a name mid height")
    oy = q("root.ahcShownFor.mapToItem(null, 0, 0).y")
    ob = q("root.ahcShownFor.mapToItem(null, 0, root.ahcShownFor.height).y")
    cy, ch = q("ahcCard.y"), q("ahcCard.height")
    check(cy >= ob or cy + ch <= oy, f"the card covers its name: card {cy}..{cy + ch}, name {oy}..{ob}")
    check(cy >= 8 and cy + ch <= 692, f"the shrunk card leaves the window: {cy}..{cy + ch}")
    q("root.ahcShownFor.visible = false")
    settle(250)
    root.resize(1280, 900)
    settle(200)

    # 8e. A click on the name during the rest opens their page: the card
    # that comes up after it does not land on that page.
    QTest.mouseMove(root, center(name_at(0)))
    settle(200)
    q('root.artistData = ({ id: "11", name: "Base Notion" }); root.artistOpen = true')
    settle(450)
    check(not q("ahcCard.visible"), "a card opened over the page of the artist just clicked")
    q("root.artistOpen = false")
    park()
    settle(300)

    # 8f. A list coasting to a stop under a still pointer: the name moves
    # through the rest, and the card waits for it to hold still rather than
    # opening for the one frame before its moved name closes it.
    q(
        '(function () { var t = Qt.createQmlObject(\'import QtQuick; Text { objectName: "coastName"; x: 300; y: 300;'
        ' text: "Coast Name"; textFormat: Text.PlainText }\', root.contentItem);'
        ' root.artistCardFor(t, "11", "Base Notion", ""); return true })()'
    )
    settle(300)
    q("root.ahcOwner.y = 310")
    settle(200)
    check(not q("ahcCard.visible"), "the card opened on a name still moving under the pointer")
    settle(250)
    check(bool(q("ahcCard.visible")), "the card did not open once the name held still")
    q("root.ahcShownFor.visible = false")
    settle(250)

    # 8g. The page of the card's artist lands (a click on the name while the
    # card came up) with the pointer still on the name: the card goes.
    QTest.mouseMove(root, center(name_at(0)))
    settle(650)
    check(bool(q("ahcCard.visible")) and q("root.ahcId") == "11", "the card did not open before the page check")
    bridge.artistLoaded.emit({"id": "11", "name": "Base Notion"})
    settle(150)
    check(not q("ahcCard.visible"), "the card stayed up over the page of its own artist")
    q("root.artistOpen = false")
    park()
    settle(300)

    # 9. A name near the top: below it. Hiding the name closes the card.
    q(
        "(function () { var t = Qt.createQmlObject('import QtQuick; Text { x: 300; y: 120;"
        ' text: "Top Name"; textFormat: Text.PlainText }\', root.contentItem);'
        ' root.artistCardFor(t, "33", "Late Artist", ""); return true })()'
    )
    settle(650)
    check(bool(q("ahcCard.visible")), "the card did not open for a name near the top")
    owner_bottom = q("root.ahcShownFor.mapToItem(null, 0, root.ahcShownFor.height).y")
    check(q("root.ahcSide") == "below" and q("ahcCard.y") >= owner_bottom, "the card is not below a name near the top")

    # 10. Nothing comes for 2.5 s: a short note; a late summary still fills it.
    settle(2600)
    check(not q("root.ahcSkeleton") and find("ahcMissing").isVisible(), "no note after 2.5 s without a summary")
    settle(250)
    for slot in ("ahcBioSlot", "ahcTopSlot", "ahcRelSlot"):
        check(find(slot) is not None and find(slot).height() == 0, f"{slot} still holds its placeholder after the wait")
    bridge.artistSummaryReady.emit("33", _summary("33", "Late Artist"))
    settle(200)
    check(bool(q("root.ahcKnown")) and not find("ahcMissing").isVisible(), "a late summary did not fill the card")
    q("root.ahcShownFor.visible = false")
    settle(250)
    check(not q("ahcCard.visible"), "the card outlived its name")

    # 11. The featured artists card open: no card, and it closes an open one.
    QTest.mouseMove(root, center(name_at(0)))
    settle(650)
    check(bool(q("ahcCard.visible")), "the card did not open before the block check")
    q("root.nowCardOpen = true; root.nowCardAnchor = npMore")
    settle(150)
    check(not q("ahcCard.visible"), "opening the featured artists card left the artist card open")
    q("root.nowCardOpen = false")
    park()
    settle(300)

    # 12. The Settings switch off (the real pref and its signal): an open
    # card closes, and resting on a name opens none.
    QTest.mouseMove(root, center(name_at(0)))
    settle(700)
    check(bool(q("ahcCard.visible")), "the card did not open before the switch check")
    bridge.setWavesPref("artist_hover_cards", False)
    settle(150)
    check(not q("root.artistCards"), "the QML did not re-read the switch on its signal")
    check(not q("ahcCard.visible"), "turning the switch off left the card open")
    park()
    settle(300)
    QTest.mouseMove(root, center(name_at(0)))
    settle(700)
    check(not q("ahcCard.visible"), "a card opened with the switch off")
    bridge.setWavesPref("artist_hover_cards", True)
    settle(50)
    check(bool(q("root.artistCards")), "the switch did not come back on")
    park()
    settle(200)

    check(not loops, f"binding loops: {loops[:3]}")
    check(not errors, f"QML errors: {errors[:3]}")
    if failures:
        for f in failures:
            print("FAIL:", f, file=sys.stderr)
        return _EXIT_REGRESSED
    print("ok")
    return _EXIT_OK


if __name__ == "__main__" and "--run-scenario" in sys.argv:
    sys.exit(_run_scenario())
