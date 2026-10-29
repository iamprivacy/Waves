"""The app's scrollbar (WavesScrollBar, the LED column), on screen.

- No page keeps Qt's stock bar: every vertical bar is a WavesScrollBar.
- The Releases feed's bar sits in the margin beside the list (never over
  its cards) and past the window's top and bottom edge fades.
- Dragged, the bar's bubble names the section at the top of the view, and
  the column is fully lit only while it is in use.
- Settings' bar sits inside its page, at its right edge.

Runs in a SUBPROCESS like the other Main.qml scenarios.
"""

from __future__ import annotations

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


def test_the_waves_scrollbar_on_screen() -> None:
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    sandbox = tempfile.mkdtemp(prefix="waves-scrollbar-")
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
    assert proc.returncode == _EXIT_OK, f"the scrollbar is wrong on screen:\n{tail}"


def _rel(rid: str, day: str, title: str, artist: str, rtype: str = "album") -> dict:
    return {
        "kind": "album",
        "id": rid,
        "title": title,
        "artist": artist,
        "artist_id": "a-" + rid,
        "artists": [{"id": "a-" + rid, "name": artist}],
        "art": "",
        "date": day,
        "listed": "",
        "year": day[:4],
        "rtype": rtype,
        "explicit": False,
        "tracks": 10,
        "duration_sec": 200,
        "quality": "LOSSLESS",
    }


