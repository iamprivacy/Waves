"""The artist bio fallback on the real Main.qml.

WHAT THIS FENCES OFF
--------------------
With "Fill missing artist bios" off (the default), an artist page or card
with no bio says so and offers FILL MISSING BIOS, which opens Settings on
the Appearance card; nothing is asked of the bridge. Turned on, the page
left behind asks for its bio at once and reads "Looking for a biography"
until the answer lands; a landed bio shows with its source's credit
("From Wikipedia", "Data provided by Discogs."), on the page and in the
card. Turned off again, a fallback bio is hidden and the link is back. No
binding loops, no QML errors.

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


def test_the_bio_fallback_link_states_and_credits():
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    sandbox = tempfile.mkdtemp(prefix="waves-artist-bio-test-")
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
    assert proc.returncode == _EXIT_OK, f"the bio fallback regressed. Scenario exit={proc.returncode}:\n{tail}"


def _run_scenario() -> int:  # (one linear scenario)
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    try:
        from PySide6.QtCore import QEventLoop, QObject, QPoint, QPointF, Qt, QTimer, QUrl, qInstallMessageHandler
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

    asked: list[str] = []
    summaries = {
        "11": {"id": "11", "known": True, "name": "Base Notion", "art": "", "bio": "", "top": [], "releases": []}
    }
    WavesBridge._library_root = lambda self: ""  # type: ignore[method-assign]
    WavesBridge.loadBrowse = lambda self: None  # type: ignore[method-assign]
    WavesBridge.refreshBrowse = lambda self: None  # type: ignore[method-assign]
    WavesBridge.prefetchArtist = lambda self, *a: None  # type: ignore[method-assign]
    WavesBridge.artistSummary = lambda self, aid: dict(summaries.get(str(aid), {"id": str(aid), "known": False}))  # type: ignore[method-assign]
    WavesBridge.requestArtistCard = lambda self, aid: None  # type: ignore[method-assign]
    WavesBridge.wantArtistBio = lambda self, aid: asked.append(str(aid))  # type: ignore[method-assign]
    WavesBridge.artistLibraryPresence = lambda self, name: {"present": False}  # type: ignore[method-assign]

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

    def page_item(name: str):
        return root.findChild(QObject, name)

    def shown(it) -> bool:
        return it is not None and wrapInstance(getCppPointer(it)[0], QQuickItem).isVisible()

    def card_item(name: str):
        top = q("ahcCard.contentItem")
        if top is None:
            return None
        stack = [wrapInstance(getCppPointer(top)[0], QQuickItem)]
        while stack:
            it = stack.pop()
            if it.objectName() == name:
                return it
            stack.extend(it.childItems())
        return None

    def click_item(it) -> None:
        w = wrapInstance(getCppPointer(it)[0], QQuickItem)
        p = w.mapToScene(QPointF(w.width() / 2, w.height() / 2))
        pt = QPoint(int(p.x()), int(p.y()))
        QTest.mouseMove(root, pt)
        settle(40)
        QTest.mouseClick(root, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, pt)

    def center(item: str) -> QPoint:
        x = q(f"{item}.mapToItem(null, {item}.width / 2, {item}.height / 2).x")
        y = q(f"{item}.mapToItem(null, {item}.width / 2, {item}.height / 2).y")
        return QPoint(int(x), int(y))

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
    settle(100)
    if not q("root.signedIn") or q("root.artistBioOn"):
        print("could not sign in with the switch off", file=sys.stderr)
        return _EXIT_PRECONDITION

    def open_page() -> None:
        q("root.settingsOpen = false; root.artistOpen = true")
        settle(200)

    # 1. Switch off: the page says there is no bio and offers the switch.
    q('root.artistData = {id: "21", name: "Sample Artist", bio: "", albums: [], eps: [], tracks: [], videos: []}')
    open_page()
    missing, fill = page_item("artistBioMissing"), page_item("artistBioFill")
    check(shown(missing) and shown(fill), "no missing-bio line with its link while the switch is off")
    check(asked == [], f"a lookup was asked with the switch off: {asked}")

    # 2. The link opens Settings on the Appearance card.
    if fill is not None:
        click_item(fill)
        settle(300)
    check(bool(q("root.settingsOpen")) and not q("root.artistOpen"), "FILL MISSING BIOS did not open Settings")

    # 3. Turned on: the page left behind asks at once, and reads as looking.
    bridge.setWavesPref("artist_bio_fallback", True)
    settle(100)
    check(bool(q("root.artistBioOn")), "the QML did not re-read the switch on its signal")
    check(asked == ["21"], f"turning the switch on did not ask for the page's bio: {asked}")
    open_page()
    check(shown(missing) and not shown(fill), "the page did not read as looking once the switch was on")

    # 4. The answer lands with its credit.
    bridge.artistBioLanded.emit(
        {"id": "21", "bio": "A short profile.", "bio_source": "discogs", "bio_url": "https://www.discogs.com/artist/1"}
    )
    settle(150)
    credit = page_item("artistBioCredit")
    check(q("root.pageBio") == "A short profile.", f"the landed bio did not show: {q('root.pageBio')!r}")
    check(shown(credit) and credit.property("text") == "Data provided by Discogs.", "no Discogs credit")
    check(not shown(missing), "the missing-bio line stayed under a bio")

    # 5. The card: no bio asks, reads as looking, then shows Wikipedia's.
    q('root.previewKind = "track"; root.previewId = "t1"; root.previewLoading = false')
    q('root.previewNowTitle = "Sample track title"; root.previewNowArtist = "Base Notion, Kira Moll"')
    q(f'root.previewNowArtistId = "11"; root.previewNowArtists = {_CREDITS}')
    q("root.previewPlaying = true; root.previewDuration = 213000; root.previewPosition = 77000")
    q("root.artistOpen = false")
    settle(300)
    QTest.mouseMove(root, center("npArtistsRow.children[0].children[0]"))
    settle(700)
    if not q("ahcCard.visible"):
        print("the artist card did not open", file=sys.stderr)
        return _EXIT_PRECONDITION
    check("11" in asked, f"the card did not ask for its bio: {asked}")
    none = card_item("ahcBioNone")
    check(shown(none) and str(none.property("text")).startswith("Looking"), "the card did not read as looking")
    bridge.artistBioLanded.emit(
        {"id": "11", "bio": "An intro.", "bio_source": "wikipedia", "bio_url": "https://en.wikipedia.org/wiki/X"}
    )
    settle(150)
    ccredit = card_item("ahcBioCredit")
    check(q("root.ahcBio") == "An intro.", "the card did not take the landed bio")
    check(shown(ccredit) and ccredit.property("text") == "From Wikipedia", "no Wikipedia credit in the card")

    # 6. Off again: the fallback bio goes and the link is back.
    bridge.setWavesPref("artist_bio_fallback", False)
    settle(150)
    check(q("root.ahcBio") == "", "a fallback bio stayed in the card with the switch off")
    check(shown(card_item("ahcBioFill")), "the card did not offer the switch again")
    check(q("root.pageBio") == "", "a fallback bio stayed on the page with the switch off")

    # 7. On again. A lookup that could not ask is not kept: the next view
    # asks again. A landed bio past its showing limit (a Discogs profile
    # six hours on, a "none" a week on) reads as never landed.
    bridge.setWavesPref("artist_bio_fallback", True)
    settle(100)
    q('root.askBio("31")')
    bridge.artistBioLanded.emit({"id": "31", "bio": "", "bio_source": "", "bio_url": "", "failed": True})
    settle(50)
    q('root.askBio("31")')
    check(asked.count("31") == 2, f"a failed lookup was kept for the session: {asked}")
    now = int(q("Date.now() / 1000"))
    for aid, bio, src, until in (("41", "Old.", "discogs", now - 1), ("42", "Fresh.", "discogs", now + 3600)):
        q(f'root.askBio("{aid}")')
        bridge.artistBioLanded.emit({"id": aid, "bio": bio, "bio_source": src, "bio_url": "", "bio_until": until})
    q('root.askBio("43")')
    bridge.artistBioLanded.emit({"id": "43", "bio": "", "bio_source": "", "bio_url": "", "bio_until": now - 1})
    settle(50)
    check(q('root.bioOf({id: "41", bio: ""}).bio || ""') == "", "an expired Discogs bio still showed")
    check(q('root.bioOf({id: "42", bio: ""}).bio || ""') == "Fresh.", "a fresh Discogs bio did not show")
    for aid in ("41", "42", "43"):
        q(f'root.askBio("{aid}")')
    check(
        asked.count("41") == 2 and asked.count("42") == 1 and asked.count("43") == 2,
        f"only the expired answers are asked again: {asked}",
    )

    QTest.mouseMove(root, QPoint(5, 5))
    settle(300)
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
