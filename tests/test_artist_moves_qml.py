"""Same-name moves on the real Main.qml: the Releases notice and the artist
page's namesake line.

WHAT THIS FENCES OFF
--------------------
A follow on another artist who shares the name is moved (or the move
suggested) to the profile the library's albums are on. On the real page:

1. A move made shows a panel on the artist page of the profile moved to,
   naming the library albums, with OK (green) and UNDO; UNDO puts the old
   follow back and the panel goes. Nothing stacks at the top of Releases.
2. A suggestion shows on the followed profile's page with SWITCH and KEEP;
   KEEP leaves the follow as it is, for good, and the panel goes. The
   Following list marks both (MOVED, SAME NAME).
3. The artist page of a profile that is not the library's says so under
   the name and offers the other one; the library's own profile says
   nothing.
4. On a shared profile the releases set apart sit in their own section at
   the bottom, folded, built only when opened, and the shelves keep only
   the rest.

The bridge is built offline (``tidal=None``, cached login parked). Runs in a
SUBPROCESS like the other Main.qml scenarios. ``WAVES_MOVES_SHOT_DIR`` saves
pictures of both panels there.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

_EXIT_OK = 0
_EXIT_REGRESSED = 1
_EXIT_NO_QT = 77
_EXIT_PRECONDITION = 78

QML_MAIN = Path(__file__).resolve().parent.parent / "waves" / "waves_ui" / "qml" / "Main.qml"
OLD, NEW, ODD = "3581093", "34868882", "4298449"
ODD2, ODD3 = "4298450", "4298451"


def test_moves_show_on_the_artist_page_and_in_the_following_list():
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="waves-moves-test-")
    env["HOME"] = env["XDG_CONFIG_HOME"]  # nothing in the scenario may reach the real home
    proc = subprocess.run(  # (fixed argv: this file under the test interpreter)
        [sys.executable, str(Path(__file__).resolve()), "--run-scenario"],
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    # The engine's teardown warnings (a null bridge) would bury the findings.
    said = [ln for ln in (proc.stdout + proc.stderr).strip().splitlines() if "WARN  waves.qt" not in ln]
    tail = "\n".join(said[-12:])
    import pytest

    if proc.returncode == _EXIT_NO_QT:
        pytest.skip("PySide6 / offscreen Qt unavailable")
    if proc.returncode == _EXIT_PRECONDITION:
        pytest.skip(f"could not set up the scenario in this environment:\n{tail}")
    assert proc.returncode == _EXIT_OK, f"the same-name move lines regressed. Scenario exit={proc.returncode}:\n{tail}"


def _run_scenario() -> int:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    try:
        from PySide6.QtCore import QEventLoop, QPoint, QPointF, Qt, QTimer, QUrl
        from PySide6.QtGui import QGuiApplication
        from PySide6.QtQml import QQmlApplicationEngine, QQmlEngine, QQmlExpression
        from PySide6.QtQuick import QQuickWindow
        from PySide6.QtTest import QTest
    except Exception as exc:
        print(f"Qt unavailable: {exc}", file=sys.stderr)
        return _EXIT_NO_QT

    from _qml_offline import PARK_LOGIN_QML, patch_offline

    patch_offline()

    app = QGuiApplication.instance() or QGuiApplication([])
    if sys.platform != "darwin":
        app.setApplicationName("Waves")
        app.setOrganizationName("Waves")
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

    def items(name: str):
        stack = [root.contentItem()]
        while stack:
            it = stack.pop()
            if it.objectName() == name and it.isVisible():
                yield it
            stack.extend(it.childItems())

    def label_of(it) -> str:
        return str(it.property("label") or "")

    def click(it) -> None:
        p = it.mapToScene(QPointF(it.width() / 2, it.height() / 2))
        pt = QPoint(int(p.x()), int(p.y()))
        QTest.mouseMove(root, pt)
        settle(40)
        QTest.mouseClick(root, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, pt)
        settle(200)

    def shot(it, name: str, pad: int = 12) -> None:
        out = os.environ.get("WAVES_MOVES_SHOT_DIR")
        if not out or it is None:
            return
        tl = it.mapToScene(QPointF(0, 0))
        root.grabWindow().copy(
            max(0, int(tl.x()) - pad), max(0, int(tl.y()) - pad), int(it.width()) + 2 * pad, int(it.height()) + 2 * pad
        ).save(os.path.join(out, name))

    failures: list[str] = []

    def check(cond, what: str) -> None:
        if not cond:
            failures.append(what)

    root.resize(1280, 900)
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
    if not q("root.signedIn"):
        print("could not sign in", file=sys.stderr)
        return _EXIT_PRECONDITION

    # One move made (a mirrored favorite) and one only suggested (a follow
    # made by hand), straight through the store the bridge reads.
    store = bridge._releases
    store.sync_tidal_favorites([{"id": OLD, "name": "Adept", "art": ""}])
    store.move_follow(OLD, {"id": NEW, "name": "Adept", "art": ""}, name_key="adept", evidence={"albums_n": 14})
    store.follow(ODD, "Static X", "")
    store.suggest_move(ODD, {"id": "14059", "name": "Static-X", "art": ""}, evidence={"albums_n": 25})
    bridge.artistMovesChanged.emit()
    q("root.settingsOpen = false; root.artistOpen = false; root.releasesOpen = true")
    settle(400)

    # Nothing stacks at the top of the Releases pane any more.
    check(not list(items("relMoveLine")), "a move line still shows at the top of Releases")

    def page_for(aid, name):
        q("root.releasesOpen = false; root.artistOpen = true")
        q(f'root.artistData = {{id: "{aid}", name: "{name}", bio: "", albums: [], eps: [], tracks: [], videos: []}}')
        settle(300)
        return next(items("artistMovePanel"), None)

    def panel_text():
        t = next(items("artistMoveText"), None)
        return str(t.property("text")) if t is not None else ""

    # A move made shows on the profile now followed, with the albums named.
    panel = page_for(NEW, "Adept")
    check(panel is not None, "the move panel did not show on the profile moved to")
    check("moved your follow here from another Adept" in panel_text(), f"the move panel reads {panel_text()!r}")
    bridge.artistMoveAlbumsLoaded.emit(
        OLD,
        {
            "held": [
                {"title": "Silence the World", "year": "2011", "cover": ""},
                {"title": "Death Dealers", "year": "2013", "cover": ""},
            ],
            "covers": [],
            "total": 14,
        },
    )
    settle(200)
    check("These 14 albums in your library are on this profile." in panel_text(), f"the panel reads {panel_text()!r}")
    # The library changed: another move's albums are dropped, the open one's kept until read again.
    q('root.relMoveAlbums = Object.assign({"999": {"held": [], "total": 3}}, root.relMoveAlbums)')
    bridge.libraryPresenceChanged.emit()
    settle(100)
    check(
        q('Object.keys(root.relMoveAlbums).join(",")') == OLD, "the move albums were not refreshed on a library change"
    )
    check(q("relMoveReloadTimer.running") is True, "the open page's move albums were not set to be read again")
    albums = next(items("artistMoveAlbums"), None)
    check(albums is not None, "the panel names no albums")
    gos = [label_of(b) for b in items("artistMoveGo")]
    backs = [label_of(b) for b in items("artistMoveBack")]
    check(gos == ["OK"] and backs == ["UNDO"], f"the moved panel's buttons read {gos} and {backs}")
    go = next(items("artistMoveGo"), None)
    check(go is not None and bool(go.property("primary")), "OK is not the green primary button")
    shot(panel, "artist-move-panel.png")
    # Not on the old profile's page.
    check(page_for(OLD, "Adept") is None, "the move panel showed on the profile moved away from")

    # A suggestion shows on the profile still followed; it never says
    # "a different artist" (a split profile is the same artist).
    panel = page_for(ODD, "Static X")
    check(panel is not None, "the suggestion panel did not show on the followed profile")
    check(
        "Another Static X profile has these 25 albums from your library" in panel_text()
        and "different artist" not in panel_text(),
        f"the suggestion panel reads {panel_text()!r}",
    )
    gos = [label_of(b) for b in items("artistMoveGo")]
    check(gos == ["SWITCH"], f"the suggestion's button reads {gos}")
    shot(panel, "artist-suggest-panel.png")

    # One album reads "is"; a profile picked on songs alone names songs.
    store.follow(ODD2, "Kino", "")
    store.move_follow(ODD2, {"id": "555", "name": "Kino", "art": ""}, evidence={"albums_n": 1})
    store.follow(ODD3, "Mono", "")
    store.suggest_move(ODD3, {"id": "556", "name": "Mono", "art": ""}, evidence={"albums_n": 0})
    bridge.artistMovesChanged.emit()
    settle(200)
    page_for("555", "Kino")
    check(
        "This album in your library is on this profile." in panel_text(), f"the one-album panel reads {panel_text()!r}"
    )
    page_for(ODD3, "Mono")
    check("has the songs from your library" in panel_text(), f"the songs-only panel reads {panel_text()!r}")
    store.accept_move(ODD2)
    store.dismiss_move(ODD3)
    bridge.artistMovesChanged.emit()
    settle(200)
    check(page_for("555", "Kino") is None, "the panel stayed after OK")

    # The Following list marks both follows.
    q("root.artistOpen = false; root.releasesOpen = true; root.releasesSetView('manage')")
    for _ in range(30):
        settle(100)
        if len(list(items("relMoveMark"))) >= 2:
            break
    marks = sorted(str(m.property("text")) for m in items("relMoveMark"))
    check(marks == ["MOVED", "SAME NAME"], f"the Following marks read {marks}")

    page_for(NEW, "Adept")
    undo = next(items("artistMoveBack"), None)
    if undo is not None:
        click(undo)
        check(store.is_followed(OLD) and not store.is_followed(NEW), "UNDO did not put the old follow back")
    page_for(ODD, "Static X")
    keep = next(items("artistMoveBack"), None)
    if keep is not None:
        click(keep)
        check(store.is_followed(ODD), "KEEP dropped the follow")
    settle(200)
    check(page_for(ODD, "Static X") is None, "the panel stayed after KEEP")

    # The artist page: a namesake of the library's artist, then the library's
    # own. The library holds Adept (a published rollup staged in place).
    idx = SimpleNamespace(cache=SimpleNamespace(distinct_artists=list))  # the namesake line needs a library on
    bridge._library_index = idx
    bridge._library_artist_index_src = idx
    bridge._library_artist_index = {"adept": {"present": True, "albums": 14, "tracks": 140}}
    bridge._rel_anchor_map = {"adept": NEW}
    bridge.artistAnchorsChanged.emit()
    q("root.releasesOpen = false; root.artistOpen = true")
    q(f'root.artistData = {{id: "{OLD}", name: "Adept", bio: "", albums: [], eps: [], tracks: [], videos: []}}')
    settle(400)
    line = next(items("artistNamesakeLine"), None)
    check(line is not None, "the namesake line did not show on another profile's page")
    check(next(items("artistPageBadges"), None) is None, "the library strip lit on a namesake's page")
    btn = next(items("artistDlBtn"), None)
    check(btn is not None and not btn.property("libPartial"), "the Download button said partly held on a namesake")
    shot(line.parentItem() if line is not None else None, "artist-namesake-line.png")
    q(f'root.artistData = {{id: "{NEW}", name: "Adept", bio: "", albums: [], eps: [], tracks: [], videos: []}}')
    settle(300)
    check(next(items("artistNamesakeLine"), None) is None, "the namesake line showed on the library's own profile")
    strip = next(items("artistPageBadges"), None)
    check(strip is not None, "the library strip did not light on the library's own profile")
    btn = next(items("artistDlBtn"), None)
    check(btn is not None and bool(btn.property("libPartial")), "the Download button did not say partly held")
    shot(strip.parentItem() if strip is not None else None, "artist-library-profile.png")

    # A shared profile: the releases set apart sit in their own folded
    # section at the bottom, built only when opened.
    def rel(i, title, rtype="album"):
        return {
            "id": i,
            "title": title,
            "artist": "Sparta",
            "artist_id": "30345",
            "art": "",
            "year": "2024",
            "date": "2024-05-01",
            "tracks": 1,
            "quality": "LOSSLESS",
            "popularity": 10,
            "rtype": rtype,
        }

    page = {
        "id": "30345",
        "name": "Sparta",
        "art": "",
        "bio": "",
        "tracks": [],
        "videos": [],
        "albums": [rel("a1", "Wiretap Scars"), rel("a2", "Porcelain")],
        "eps": [rel("e1", "Crater", "single")],
        "apart": [
            rel("x1", "Bora Bora", "single"),
            rel("x2", "Rap Salvaje", "single"),
            rel("x3", "Jealousy", "single"),
        ],
    }
    bridge.artistLoaded.emit(page)
    settle(400)
    check(q("artistApartModel.count") == 3, "the set-apart releases did not reach their section")
    check(q("artistApartHead.visible") is True, "the set-apart section header is hidden")
    check(q("artistApartRep.count") == 0, "the set-apart section opened on its own")
    check(q("artistAlbumsModel.count") == 2 and q("artistEpModel.count") == 1, "the shelves took set-apart releases")
    q("artistView.contentY = Math.max(0, artistView.contentHeight - artistView.height)")
    settle(200)
    click(next(items("artistApartHead")))
    q("artistView.contentY = Math.max(0, artistView.contentHeight - artistView.height)")
    settle(300)
    check(q("artistApartRep.count") == 3, "opening the set-apart section built no rows")
    check(q("artistApartNote.visible") is True, "the set-apart section carries no note")
    out = os.environ.get("WAVES_MOVES_SHOT_DIR")
    head = next(items("artistApartHead"), None)
    if out and head is not None:
        a = head.mapToScene(QPointF(0, 0))
        last = q("artistApartRep.itemAt(2)")
        b = last.mapToScene(QPointF(0, last.height()))
        root.grabWindow().copy(int(a.x()) - 12, int(a.y()) - 12, int(head.width()) + 24, int(b.y() - a.y()) + 24).save(
            os.path.join(out, "artist-apart-section.png")
        )
    bridge.artistLoaded.emit({**page, "id": "1", "name": "Other", "apart": []})
    settle(300)
    check(q("artistApartHead.visible") is False, "the set-apart section showed on a profile with none")

    if failures:
        print("\n".join(failures), file=sys.stderr)
        return _EXIT_REGRESSED
    print("ok")
    return _EXIT_OK


if __name__ == "__main__" and "--run-scenario" in sys.argv:
    sys.exit(_run_scenario())
