"""The bar's artist credits fit the bar, on the real Main.qml.

WHAT THIS FENCES OFF
--------------------
A track with many credited artists used to print every name and fill the
bottom bar. Now each credit is its own link (its own artist id, never the
joined line as one link), as many show as fit the room the bar gives them
(more in a wide window, fewer in a narrow one), and the rest fold into a
"+N" chip that opens the featured artists card above the bar, which lists
only those folded names (a pick opens the page a beat later, after the
light's flash). Hovering a name keeps it green. A source that
sends no per-artist list falls back to the joined line split apart. The
would-seek time flag and the card draw from a window overlay, above the
page fades, and a new track closes the card. No binding loops.

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

_CREDITS = (
    '[{name:"Base Notion",id:"11"},{name:"Kira Moll",id:"12"},{name:"The Late Hours",id:"13"},'
    '{name:"Ode Ferro",id:"14"},{name:"Sunny Vale",id:"15"},{name:"Marlo Quist",id:"16"},'
    '{name:"June Arden and the Tide",id:"17"},{name:"Pale Robin",id:"18"},{name:"Ivo Lark",id:"19"}]'
)


def test_the_bar_credits_fit_and_link_one_by_one():
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    sandbox = tempfile.mkdtemp(prefix="waves-now-credits-test-")
    env["HOME"] = sandbox
    env["XDG_CONFIG_HOME"] = str(Path(sandbox) / ".config")
    proc = subprocess.run(  # (fixed argv: this file, one flag)
        [sys.executable, str(Path(__file__).resolve()), "--run-scenario"],
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    tail = "\n".join((proc.stdout + proc.stderr).strip().splitlines()[-16:])
    import pytest

    if proc.returncode == _EXIT_NO_QT:
        pytest.skip("PySide6 / offscreen Qt unavailable")
    if proc.returncode == _EXIT_PRECONDITION:
        pytest.skip(f"could not set up the scenario in this environment:\n{tail}")
    assert proc.returncode == _EXIT_OK, f"the bar's artist credits regressed. Scenario exit={proc.returncode}:\n{tail}"


def _run_scenario() -> int:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    try:
        from PySide6.QtCore import QEventLoop, QPoint, Qt, QTimer, QUrl, qInstallMessageHandler
        from PySide6.QtGui import QGuiApplication
        from PySide6.QtQml import QQmlApplicationEngine, QQmlEngine, QQmlExpression
        from PySide6.QtTest import QTest
    except Exception as exc:
        print(f"Qt unavailable: {exc}", file=sys.stderr)
        return _EXIT_NO_QT

    from _qml_offline import PARK_LOGIN_QML, patch_offline

    loops: list[str] = []

    def on_message(_mode, _ctx, msg) -> None:
        if "Binding loop" in msg or "binding loop" in msg:
            loops.append(msg)

    qInstallMessageHandler(on_message)
    patch_offline()
    app = QGuiApplication.instance() or QGuiApplication([])
    try:
        from waves.waves_ui.app import _load_mono
        from waves.waves_ui.backend import WavesBridge
    except Exception as exc:
        print(f"Qt platform/backend unavailable: {exc}", file=sys.stderr)
        return _EXIT_NO_QT

    WavesBridge._library_root = lambda self: ""  # type: ignore[method-assign]
    WavesBridge.loadBrowse = lambda self: None  # type: ignore[method-assign]
    WavesBridge.refreshBrowse = lambda self: None  # type: ignore[method-assign]
    opened: list[str] = []
    WavesBridge.loadArtist = lambda self, artist_id: opened.append(str(artist_id))  # type: ignore[method-assign]

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

    root.setVisible(True)
    settle(150)
    q("bootOverlay.done = true")
    q("bootContentShown = 1")
    q(PARK_LOGIN_QML)
    q("termsGate.visible = false")
    q("ffmpegGate.visible = false")
    q("root.width = 1280")
    q('root.previewKind = "track"')
    q('root.previewId = "t1"')
    q('root.previewNowTitle = "Sample track title"')
    q('root.previewNowArtist = "Base Notion, Kira Moll & others"')
    q('root.previewNowArtistId = "11"')
    q(f"root.previewNowArtists = {_CREDITS}")
    q("root.previewPlaying = true")
    q("root.previewDuration = 213000")
    q("root.previewPosition = 77000")
    settle(300)
    if not q("nowPlaying.visible"):
        print("the now-playing row did not show", file=sys.stderr)
        return _EXIT_PRECONDITION

    # Docked right, a width change never slides the row: the right edge (rail,
    # stop) holds still, at once, and the row grows leftward.
    edge = q("nowPlaying.x + nowPlaying.width")
    check(abs(edge - (q("statusBar.width") - 22)) < 0.5, f"the row is not docked right: right edge {edge}")
    title = q("root.previewNowTitle")
    q('root.previewNowTitle = "A much longer sample track title"')
    settle(40)
    moved = q("nowPlaying.x + nowPlaying.width") - edge
    check(abs(moved) < 0.5, f"a longer title slid the docked row: its right edge moved {moved:.1f} px")
    q(f"root.previewNowTitle = {title!r}")
    settle(40)

    n = int(q("npArtist.n"))
    mid = int(q("npArtist.shown"))
    check(n == 9, f"the credits are not one entry per artist: {n}")
    check(1 <= mid < n, f"a 1280 window shows {mid} of {n}: the line does not fit the bar")
    check(bool(q("npMore.visible")) and int(q("npMore.n")) == n - mid, "the +N chip does not count the folded names")
    check(q("npArtist.implicitWidth") <= q("npArtist.cap") + 1, "the credits overrun the room the bar gives them")

    # The width follows the window: more names wide, fewer narrow.
    q("root.width = 1800")
    settle(250)
    wide = int(q("npArtist.shown"))
    q("root.width = 1002")
    settle(250)
    narrow = int(q("npArtist.shown"))
    check(wide > mid > narrow >= 1, f"the count does not follow the window: {narrow} < {mid} < {wide} expected")
    q("root.width = 1280")
    settle(250)

    # Clicking the second name opens that artist alone.
    if mid >= 2:
        QTest.mouseClick(
            root,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
            center("npArtistsRow.children[1].children[0]"),
        )
        settle(80)
        check(opened == ["12"], f"the second name did not open its own artist: {opened}")
    opened.clear()

    # Hovering a bar name keeps it green: the underline marks the link.
    QTest.mouseMove(root, center("npArtistsRow.children[1].children[0]"))
    settle(80)
    check(
        q("npArtistsRow.children[1].children[0].color.toString()") == q("root.accent.toString()"),
        "a hovered bar name left the accent green",
    )

    # The chip opens the card above the bar, a row per folded name.
    QTest.mouseMove(root, center("npMore"))
    QTest.mouseClick(root, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, center("npMore"))
    settle(120)
    check(bool(q("nowCard.visible")), "the +N chip did not open the featured artists card")
    bar_top = q("statusBar.mapToItem(nowOverlay, 0, 0).y")
    check(q("nowCard.y + nowCard.height") <= bar_top, "the card does not sit above the bar")
    chip_r = q("npMore.mapToItem(nowOverlay, npMore.width, 0).x")
    card_r = q("nowCard.x + nowCard.width")
    check(
        abs(card_r - 14 - chip_r) < 1.5,
        f"the card does not open over its +N chip: card right {card_r}, chip right {chip_r}",
    )
    rows = int(q("nowCardCol.children.length")) - 3  # head, gap, repeater
    check(rows == n - mid, f"the card lists {rows} names, the bar folded {n - mid}")
    first = q("nowCard.folded[0].name")
    check(first == q(f"root.nowCredits[{mid}].name"), f"the card starts at {first!r}, not the first folded name")
    QTest.mouseMove(root, center("nowCardCol.children[2]"))
    settle(80)
    QTest.mouseMove(root, center("nowCardCol.children[3]"))
    settle(80)
    check(int(q("nowCard.hotRow")) == 1, f"the light does not follow the hovered row: {q('nowCard.hotRow')}")
    QTest.mouseClick(root, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, center("nowCardCol.children[3]"))
    settle(400)
    want = str(11 + mid + 1)
    check(opened == [want], f"a card row did not open its own artist: {opened}, want {want}")
    check(not bool(q("nowCard.visible")), "opening an artist from the card left it open")

    # A new track closes the card.
    q("root.nowCardOpen = true; root.nowCardAnchor = npMore")
    settle(50)
    q('root.previewNowArtists = [{name:"Solo Act",id:"21"}]')
    settle(80)
    check(not bool(q("root.nowCardOpen")), "a new track left the card open")
    check(int(q("npArtist.shown")) == 1 and not bool(q("npMore.visible")), "a single credit shows a chip")

    # No per-artist list: the joined line is split, only the first links.
    q('root.previewNowArtist = "Ann Ray, Bo Kent & Cy Dale feat. Dee"')
    q('root.previewNowArtistId = "31"')
    q("root.previewNowArtists = []")
    settle(80)
    names = q("root.nowCredits.map(function (c) { return c.name }).join('|')")
    ids = q("root.nowCredits.map(function (c) { return c.id }).join('|')")
    check(names == "Ann Ray|Bo Kent|Cy Dale|Dee", f"the joined line did not split into names: {names!r}")
    check(ids == "31|||", f"only the first split name may carry the id: {ids!r}")

    # The would-seek flag floats above the bar, from the overlay.
    QTest.mouseMove(root, center("nowRail"))
    settle(80)
    check(bool(q("nowAimFlag.visible")), "hovering the rail shows no time flag")
    check(
        q("nowAimFlag.y + nowAimFlag.height") <= q("statusBar.mapToItem(nowOverlay, 0, 0).y"),
        "the time flag is not above the bar",
    )

    check(not loops, f"binding loops: {loops[:3]}")
    if failures:
        for f in failures:
            print("FAIL:", f, file=sys.stderr)
        return _EXIT_REGRESSED
    print("ok")
    return _EXIT_OK


if __name__ == "__main__" and "--run-scenario" in sys.argv:
    sys.exit(_run_scenario())
