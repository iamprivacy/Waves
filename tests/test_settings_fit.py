"""Settings at the smallest window: everything fits, every way in works.

WHAT THIS FENCES OFF
--------------------
Settings shows one section at a time beside a rail of sections. The rail
takes width from the pane, so at the smallest window the pane is narrow, and
the page adapts: a switch tile too narrow for words beside its switch puts
the switch by its label, and a choice too wide for its row moves under the
words. On the real SettingsPage.qml inside Main.qml, at the window's own
minimum size:

1. No text elides and no control runs past the pane, in any section.
2. A click on each rail row shows that section; Up / Down step through them.
3. Every deep link Main.qml makes (jumpToCard) shows its section, and the
   FLAC one scrolls to the card itself.
4. The section on show is stored (settings_section) for the next launch.
5. The rail scrolls when the window is too short to hold it, so the coffee
   button and the credit at its foot stay reachable.
6. The heart burst runs, clears itself, and the credit comes back whole.

The bridge is built offline (``tidal=None``, cached login parked). Runs in a
SUBPROCESS like the other Main.qml scenarios: building the bridge installs
process-global handlers that must not leak into the suite.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

_EXIT_OK = 0
_EXIT_REGRESSED = 1
_EXIT_NO_QT = 77
_EXIT_PRECONDITION = 78

QML_DIR = Path(__file__).resolve().parent.parent / "waves" / "waves_ui" / "qml"
QML_MAIN = QML_DIR / "Main.qml"

_SECTIONS = [
    "downloads",
    "library",
    "releases",
    "discography",
    "files",
    "metadata",
    "processing",
    "appearance",
    "notifications",
    "pacing",
    "updates",
    "diagnostics",
]


# The switches that show more rows, and the rows they show (by section).
_REVEALING = (
    "auto_update",
    "ffmpeg_auto_update",
    "releases_auto_download",
    "downsample_enabled",
    "library_enabled",
    "metadata_genre",
)
_REVEALED = (
    ("update_cadence", "updates"),
    ("ffmpeg_update_cadence", "processing"),
    ("releases_auto_kinds", "releases"),
    ("downsample_target", "processing"),
    ("metadata_delimiter_genre", "metadata"),
)


def test_settings_fits_the_smallest_window_and_every_way_in_works() -> None:
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    sandbox = tempfile.mkdtemp(prefix="waves-settings-fit-")
    env["XDG_CONFIG_HOME"] = sandbox
    env["HOME"] = sandbox  # nothing in the scenario may reach the real home
    proc = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), "--run-scenario"],
        env=env,
        capture_output=True,
        text=True,
        timeout=240,
    )
    tail = "\n".join((proc.stdout + proc.stderr).strip().splitlines()[-30:])
    if proc.returncode == _EXIT_NO_QT:
        pytest.skip("PySide6 / offscreen Qt unavailable")
    if proc.returncode == _EXIT_PRECONDITION:
        pytest.skip(f"could not set up the scenario in this environment:\n{tail}")
    assert proc.returncode == _EXIT_OK, f"Settings regressed at the smallest window:\n{tail}"


def _run_scenario() -> int:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    try:
        from PySide6.QtCore import QEventLoop, QMetaObject, QPoint, QPointF, Qt, QTimer, QUrl
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
        return r[0] if isinstance(r, tuple) else r

    def settle(ms: int) -> None:
        loop = QEventLoop()
        QTimer.singleShot(ms, loop.quit)
        loop.exec()

    def items(start=None):
        stack = [start or root.contentItem()]
        while stack:
            it = stack.pop()
            yield it
            stack.extend(it.childItems())

    def named(name: str):
        return next((it for it in items() if it.objectName() == name), None)

    def center_of(it):
        p = it.mapToScene(it.boundingRect().center())
        return QPoint(int(p.x()), int(p.y()))

    def click(it) -> None:
        QTest.mouseClick(root, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, center_of(it))

    # The window at its own minimum, whatever the platform's fonts make it.
    root.setVisible(True)
    settle(120)
    min_w = int(root.property("minimumWidth"))
    min_h = int(root.property("minimumHeight"))
    root.resize(min_w, min_h)
    q("bootOverlay.done = true")
    q("bootContentShown = 1")
    q(PARK_LOGIN_QML)
    q("termsGate.visible = false")
    q("ffmpegGate.visible = false")
    q("root.settingsOpen = true")
    settle(500)

    failures: list[str] = []
    pane = named("settingsPane")
    if pane is None:
        print("no settings pane", file=sys.stderr)
        return _EXIT_PRECONDITION
    ids = str(q('settingsPage.groups.map(function(g) { return g.id }).join(",")') or "").split(",")
    if ids != _SECTIONS:
        failures.append(f"the backend's sections are not the rail's: {ids}")

    # 1. Every section fits. Scroll through each so every row is laid out,
    # then again with the switches on that reveal more rows (each one's
    # dependents, and the library card's folder row), staged live so no save
    # is involved.
    def fit_pass(sids) -> None:
        for sid in sids:
            fit_section(sid)

    def fit_section(sid: str) -> None:
        q(f'settingsPage.showSection("{sid}")')
        settle(350)
        h = float(pane.property("contentHeight"))
        step = max(200.0, pane.height() - 40)
        y = 0.0
        while True:
            pane.setProperty("contentY", min(y, max(0.0, h - pane.height())))
            settle(80)
            pw = pane.width()
            for it in items(pane):
                if not it.isVisible() or it.width() <= 0 or it.opacity() == 0:
                    continue
                text = it.property("text")
                if it.property("truncated") is True and isinstance(text, str) and text:
                    failures.append(f"{sid}: elided {text[:70]!r}")
                # Every tile's switch sits in one place: the top right
                # corner, tilePadT from the tile's top, however long the
                # title or help (a centered one moved with them).
                if it.objectName() == "tileSwitch":
                    host = it.parentItem()
                    top = it.y() + (0.0 if host.objectName() == "tileBody" else host.y())
                    if abs(top - pad_t) > 0.5:
                        failures.append(f"{sid}: a switch sits {top:.1f}px from its tile's top, not {pad_t:.0f}")
                    switches[0] += 1
                p0 = it.mapToItem(pane, QPointF(0, 0))
                if p0.x() + it.width() > pw + 1 and p0.x() < pw:
                    failures.append(
                        f"{sid}: {it.metaObject().className()} runs {p0.x() + it.width() - pw:.0f}px past the pane"
                    )
            if y >= h - pane.height():
                break
            y += step
        pane.setProperty("contentY", 0)

    pad_t = float(q("settingsPage.tilePadT"))
    switches = [0]
    fit_pass(_SECTIONS)
    if switches[0] == 0:
        failures.append("no tile switch was found to measure")
    for key in _REVEALING:
        q(f'settingsPage.setLive("{key}", true)')
    q('settingsPage.setLive("library_source", "separate")')
    for key, _ in _REVEALED:
        if not q(f'settingsPage.depOK(settingsPage.fieldByKey("{key}"))'):
            failures.append(f"{key} stayed hidden with its switch on")
    fit_pass(sorted({sid for _, sid in _REVEALED} | {"library"}))
    # The library card's folder field keeps room to read a path: a tight row
    # puts it on a line of its own rather than squeezing it.
    q('settingsPage.showSection("library")')
    settle(300)
    field = named("libFolderField")
    if field is None or not field.isVisible():
        failures.append("the library card's folder field is not shown in separate-folder mode")
    elif field.width() < 200:
        failures.append(f"the library card's folder field is only {field.width():.0f}px wide")
    q("settingsPage.editMap = ({})")
    failures = list(dict.fromkeys(failures))  # one line per problem, not per scroll stop

    # 1b. The Notifications master rides beside the section title, not in the
    # grid, and the body sleeps while it is off: faded, and deaf to clicks.
    if q('settingsPage.railHas("notifications")'):
        q('settingsPage.showSection("notifications")')
        settle(400)

        def visible_named(name):
            return next((it for it in items() if it.objectName() == name and it.isVisible()), None)

        master = visible_named("sectionMaster")
        body = visible_named("sectionBody")
        note = visible_named("sectionMasterNote")
        grid = q(
            'settingsPage.boolFields(settingsPage.groups.filter(function (g) { return g.id === "notifications" })[0]'
            '.fields).map(function (f) { return f.key }).join(",")'
        )
        if "notify_enabled" in str(grid).split(","):
            failures.append("the notifications master is still a tile in the grid")
        if master is None or body is None:
            failures.append("no master switch beside the Notifications title, or no section body")
        else:
            if body.isEnabled() or abs(body.opacity() - 0.4) > 0.01:
                failures.append(f"the body under an off master is not asleep (opacity {body.opacity():.2f})")
            if note is None or "never asked" not in str(note.property("text")):
                failures.append("the off master does not say what off means")
            click(master)
            settle(500)
            if q('settingsPage.editMap["notify_enabled"]') is not True:
                failures.append("clicking the master did not stage it on")
            if not body.isEnabled() or body.opacity() < 0.99:
                failures.append(f"the body did not wake with the master on (opacity {body.opacity():.2f})")
            note = visible_named("sectionMasterNote")
            if note is None or "test banner" not in str(note.property("text")):
                failures.append("the staged master does not say SAVE sends a test banner")
            # What SAVE CHANGES records (the real save would send the banner):
            # once it is saved, the note must stop asking for a save.
            save_src = (Path(__file__).resolve().parent.parent / "waves/waves_ui/qml/SettingsPage.qml").read_text()
            if "page.savedVals = Object.assign({}, page.savedVals, page.editMap)" not in save_src:
                failures.append("SAVE CHANGES no longer records what it saved")
            q("settingsPage.savedVals = Object.assign({}, settingsPage.savedVals, settingsPage.editMap)")
            settle(300)
            note = visible_named("sectionMasterNote")
            if note is not None and "test banner" in str(note.property("text")):
                failures.append("after SAVE the master still says SAVE sends a test banner")
        q("settingsPage.editMap = ({})")
        q("settingsPage.savedVals = ({})")
        settle(400)

    # 2. The rail: a click shows its section; Up / Down step from there.
    for sid in ("pacing", "library", "downloads"):
        row = named("railItem_" + sid)
        if row is None:
            failures.append(f"no rail row for {sid}")
            continue
        # Bring the row into the rail's view first (the rail scrolls at this size).
        scroller = q("settingsPage.railScroller")
        y = row.mapToItem(scroller.property("contentItem"), QPointF(0, 0)).y()
        most = max(0.0, float(scroller.property("contentHeight")) - scroller.height())
        scroller.setProperty("contentY", min(most, max(0.0, y - 40)))
        settle(120)
        click(row)
        settle(200)
        if q("settingsPage.curSec") != sid:
            failures.append(f"clicking the {sid} rail row showed {q('settingsPage.curSec')}")
    QTest.keyClick(root, Qt.Key.Key_Down)
    settle(150)
    if q("settingsPage.curSec") != "library":
        failures.append(f"Down from Downloads showed {q('settingsPage.curSec')}")
    QTest.keyClick(root, Qt.Key.Key_Up)
    settle(150)
    if q("settingsPage.curSec") != "downloads":
        failures.append(f"Up from Library showed {q('settingsPage.curSec')}")
    # A section the schema leaves out (Notifications without banner support)
    # is hidden on the rail, so the arrows step over it, never onto a blank
    # pane, and a remembered pick of it is refused.
    q('settingsPage.groups = settingsPage.groups.filter(function (g) { return g.id !== "notifications" })')
    settle(150)
    q('settingsPage.showSection("appearance")')
    settle(150)
    QTest.keyClick(root, Qt.Key.Key_Down)
    settle(150)
    if q("settingsPage.curSec") != "pacing":
        failures.append(f"Down from Appearance with Notifications hidden showed {q('settingsPage.curSec')}")
    if q('settingsPage.railHas("notifications")'):
        failures.append("a hidden Notifications section still counts as a rail section")
    q("settingsPage.refreshSchema()")
    settle(150)
    q('settingsPage.showSection("downloads")')
    settle(150)

    # 3. Every deep link Main.qml makes lands where it means to.
    main_src = QML_MAIN.read_text(encoding="utf-8")
    want = {"downloads": "downloads", "ffmpeg": "processing", "updates": "updates", "flac": "processing"}
    links = set(re.findall(r'jumpToCard\("([a-z]+)"\)', main_src))
    for link in sorted(links):
        if link not in want:
            failures.append(f"Main.qml deep-links to {link!r}, which this test does not know")
            continue
        q('settingsPage.showSection("diagnostics")')
        settle(150)
        q(f'settingsPage.jumpToCard("{link}")')
        settle(700)
        if q("settingsPage.curSec") != want[link]:
            failures.append(f"jumpToCard({link!r}) showed {q('settingsPage.curSec')}")
    q('settingsPage.showSection("diagnostics")')
    settle(150)
    q('settingsPage.jumpToCard("flac")')
    settle(900)
    flac = q("settingsPage.flacCardItem")
    if flac is None:
        failures.append("the FLAC card was never built")
    else:
        top = flac.mapToItem(pane, QPointF(0, 0)).y()
        if not (-1 <= top < pane.height() - 40):
            failures.append(f"jumpToCard('flac') left the card at y={top:.0f} of a {pane.height():.0f}px pane")

    # 4. The section on show is stored for the next launch.
    q('settingsPage.showSection("metadata")')
    settle(100)
    if bridge.wavesPref("settings_section") != "metadata":
        failures.append(f"the section was not stored: {bridge.wavesPref('settings_section')!r}")

    # 5. The rail's foot is reachable at the smallest window.
    coffee = named("settingsCoffee")
    credit = named("settingsCredit")
    scroller = q("settingsPage.railScroller")
    if coffee is None or credit is None or scroller is None:
        failures.append("the rail foot (coffee button, credit) is missing")
    else:
        scroller.setProperty("contentY", max(0.0, float(scroller.property("contentHeight")) - scroller.height()))
        settle(200)
        rail_bottom = scroller.mapToScene(QPointF(0, scroller.height())).y()
        for name, it in (("coffee button", coffee), ("credit", credit)):
            bottom = it.mapToScene(QPointF(0, it.height())).y()
            if not it.isVisible() or bottom > rail_bottom + 1:
                failures.append(f"the {name} cannot be scrolled into view ({bottom:.0f} > {rail_bottom:.0f})")

        # 6. The heart burst clears itself and the credit retypes.
        settle(2200)  # the credit types itself out first
        gibs = named("settingsGibs")
        if not credit.property("heartLive"):
            failures.append("the credit never finished typing, so the heart cannot burst")
        else:
            QMetaObject.invokeMethod(credit, "gibHeart")
            settle(150)
            if not (credit.property("heartGibbed") and gibs.property("running")):
                failures.append("clicking the heart did not burst it")
            settle(9000)
            if gibs.property("running") or credit.property("heartGibbed"):
                failures.append("the burst never cleared")
            settle(2000)
            if not credit.property("heartLive"):
                failures.append("the credit did not type itself back in after the burst")

    if failures:
        print("\n".join(failures), file=sys.stderr)
        return _EXIT_REGRESSED
    print("ok")
    return _EXIT_OK


if __name__ == "__main__" and "--run-scenario" in sys.argv:
    sys.exit(_run_scenario())