def _run_scenario() -> int:
    here = Path(__file__).resolve()
    sys.path.insert(0, str(here.parent.parent))
    sys.path.insert(0, str(here.parent))
    try:
        from PySide6.QtCore import QEventLoop, QPoint, Qt, QTimer, QUrl
        from PySide6.QtGui import QGuiApplication
        from PySide6.QtQml import QQmlApplicationEngine, QQmlEngine, QQmlExpression
        from PySide6.QtQuick import QQuickWindow  # noqa: F401  (the root reads as a QQuickWindow)
        from PySide6.QtTest import QTest
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

    import datetime as _datetime

    from waves.waves_ui import bridge_releases

    class _Day(_datetime.date):
        @classmethod
        def today(cls):
            return cls(2026, 10, 14)

    bridge_releases._dt = type("_Dt", (), {"date": _Day})

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
    root.setProperty("width", 1280)
    root.setProperty("height", 1200)

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

    failures: list[str] = []
    settle()
    q(PARK_LOGIN_QML)
    q("bootOverlay.done = true")
    q("bootContentShown = 1")
    q("newCutoffClock.running = false")
    store = bridge._releases
    bridge._waves_prefs["releases_window_months"] = "12"
    for n in range(80):
        aid, name = f"a{n:02d}", f"Artist {n:02d}"
        store.follow(aid, name, "")
        store.record_check(
            aid,
            [_rel(f"{aid}r{k}", f"2026-{1 + (n + k) % 9:02d}-{1 + k:02d}", f"T{k}", name) for k in range(4)],
        )

    q("openReleases()")
    if not pump(lambda: q("releasesModel.count") > 0 and not q("releasesLoadingMore")):
        print("the feed never arrived", file=sys.stderr)
        return _EXIT_PRECONDITION
    settle(400)

    bar = "relFeedList.ScrollBar.vertical"

    def box(expr: str) -> list[float]:
        return [
            float(v)
            for v in str(
                q(
                    f"(function () {{ var it = {expr}; var p = it.mapToItem(null, 0, 0);"
                    " return [p.x, p.y, it.width, it.height].join(',') })()"
                )
            ).split(",")
        ]

    if not q(f"{bar}.visible"):
        print("the seeded feed does not scroll", file=sys.stderr)
        return _EXIT_PRECONDITION

    # 1. The feed's bar is the Waves bar, beside the list, never over it.
    if q(f"{bar}.objectName") != "wavesScrollBar":
        failures.append(f"the feed's bar is {q(bar + '.objectName')!r}, not the Waves bar")
    lx, ly, lw, lh = box("relFeedList")
    bx, by, bw, bh = box(bar)
    if bx < lx + lw:
        failures.append(f"the feed's bar starts at x {bx:.0f}, over the list (which ends at {lx + lw:.0f})")
    if abs(by - ly) > 1 or abs(bh - lh) > 1:
        failures.append(f"the feed's bar runs {by:.0f}+{bh:.0f}, not the list's {ly:.0f}+{lh:.0f}")

    # 2. The edge fades stop short of it, scrolled to the middle (both up).
    q("relFeedList.contentY = relFeedList.originY + (relFeedList.contentHeight - relFeedList.height) / 2")
    settle(300)
    fades = str(q("""(function () {
        var out = [], d = scrollDressing
        for (var i = 0; i < d.children.length; i++) {
            var c = d.children[i]
            if (c.gradient && c.visible)  // the bottom one is turned 180: map both corners
                out.push(Math.round(Math.max(c.mapToItem(null, 0, 0).x, c.mapToItem(null, c.width, 0).x)))
        }
        return out.join(',')
    })()"""))
    rights = [int(v) for v in fades.split(",") if v]
    if len(rights) != 2:
        failures.append(f"wanted the two edge fades up mid-page, saw {rights}")
    elif max(rights) > bx:
        failures.append(f"an edge fade runs to x {max(rights)}, over the bar at {bx:.0f}")

    # 3. Idle, the column is dim; dragged, it lights and its bubble names
    # the section at the top of the view.
    q("relFeedList.contentY = relFeedList.originY")
    settle(1200)
    if abs(float(q(f"{bar}.background.opacity")) - 0.5) > 0.01:
        failures.append(f"the idle column reads opacity {q(bar + '.background.opacity')}, wanted 0.5")
    win = root
    thumb_y = by + float(q(f"{bar}.size")) * bh / 2
    start = QPoint(int(bx + bw / 2), int(thumb_y))
    QTest.mousePress(win, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start)
    for k in range(1, 9):
        QTest.mouseMove(win, QPoint(start.x(), int(thumb_y + k * bh / 20)))
        settle(30)
    settle(250)
    want = str(q("relFeedSectionAt(relFeedList.contentY - relFeedList.originY)"))
    bubble = str(q(f"{bar}.section"))
    shown = float(q(f"{bar}.contentItem.children[0].opacity"))
    if not q(f"{bar}.pressed"):
        failures.append("pressing the lit run did not take hold of the bar")
    if float(q("relFeedList.contentY - relFeedList.originY")) < 100:
        failures.append("dragging the bar did not scroll the feed")
    if not want or bubble != want:
        failures.append(f"the dragged bar's bubble says {bubble!r}, the view's top is in {want!r}")
    if shown < 0.99:
        failures.append(f"the bubble is at opacity {shown} while dragged")
    if abs(float(q(f"{bar}.background.opacity")) - 1) > 0.01:
        failures.append("the column is not fully lit while dragged")
    QTest.mouseRelease(
        win, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(start.x(), int(thumb_y + 8 * bh / 20))
    )
    settle(400)
    if float(q(f"{bar}.contentItem.children[0].opacity")) > 0.01:
        failures.append("the bubble stays up after the drag")

    # 4. Settings' bar sits inside its page, at the page's right edge.
    q("settingsOpen = true; artistOpen = false; libraryOpen = false; releasesOpen = false; browseOpen = false")
    settle(600)
    sbar = "settingsPage.scroller.ScrollBar.vertical"
    if q(f"{sbar}.objectName") != "wavesScrollBar":
        failures.append("Settings keeps a stock bar")
    elif q(f"{sbar}.visible"):
        sx, _sy, sw, _sh = box("settingsPage.scroller")
        tx, _ty, tw, _th = box(sbar)
        if abs((tx + tw) - (sx + sw)) > 1:
            failures.append(f"Settings' bar ends at x {tx + tw:.0f}, not the page's right edge {sx + sw:.0f}")

    if failures:
        print("\n".join(failures), file=sys.stderr)
        return _EXIT_REGRESSED
    print("ok")
    return _EXIT_OK


def test_no_stock_scrollbar_is_left() -> None:
    qml = Path(__file__).resolve().parent.parent / "waves" / "waves_ui" / "qml"
    for name in ("Main.qml", "SettingsPage.qml"):
        src = (qml / name).read_text(encoding="utf-8")
        assert "ScrollBar.vertical: ScrollBar" not in src, f"{name} keeps a stock vertical bar"


if __name__ == "__main__" and "--run-scenario" in sys.argv:
    sys.exit(_run_scenario())
