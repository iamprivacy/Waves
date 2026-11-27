"""Settings: the "Ask Lavender first" key opens a Lavender drawer when it cannot work.

WHAT THIS FENCES OFF
--------------------
The key sits in the Prefer LRCLIB tile and only does something with the
Lavender app installed. On the real SettingsPage.qml inside Main.qml:

1. Lavender missing: the key carries a NOT INSTALLED tag. A click leaves it
   off, stages nothing, and lifts the key into the drawer over the help.
2. The drawer's x drops it back to the key, tag and all.
3. Lavender installed while Waves runs: the next click turns the key on, with
   no drawer and no tag (no restart).
4. DOWNLOAD fetches the installer: the bar with its red x while it comes
   (the x cancels), the reason when it is refused, INSTALL once it is saved
   and opened, and the key the user asked for comes on as soon as the new
   Lavender is found. No build for this computer opens Lavender's site.
5. An installed Lavender without the lyrics worker: the next time Settings
   opens, the drawer says it is too old, and the saved key stays on.

Nothing is opened for real: file and https links go to a recorder, and the
download's states are sent by hand (no network).

The bridge is built offline (``tidal=None``, cached login parked). Runs in a
SUBPROCESS like the other Main.qml scenarios: building the bridge installs
process-global handlers that must not leak into the suite.
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


def test_the_lavender_key_opens_a_drawer_when_it_cannot_work():
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="waves-lavender-key-test-")
    env["HOME"] = env["XDG_CONFIG_HOME"]  # nothing in the scenario may reach the real home
    proc = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), "--run-scenario"],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    tail = "\n".join((proc.stdout + proc.stderr).strip().splitlines()[-10:])
    import pytest

    if proc.returncode == _EXIT_NO_QT:
        pytest.skip("PySide6 / offscreen Qt unavailable")
    if proc.returncode == _EXIT_PRECONDITION:
        pytest.skip(f"could not set up the scenario in this environment:\n{tail}")
    assert proc.returncode == _EXIT_OK, f"the Lavender key regressed. Scenario exit={proc.returncode}:\n{tail}"


def _run_scenario() -> int:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    try:
        from PySide6.QtCore import QEventLoop, QMetaObject, QObject, QPoint, Qt, QTimer, QUrl, Slot
        from PySide6.QtGui import QDesktopServices, QGuiApplication
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
        import waves.lyrics_lavender as lav
        from waves.waves_ui.app import _load_mono
        from waves.waves_ui.backend import WavesBridge
    except Exception as exc:
        print(f"Qt platform/backend unavailable: {exc}", file=sys.stderr)
        return _EXIT_NO_QT

    # The machine's own Lavender (or lack of one) never decides the test.
    installs: list[list[str]] = []
    lav._candidates = lambda: installs

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
            raise RuntimeError(e.error().toString())
        return r[0] if isinstance(r, tuple) else r

    def settle(ms: int) -> None:
        loop = QEventLoop()
        QTimer.singleShot(ms, loop.quit)
        loop.exec()

    def find_all(top, name: str):
        stack, found = [top], []
        while stack:
            it = stack.pop()
            stack.extend(it.childItems())
            if it.objectName() == name:
                found.append(it)
        return found

    def tile_parts():
        """The Prefer LRCLIB tile, its drawer and its key's tag, as built right now."""
        for drawer in find_all(root.contentItem(), "lavenderDrawer"):  # every flag tile builds one
            tile = drawer.parentItem()
            if tile.property("lavenderKey"):
                tags = find_all(tile, "lavenderTag")
                return tile, drawer, tags[0] if tags else None
        return None, None, None

    class Opened(QObject):
        def __init__(self) -> None:
            super().__init__()
            self.urls: list[str] = []

        @Slot(QUrl)
        def open(self, url) -> None:
            self.urls.append(url.toString())

    opened = Opened()
    for scheme in ("file", "https", "http"):
        QDesktopServices.setUrlHandler(scheme, opened, "open")

    def drawer_part(name: str):
        found = find_all(drawer, name)
        return found[0] if found else None

    shots = os.environ.get("WAVES_LAVENDER_KEY_SHOT")
    env_home = os.environ["HOME"]  # the test's own temporary home

    def shoot(name: str) -> None:
        if shots:
            tile, _drawer, _tag = tile_parts()
            p = tile.mapToScene(tile.boundingRect().topLeft())
            box = (int(p.x()) - 12, int(p.y()) - 12, int(tile.width()) + 24, int(tile.height()) + 24)
            root.grabWindow().copy(*box).save(f"{shots}-{name}.png")

    root.resize(1280, 1700)  # tall, so the lyrics tiles at the foot of their section are on show
    root.setVisible(True)
    settle(120)
    q("bootOverlay.done = true")
    q("bootContentShown = 1")
    q(PARK_LOGIN_QML)
    q("termsGate.visible = false")
    q("ffmpegGate.visible = false")
    q("root.settingsOpen = true")
    settle(200)
    section = q(
        "(settingsPage.groups.filter(function(g) { return g.fields.some(function(f) {"
        ' return f.key === "lyrics_prefer_lrclib" }) })[0] || {}).id'
    )
    if not section:
        print("no section holds the Prefer LRCLIB tile", file=sys.stderr)
        return _EXIT_PRECONDITION
    q(f'settingsPage.showSection("{section}")')
    # The tile is live only while a lyrics option and the preference are on.
    q('settingsPage.setv("lyrics_embed", true)')
    q('settingsPage.setv("lyrics_prefer_lrclib", true)')
    settle(400)
    tile, drawer, tag = tile_parts()
    if tile is None or tag is None:
        print("the Lavender key was never built", file=sys.stderr)
        return _EXIT_PRECONDITION

    def staged():
        return q('settingsPage.editMap["lyrics_use_lavender"]')

    def click_key() -> None:
        QMetaObject.invokeMethod(tile, "flipChild")
        settle(500)

    failures: list[str] = []
    if drawer.isVisible():
        failures.append("the drawer shows before the key was touched")
    if not tag.isVisible() or tag.property("text") != "NOT INSTALLED":
        failures.append(f"a missing Lavender is not tagged on the key: {tag.property('text')!r}")
    shoot("first")

    click_key()
    if staged() is True:
        failures.append("the key turned on with no Lavender installed")
    if not drawer.isVisible() or tile.property("lavState") != "missing":
        failures.append(f"a click with Lavender missing did not open the drawer (state {tile.property('lavState')!r})")
    if tag.isVisible():
        failures.append("the key's tag shows under the open drawer")
    shoot("missing")

    tile.setProperty("lavShut", True)  # what the drawer's x does
    settle(600)  # the drawer folds back into the key
    if drawer.isVisible() or not tag.isVisible():
        failures.append("the drawer's x did not drop it back to the tagged key")
    shoot("shut")

    # Saved on from a time Lavender was installed: turning the key off is
    # not asking for Lavender, so the drawer stays down.
    q('settingsPage.setv("lyrics_use_lavender", true)')
    tile.setProperty("lavShut", True)
    settle(200)
    click_key()
    settle(200)
    if staged() is not False or drawer.isVisible() or not tag.isVisible():
        failures.append(f"turning the key off with Lavender missing reopened the drawer (staged {staged()!r})")

    installs.append([sys.executable])
    click_key()
    if staged() is not True:
        failures.append(f"an install made while Waves runs did not let the key turn on: {staged()!r}")
    if drawer.isVisible() or tag.isVisible():
        failures.append("the drawer or tag stayed after Lavender was installed")
    shoot("ok")

    # DOWNLOAD, from a missing Lavender again, through the real slot and its
    # worker. Only the fetch itself is stood in for (no network).
    import waves.lavender_get as lget

    lget.installer_pattern = lambda: r"Lavender_.*\.dmg"
    saved = Path(env_home) / "Lavender_0.1.5_aarch64.dmg"

    def fetch_until_canceled(dest_dir, session, progress_cb=None, abort=None):
        progress_cb(42.0)
        abort.wait(10)
        raise lget.LavenderGetCanceled()

    def fetch_refused(*a, **k):
        raise lget.LavenderGetError(lget.NOT_VERIFIED)

    installs.clear()
    q('settingsPage.setv("lyrics_use_lavender", false)')
    click_key()
    bar, get_btn = drawer_part("lavenderBar"), drawer_part("lavenderGet")
    lget.fetch = fetch_until_canceled
    q("settingsPage.getLavender()")
    settle(400)
    if not drawer.isVisible() or not bar.isVisible() or get_btn.isVisible():
        failures.append("a download does not show its bar in place of the button")
    if round(q("settingsPage.lavGetPct")) != 42:
        failures.append(f"the bar does not follow the download: {q('settingsPage.lavGetPct')!r}")
    shoot("downloading")
    led = drawer_part("lavenderLed")
    if led.property("showMsg") is not True:
        failures.append("the wide drawer's bar lost its words")
    root.resize(880, 1700)  # the window's minimum width: the tile is at its narrowest
    settle(300)
    if led.property("showMsg") is not False or led.property("rw") < 60:
        failures.append(f"the narrow drawer's bar keeps words it has no room for: rw {led.property('rw')!r}")
    shoot("downloading-narrow")
    root.resize(1280, 1700)
    settle(300)
    cancel = drawer_part("lavenderCancel")
    center = cancel.mapToScene(cancel.boundingRect().center())
    QTest.mouseClick(
        root, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(int(center.x()), int(center.y()))
    )
    settle(400)
    if bar.isVisible() or not get_btn.isVisible() or q("settingsPage.lavGet") != "":
        failures.append("the bar's red x did not cancel the download and bring the button back")
    lget.fetch = fetch_refused
    q("settingsPage.getLavender()")
    settle(800)
    if not bar.isVisible():
        failures.append("an instant refusal blinked the bar instead of holding it a moment")
    settle(1000)
    if q("settingsPage.lavGet") != "failed" or q("settingsPage.lavGetError") != lget.NOT_VERIFIED:
        failures.append(f"a refused download is not said in the drawer: {q('settingsPage.lavGetError')!r}")
    if bar.isVisible() or not get_btn.isVisible():
        failures.append("the refusal did not take the bar's place")
    lget.fetch = lambda *a, **k: saved
    q("settingsPage.getLavender()")
    settle(1900)
    file_url = QUrl.fromLocalFile(str(saved)).toString()
    if opened.urls[-1:] != [file_url]:
        failures.append(f"the saved installer was not opened: {opened.urls[-1:]!r}")
    if not drawer.isVisible() or not get_btn.isVisible() or q("settingsPage.lavGet") != "ready":
        failures.append("the drawer does not offer INSTALL once the installer is saved")
    shoot("ready")
    q("settingsPage.getLavender()")  # INSTALL opens the same installer again
    if opened.urls[-2:] != [file_url, file_url]:
        failures.append("INSTALL did not open the saved installer again")
    installs.append([sys.executable])
    q("settingsPage.lavTick++")  # what the drawer's timer does while the installer is open
    settle(700)
    if staged() is not True or drawer.isVisible() or q("settingsPage.lavGet") != "":
        failures.append(f"the new Lavender did not turn the asked-for key on: {staged()!r}")
    if bridge.settings.data.lyrics_use_lavender is not True:
        failures.append("the key the new Lavender turned on was not saved")
    lget.installer_pattern = lambda: None
    q("settingsPage.getLavender()")
    settle(200)
    if opened.urls[-1:] != ["https://getlavender.dev"] or q("settingsPage.lavGet") != "":
        failures.append(f"no build for this computer did not open Lavender's site: {opened.urls[-1:]!r}")

    # Saved on, then a download's start gets no hello: this install is too
    # old, and Settings says so the next time it opens.
    # (what SAVE CHANGES does: write, then re-read the fields on the next open)
    q("waves.applySettings(settingsPage.editMap)")
    q("settingsPage.needsRefresh = true")
    settle(300)
    lav.shared_client()._refused = (lav._stamp(installs[0]), True)
    q("root.settingsOpen = false")
    settle(300)
    q("root.settingsOpen = true")
    settle(500)
    tile, drawer, tag = tile_parts()
    if tile is None:
        failures.append("the tile is gone after reopening Settings")
    elif not drawer.isVisible() or tile.property("lavState") != "old":
        failures.append(
            f"an old Lavender does not open the drawer: (state {tile.property('lavState')!r},"
            f" key on {tile.property('childVal')!r}, client {lav.shared_client().state()!r})"
        )
    elif tile.property("childVal") is not True:
        failures.append("an old Lavender turned the saved key off")
    else:
        shoot("old")

    if failures:
        print("\n".join(failures), file=sys.stderr)
        return _EXIT_REGRESSED
    print("ok")
    return _EXIT_OK


if __name__ == "__main__" and "--run-scenario" in sys.argv:
    sys.exit(_run_scenario())
