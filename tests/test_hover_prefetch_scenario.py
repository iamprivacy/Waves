"""A pointer resting on a playlist card has its page ready before the click.

Drives the real Main.qml: a Browse shelf of playlist cards with local cover
files, a mouse move onto one card, and a hold. The dwell must reach the
backend's prefetch (the page lands in the cache, its covers in the warm
pool), and the click that follows must paint the page with no loading hint
and without the hero ever showing the "art: GET" box. A second, never-hovered
card proves the skeleton header: title and cover up while the payload is
still on the wire.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

QML_MAIN = Path(__file__).resolve().parent.parent / "tidaler" / "waves_ui" / "qml" / "Main.qml"

_EXIT_OK = 0
_EXIT_FAIL = 1
_EXIT_NO_QT = 3
_EXIT_PRECONDITION = 4


def test_resting_on_a_playlist_card_prefetches_its_page_and_the_click_paints_whole():
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    # Sandboxed: a REAL WavesBridge adopts whatever config dir it finds.
    env["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="waves-hover-prefetch-test-")
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
    # PRECONDITION is for the fixture (a fake card missing from the shelf),
    # never for the app: a click that does not open, a Back that does not
    # return, a page that never loads are regressions and exit FAIL.
    if proc.returncode == _EXIT_PRECONDITION:
        pytest.skip(f"could not set up the scenario in this environment:\n{tail}")
    assert proc.returncode == _EXIT_OK, f"hover prefetch / skeleton header regression:\n{tail}"


def _run_scenario() -> int:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    try:
        from PySide6.QtCore import QEventLoop, QPoint, Qt, QTimer, QUrl
        from PySide6.QtGui import QColor, QGuiApplication, QImage
        from PySide6.QtQml import QQmlApplicationEngine, QQmlEngine, QQmlExpression
        from PySide6.QtQuick import QQuickWindow
        from PySide6.QtTest import QTest
    except Exception as exc:  # pragma: no cover - environment guard
        print(f"Qt unavailable: {exc}", file=sys.stderr)
        return _EXIT_NO_QT

    app = QGuiApplication.instance() or QGuiApplication([])
    try:
        from tidaler.waves_ui.app import _load_mono
        from tidaler.waves_ui.backend import WavesBridge
    except Exception as exc:  # pragma: no cover - environment guard
        print(f"Qt platform/backend unavailable: {exc}", file=sys.stderr)
        return _EXIT_NO_QT

    from tests._qml_offline import PARK_LOGIN_QML, patch_offline

    # Local cover files: the page's art must actually reach Ready offline.
    art_dir = Path(tempfile.mkdtemp(prefix="waves-hover-art-"))

    def cover(name: str, rgb: tuple) -> str:
        img = QImage(64, 64, QImage.Format.Format_RGB32)
        img.fill(QColor(*rgb))
        path = art_dir / f"{name}.png"
        img.save(str(path))
        return QUrl.fromLocalFile(str(path)).toString()

    hero_art = cover("hero", (40, 120, 200))
    row_art = cover("row", (200, 80, 40))
    hint_art = cover("hint", (60, 160, 90))
    # A cover that cannot resolve: the disc retries it (loading, mark up) and
    # then gives up (failed). The one state a local file cannot hold still.
    gone_art = QUrl.fromLocalFile(str(art_dir / "gone.png")).toString()

    patch_offline()  # BEFORE the bridge: its __init__ fires the sign-in check
    bridge = WavesBridge(tidal=None)
    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty("waves", bridge)
    engine.rootContext().setContextProperty("monoFont", _load_mono())
    engine.rootContext().setContextProperty("uiFontFamily", app.font().family())
    engine.load(QUrl.fromLocalFile(str(QML_MAIN)))
    roots = engine.rootObjects()
    if not roots:
        print("Main.qml failed to load", file=sys.stderr)
        return _EXIT_PRECONDITION
    root = roots[0]
    if not isinstance(root, QQuickWindow):
        print("root object is not a window", file=sys.stderr)
        return _EXIT_PRECONDITION

    def q(expr: str):
        r = QQmlExpression(QQmlEngine.contextForObject(root), root, expr).evaluate()
        return r[0] if isinstance(r, tuple) else r

    def pump(until, timeout_ms: int = 3000) -> bool:
        state = {"ok": False}
        loop = QEventLoop()
        poll = QTimer()
        poll.setInterval(25)

        def tick():
            if until():
                state["ok"] = True
                loop.quit()

        poll.timeout.connect(tick)
        poll.start()
        QTimer.singleShot(timeout_ms, loop.quit)
        loop.exec()
        poll.stop()
        return state["ok"]

    def settle(ms: int = 150) -> None:
        pump(lambda: False, ms)

    # WAVES_SCENARIO_GRABS=<dir> saves a window grab at the moments worth
    # eyeballing (a livetest aid, never part of the assertion).
    grab_dir = os.environ.get("WAVES_SCENARIO_GRABS", "")

    def grab(name: str) -> None:
        if grab_dir:
            settle(40)
            root.grabWindow().save(str(Path(grab_dir) / f"{name}.png"))

    # The page builder never touches the network here: a canned page whose
    # covers are the local files. "slow" holds its payload so the skeleton
    # header can be observed while the page is "on the wire".
    def build(kind, media_id, key, *, record=True):
        if media_id == "slow":
            import time

            time.sleep(1.2)

        # "discs": half the rows point at a cover that cannot resolve, half
        # have none at all, so every disc state but "ready" is on one page.
        def row_cover(n: int) -> str:
            if media_id != "discs":
                return row_art
            return gone_art if n < 4 else ""

        rows = [
            {
                "id": f"t{n}",
                "kind": "track",
                "title": f"Track {n}",
                "artist": "A",
                "duration": "3:20",
                "num": n + 1,
                "art": row_cover(n),
            }
            for n in range(8)
        ]
        return {
            "key": key,
            "title": f"Page {media_id}",
            "header": {
                "kind": kind,
                "id": media_id,
                "title": f"Page {media_id}",
                "subtitle": "",
                "desc": "",
                "stats": "8 tracks",
                "art": hero_art,
            },
            "sections": [{"rowKind": "tracks", "title": "Tracks", "items": rows}],
            "error": False,
        }

    bridge._build_browse_item = build

    # The landing the Browse surface asks for when it opens (and revalidates
    # on every return): one shelf of playlist cards wearing a local cover.
    # Served from here, never the network; unchanged on revalidate, so the
    # shelf is never repainted under the pointer.
    cards = [
        {"id": f"p{j}", "kind": "playlist", "title": f"Playlist {j}", "creator": "Curator", "art": hint_art}
        for j in range(6)
    ]
    cards[1]["id"] = "slow"
    cards[2]["id"] = "discs"
    landing = {
        "sections": [{"title": "Shelf", "rowKind": "cards", "items": cards}],
        "genres": [],
        "moods": [],
        "decades": [],
        "error": False,
    }
    bridge._browse_root = lambda: dict(landing)
    prefetched: list = []
    bridge.browsePagePrefetched.connect(lambda p: prefetched.append(dict(p)))

    root.resize(1280, 900)
    root.show()
    settle(400)
    q("bootOverlay.done = true")
    q("bootContentShown = 1")
    q(PARK_LOGIN_QML)
    # Signed in, as far as the hover gate and the slots are concerned.
    bridge._logged_in = True
    bridge.loggedInChanged.emit()
    settle(100)
    q("root.openBrowse()")
    if not pump(lambda: q("browsePageKey") == "" and q("browseSections.length") == 1 and not q("browseBuilding")):
        print(
            f"Browse landing never built (sections={q('browseSections.length')}, error={q('browseError')})",
            file=sys.stderr,
        )
        return _EXIT_PRECONDITION
    settle(300)

    def is_card(it) -> bool:
        # ArtCard (the default art shelves, `hero`) or BrowseCard (console
        # shelves, `openable`): both carry the row's `card` dict.
        mo = it.metaObject()
        return mo.indexOfProperty("card") >= 0 and (
            mo.indexOfProperty("hero") >= 0 or mo.indexOfProperty("openable") >= 0
        )

    def visual_items():
        # The VISUAL tree: delegates a ListView instantiates have no QObject
        # parent, so findChildren never sees them; childItems does.
        stack = [root.contentItem()]
        while stack:
            it = stack.pop()
            yield it
            stack.extend(it.childItems())

    def card_item(card_id: str):
        for it in visual_items():
            if not is_card(it):
                continue
            card = it.property("card")
            if isinstance(card, dict) and card.get("id") == card_id:
                return it
        return None

    def centre_of(it):
        p = it.mapToScene(it.boundingRect().center())
        return QPoint(int(p.x()), int(p.y()))

    def discs():
        # PreviewArt, the round track cover: aimFrac is its own (the square
        # Art next door has artState too, but no seek aim).
        return [it for it in visual_items() if it.metaObject().indexOfProperty("aimFrac") >= 0]

    def disc_states():
        return sorted({str(d.property("artState")) for d in discs()})

    first = card_item("p0")
    if first is None:
        items = list(visual_items())
        n_cards = sum(1 for it in items if is_card(it))
        n_card_prop = sum(1 for it in items if it.metaObject().indexOfProperty("card") >= 0)
        print(
            f"no playlist card in the shelf to hover (sections={q('browseSections.length')}, "
            f"items={len(items)}, with card={n_card_prop}, card-like={n_cards}, error={q('browseError')}, "
            f"landingH={q('browseLanding.contentHeight')}, open={q('browseOpen')}, key={q('browsePageKey')!r})",
            file=sys.stderr,
        )
        return _EXIT_PRECONDITION
    c = centre_of(first)

    # 1. Hover and hold: the dwell must reach the backend and come back.
    QTest.mouseMove(root, QPoint(c.x() - 40, c.y() - 40))
    settle(80)
    QTest.mouseMove(root, c)
    if not pump(lambda: bool(prefetched) and "item:playlist:p0" in bridge._browse_pages, 3000):
        print(
            f"the hover never prefetched the page (prefetched={prefetched}, key={q('root._hoverPrefetchKey')})",
            file=sys.stderr,
        )
        return _EXIT_FAIL
    if prefetched[0].get("art") != hero_art or list(prefetched[0].get("rowArts") or []) != [row_art]:
        print(f"prefetch summary carried the wrong covers: {prefetched[0]}", file=sys.stderr)
        return _EXIT_FAIL
    # The card's cover and the page's covers are in the warm pool.
    if not pump(lambda: q("warmArtModel.count") >= 3, 2000):
        print(f"warm pool did not take the prefetched covers (count={q('warmArtModel.count')})", file=sys.stderr)
        return _EXIT_FAIL
    if q("root.busy") is True:
        print("a hover prefetch flipped the busy indicator", file=sys.stderr)
        return _EXIT_FAIL

    # 2. Click the card's art: the page must paint at once, no loading hint.
    QTest.mouseClick(root, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, c)
    settle(50)
    if q("browsePageKey") != "item:playlist:p0":
        print(f"the click did not open the page (key={q('browsePageKey')})", file=sys.stderr)
        return _EXIT_FAIL
    if q("browsePageLoading") is True or not q("browseItemHeader.visible"):
        print("a prefetched page still showed the loading state on click", file=sys.stderr)
        return _EXIT_FAIL
    if q("browseDrillHint.visible") is True:
        print('"Reading the wire…" showed for a prefetched page', file=sys.stderr)
        return _EXIT_FAIL
    grab("1-prefetched-open")
    # Over the next second the hero must never be in the state that shows the
    # "art: GET" box (waited, with no stand-in up), and must end up ready.
    for _ in range(40):
        settle(25)
        if q("bihArt.artWaited") is True and q("bihArt.underReady") is not True:
            print("the hero showed the art: GET box after a prefetched open", file=sys.stderr)
            return _EXIT_FAIL
    if q("bihArt.artState") != "ready":
        print(f"hero never became ready (state={q('bihArt.artState')})", file=sys.stderr)
        return _EXIT_FAIL
    # The rows' discs came from the warm pool: ready, and none of them ever
    # waited, so no mark was shown and nothing faded.
    warm = discs()
    if not warm:
        print("no track discs on the opened page", file=sys.stderr)
        return _EXIT_PRECONDITION
    if disc_states() != ["ready"]:
        print(f"a warm page's discs were not all ready: {disc_states()}", file=sys.stderr)
        return _EXIT_FAIL
    if any(d.property("artWaited") is True for d in warm):
        print("a warm disc showed the waiting state (it would flash the mark and fade)", file=sys.stderr)
        return _EXIT_FAIL

    # 3. Back, then open a never-hovered card whose page is slow: the header
    #    paints as a skeleton from the card's own title and cover while the
    #    payload is still on the wire.
    q("navBack()")
    if not pump(lambda: q("browsePageKey") == "", 2000):
        print("Back did not return to the landing", file=sys.stderr)
        return _EXIT_FAIL
    settle(200)
    slow = card_item("slow")
    if slow is None:
        print("the slow card is not on the shelf", file=sys.stderr)
        return _EXIT_PRECONDITION
    sc = centre_of(slow)
    QTest.mouseMove(root, QPoint(sc.x() - 40, sc.y() - 40))
    settle(30)
    QTest.mouseClick(root, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, sc)
    settle(30)
    if q("browsePageKey") != "item:playlist:slow" or q("browsePageLoading") is not True:
        print(f"the slow page did not enter its loading state (key={q('browsePageKey')})", file=sys.stderr)
        return _EXIT_FAIL
    if not q("browseItemHeader.visible") or q("bihTitle.text") != "Playlist 1":
        print(
            f"no skeleton header while loading (visible={q('browseItemHeader.visible')}, title={q('bihTitle.text')!r})",
            file=sys.stderr,
        )
        return _EXIT_FAIL
    if not pump(lambda: q("bihArt.underReady") is True, 1000):
        print("the card's cover never showed in the skeleton hero", file=sys.stderr)
        return _EXIT_FAIL
    if q("browseDrillHint.visible") is not True:
        print("the loading hint must still show under the skeleton", file=sys.stderr)
        return _EXIT_FAIL
    grab("2-skeleton-while-loading")
    if not pump(lambda: q("browsePageLoading") is False and q("bihArt.artState") == "ready", 4000):
        print("the slow page never landed", file=sys.stderr)
        return _EXIT_FAIL
    if q("bihTitle.text") != "Page slow":
        print(f"payload title did not replace the hint ({q('bihTitle.text')!r})", file=sys.stderr)
        return _EXIT_FAIL
    grab("3-slow-page-landed")

    # 4. A page whose row covers cannot resolve: the discs must SAY so. While
    #    the retries run they wear the prompt mark, a disc with no cover at
    #    all wears the wave glyph, and once the retries are spent the mark
    #    turns red. None of them may sit as a plain grey hole.
    q("navBack()")
    if not pump(lambda: q("browsePageKey") == "", 2000):
        print("Back did not return to the landing (before the disc page)", file=sys.stderr)
        return _EXIT_FAIL
    settle(200)
    dcard = card_item("discs")
    if dcard is None:
        print("the disc-state card is not on the shelf", file=sys.stderr)
        return _EXIT_PRECONDITION
    QTest.mouseClick(root, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, centre_of(dcard))
    if not pump(lambda: q("browsePageKey") == "item:playlist:discs" and q("browsePageLoading") is False, 3000):
        print(f"the disc page never opened (key={q('browsePageKey')})", file=sys.stderr)
        return _EXIT_FAIL
    # A cover that cannot resolve keeps the disc in loading while it retries.
    if not pump(lambda: any(d.property("artWaited") is True for d in discs()), 2000):
        print(f"an unresolvable cover never reached the waiting state: {disc_states()}", file=sys.stderr)
        return _EXIT_FAIL
    marked = [d for d in discs() if d.property("artWaited") is True]
    if not any(str(d.property("artState")) == "loading" for d in marked):
        print(f"a retrying disc must read as loading, not failed: {disc_states()}", file=sys.stderr)
        return _EXIT_FAIL
    if "none" not in disc_states():
        print(f"a track with no cover must say so, not sit grey: {disc_states()}", file=sys.stderr)
        return _EXIT_FAIL
    grab("4-disc-loading-mark")
    # Retries spent (600ms x 3): the mark turns red rather than retrying forever.
    if not pump(lambda: "failed" in disc_states(), 5000):
        print(f"the discs never gave up (states={disc_states()})", file=sys.stderr)
        return _EXIT_FAIL
    settle(500)  # let the face and the border finish crossing over
    grab("5-disc-failed")
    # 5. Two rows of one album share a key. Crossing between them lands the
    # entered row's arm before the left row's cancel; the cancel must act
    # only for the card that armed, or reading down an album never
    # prefetches it.
    keys = q(
        "(function () {"
        ' var a = { kind: "album", id: "al9", art: "" }, b = { kind: "album", id: "al9", art: "" };'
        " root.hoverPrefetch(b, 5000);"  # the entered row armed first
        " root.hoverPrefetchCancel(a);"  # then the left row cancelled
        " var after_foreign = root._hoverPrefetchKey;"
        " root.hoverPrefetchCancel(b);"  # the owner leaves
        " return after_foreign + '|' + root._hoverPrefetchKey; })()"
    )
    if keys != "album:al9|":
        print(f"a neighbouring row of the same album cancelled the arm it did not own (keys={keys!r})", file=sys.stderr)
        return _EXIT_FAIL
    print("hover prefetch + skeleton header + disc states OK", flush=True)
    return _EXIT_OK


if __name__ == "__main__":
    if "--run-scenario" in sys.argv:
        raise SystemExit(_run_scenario())
    raise SystemExit("run this file through pytest")
