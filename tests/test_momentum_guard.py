"""A fast trackpad swipe never throws a page back to where it began.

macOS sends a swipe's end as the fingers lift unless the momentum's first
event is already queued; on a busy frame it is not, and the momentum then
arrives after the end. A Flickable alone takes that late momentum for a new
drag from where the swipe began and snaps back there (Qt 6.11). Every page
that scrolls up and down wears a MomentumGuard against it. The scenario
swipes several pages (Settings, the search results, My Tidal's home) once
with the momentum on time and once late, and proves the late one never moves
backwards and lands where the on-time one does.

Runs in a SUBPROCESS like the other Main.qml scenarios: building the bridge
installs process-global handlers that must not leak into the suite.
"""

from __future__ import annotations

import itertools
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


def test_a_late_momentum_never_throws_a_page_back() -> None:
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    sandbox = tempfile.mkdtemp(prefix="waves-momentum-guard-")
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
    assert proc.returncode == _EXIT_OK, f"a page snapped back on a late momentum:\n{tail}"
    # The guards are parented to their pages; nothing tries to stack them
    # as siblings of what made them (a warning per guard at every launch).
    assert "Cannot stack" not in proc.stdout + proc.stderr


# Where an item sits in the window: "x,y,width,height".
_BOX = """(function () {
    var p = %s.mapToItem(null, 0, 0)
    return [Math.round(p.x), Math.round(p.y), Math.round(%s.width), Math.round(%s.height)].join(",")
})()"""


def _run_scenario() -> int:  # noqa: C901
    here = Path(__file__).resolve()
    sys.path.insert(0, str(here.parent.parent))
    sys.path.insert(0, str(here.parent))
    try:
        from PySide6.QtCore import QEventLoop, QPoint, QPointF, Qt, QTimer, QUrl
        from PySide6.QtGui import QGuiApplication, QWheelEvent
        from PySide6.QtQml import QQmlApplicationEngine, QQmlEngine, QQmlExpression
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
    root.setProperty("height", 700)

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

    def box(item: str) -> tuple[int, int, int, int]:
        x, y, w, h = (int(v) for v in q(_BOX % (item, item, item)).split(","))
        return x, y, w, h

    def swipe(flick: str, momentum_late: bool) -> list[int]:
        """A fast swipe down over `flick`; its contentY after every event."""
        x, y, w, h = box(flick)
        px, py = x + w // 2, y + h // 2
        ys: list[int] = []

        def send(phase, d: int) -> None:
            ev = QWheelEvent(
                QPointF(px, py),
                QPointF(px, py),
                QPoint(0, d),
                QPoint(0, d * 2),
                Qt.MouseButton.NoButton,
                Qt.KeyboardModifier.NoModifier,
                phase,
                False,
                Qt.MouseEventSource.MouseEventSynthesizedBySystem,
            )
            QGuiApplication.sendEvent(root, ev)
            settle(16)
            ys.append(round(q(f"{flick}.contentY")))

        send(Qt.ScrollPhase.ScrollBegin, 0)
        for _ in range(6):
            send(Qt.ScrollPhase.ScrollUpdate, -40)
        if momentum_late:
            send(Qt.ScrollPhase.ScrollEnd, 0)
        for k in range(20):
            send(Qt.ScrollPhase.ScrollMomentum, round(-40 * (1 - k / 20)))
        send(Qt.ScrollPhase.ScrollEnd, 0)
        settle(300)
        return ys

    def check(page: str, flick: str) -> None:
        q(f"{flick}.contentY = {flick}.originY")
        settle(200)
        on_time = swipe(flick, False)
        if on_time[-1] - int(q(f"{flick}.originY")) < 300:
            failures.append(f"{page}: an on-time swipe did not scroll ({on_time[-1]})")
            return
        q(f"{flick}.contentY = {flick}.originY")
        settle(200)
        late = swipe(flick, True)
        back = [(a, b) for a, b in itertools.pairwise(late) if b < a - 2]
        travel = on_time[-1] - int(q(f"{flick}.originY"))
        if back or abs(late[-1] - on_time[-1]) > travel / 10:
            failures.append(f"{page}: a late momentum snapped back {back[:2]}, ended {late[-1]} vs {on_time[-1]}")

    failures: list[str] = []
    settle()
    q(PARK_LOGIN_QML)
    q("bootOverlay.done = true")
    q("bootContentShown = 1")
    q("newCutoffClock.running = false")

    # Settings scrolls its own cards. It shows one section at a time, so pick
    # the longest one (Files & Naming) to have something to swipe through.
    q("settingsOpen = true; artistOpen = false; libraryOpen = false; releasesOpen = false; browseOpen = false")
    settle(300)
    q('settingsPage.showSection("files")')
    settle(600)
    check("Settings", "settingsPage.scroller")

    # The search results and My Tidal's home, made tall (no account here).
    q("settingsOpen = false")
    settle(300)
    q("results.contentHeight = 20000")
    check("Search results", "results")
    q("libraryCategory = 'home'; libraryOpen = true")
    settle(300)
    q("homePane.contentHeight = 20000")
    check("My Tidal home", "homePane")

    # Every vertical page wears one, the playlist folder view included.
    names = q(
        "(function () { var out = []; var ps = [libFolderList, libPlaylistsList, relFeedList, queueList];"
        " for (var i = 0; i < ps.length; i++) { var k = ps[i].children, n = 0;"
        " for (var j = 0; j < k.length; j++) if (('' + k[j].objectName).indexOf('momentumGuard_') === 0) n++;"
        " out.push(n) } return out.join(',') })()"
    )
    if names != "1,1,1,1":
        failures.append(f"a page wears {names} guards, wanted one each")
    # An angle-only notch (no pixels) after a swipe is not late momentum:
    # the guard leaves it to the page instead of moving it by zero.
    q("homePane.contentY = homePane.originY")
    settle(200)
    x, y, w, h = box("homePane")
    for phase, px, ang in (
        (Qt.ScrollPhase.ScrollBegin, 0, 0),
        (Qt.ScrollPhase.ScrollEnd, 0, 0),
        (Qt.ScrollPhase.ScrollUpdate, 0, -120),
    ):
        QGuiApplication.sendEvent(
            root,
            QWheelEvent(
                QPointF(x + w // 2, y + h // 2),
                QPointF(x + w // 2, y + h // 2),
                QPoint(0, px),
                QPoint(0, ang),
                Qt.MouseButton.NoButton,
                Qt.KeyboardModifier.NoModifier,
                phase,
                False,
                Qt.MouseEventSource.MouseEventNotSynthesized,
            ),
        )
        settle(30)
    settle(400)
    if round(q("homePane.contentY")) <= round(q("homePane.originY")):
        failures.append("an angle-only wheel notch after a swipe did not scroll the page")

    noise = [w for w in warnings if "Guard" in w]
    if noise:
        failures.append("QML warnings: " + "; ".join(noise[:3]))

    if failures:
        print("\n".join(failures), file=sys.stderr)
        return _EXIT_REGRESSED
    print("ok")
    return _EXIT_OK


if __name__ == "__main__" and "--run-scenario" in sys.argv:
    sys.exit(_run_scenario())
