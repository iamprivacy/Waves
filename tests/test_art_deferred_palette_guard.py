"""A cover torn down with its palette ask still pending stays quiet.

A cover whose URL changes asks for its glow colors on the next turn of the
event loop (Qt.callLater), because a cached cover keeps its Image Ready and no
other change would ask. A report from Linux showed the deferred call landing
on a cover whose context was already gone ("Property '_fxPalFetch' of object
Art is not a function"). A truthiness or typeof check on the item does not
help: an item whose context is gone still reads as an object. The ask now
carries a token the cover marks on destruction, and both the deferred call
and the grab's callback check it.

The scenario tears covers down both ways the app does (a section emptied,
a Loader letting its item go) with the ask pending and asserts nothing is
logged. The exact ordering the report hit depends on the platform's event
loop and did not reproduce on macOS, so the source check pins the guard.

Offscreen, in a subprocess, with the real bridge and a temp config.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

_EXIT_OK = 0
_EXIT_REGRESSED = 1
_EXIT_NO_QT = 77
_EXIT_PRECONDITION = 78

QML_MAIN = Path(__file__).resolve().parent.parent / "waves" / "waves_ui" / "qml" / "Main.qml"


def test_the_deferred_palette_ask_checks_a_destruction_token():
    src = QML_MAIN.read_text(encoding="utf-8")
    start = src.index("component Art: Rectangle {")
    body = src[start : start + 40000]
    assert "Component.onDestruction: _life.gone = true" in body
    url_changed = re.search(r"onUrlChanged: \{[^\n]*\}", body).group(0)
    assert "life.gone" in url_changed, url_changed
    fetch = body[body.index("function _fxPalFetch()") :]
    fetch = fetch[: fetch.index("onFxRaisedChanged")]
    assert "life.gone" in fetch, "the grab callback does not check the token"


def test_a_cover_destroyed_with_its_palette_ask_pending_logs_nothing():
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="waves-art-palette-guard-test-")
    proc = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), "--run-scenario"],  # noqa: S603 (fixed argv)
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    tail = "\n".join((proc.stdout + proc.stderr).strip().splitlines()[-14:])
    import pytest

    if proc.returncode == _EXIT_NO_QT:
        pytest.skip("PySide6 / offscreen Qt unavailable")
    if proc.returncode == _EXIT_PRECONDITION:
        pytest.skip(f"could not set up the scenario in this environment:\n{tail}")
    assert proc.returncode == _EXIT_OK, f"a torn-down cover logged. Scenario exit={proc.returncode}:\n{tail}"


def _run_scenario() -> int:  # noqa: C901 (one straight scenario)
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    try:
        from PySide6.QtCore import QEventLoop, QTimer, QUrl, qInstallMessageHandler
        from PySide6.QtGui import QGuiApplication
        from PySide6.QtQml import QQmlApplicationEngine, QQmlEngine, QQmlExpression
    except Exception as exc:
        print(f"Qt unavailable: {exc}", file=sys.stderr)
        return _EXIT_NO_QT

    app = QGuiApplication.instance() or QGuiApplication([])
    try:
        from waves.waves_ui.app import _load_mono
        from waves.waves_ui.backend import WavesBridge
    except Exception as exc:
        print(f"Qt platform/backend unavailable: {exc}", file=sys.stderr)
        return _EXIT_NO_QT

    messages: list[str] = []
    qInstallMessageHandler(lambda _mode, _ctx, msg: messages.append(msg))

    engine = QQmlApplicationEngine()
    bridge = WavesBridge(tidal=None)
    engine.rootContext().setContextProperty("waves", bridge)
    engine.rootContext().setContextProperty("monoFont", _load_mono())
    engine.rootContext().setContextProperty("uiFontFamily", app.font().family())
    engine.load(QUrl.fromLocalFile(str(QML_MAIN)))
    roots = engine.rootObjects()
    if not roots:
        print("Main.qml failed to load:\n" + "\n".join(messages[-10:]), file=sys.stderr)
        return _EXIT_PRECONDITION
    root = roots[0]
    root.setProperty("width", 1400)
    root.setProperty("height", 900)

    def q(expr: str):
        e = QQmlExpression(QQmlEngine.contextForObject(root), root, expr)
        r = e.evaluate()
        if e.hasError():
            raise RuntimeError(e.error().toString())
        return r[0] if isinstance(r, tuple) else r

    def settle(ms: int = 150) -> None:
        loop = QEventLoop()
        QTimer.singleShot(ms, loop.quit)
        loop.exec()

    settle()
    q("bootOverlay.done = true")
    q("bootContentShown = 1")
    q("browseBuilding = false")
    q("_browseAsyncBuild = false")
    q('browseStyle = "art"')

    def fill(round_no: int) -> None:
        items = [
            {
                "id": f"r{round_no}a{i}",
                "kind": "album",
                "title": f"Album {i}",
                "artist": "Someone",
                "year": "2020",
                "tracks": 10,
                "art": "",
            }
            for i in range(6)
        ]
        root.setProperty("browseSections", [{"title": "ALBUMS", "rowKind": "cards", "data": "", "items": items}])

    # Every cover under the landing changes its URL (each schedules its
    # deferred palette ask) and, in the same turn, the section goes away.
    tear = """
    (function() {
        var n = 0
        function walk(item) {
            if (!item) return
            var kids = item.children || []
            for (var i = 0; i < kids.length; i++) {
                var k = kids[i]
                if (k && k.fxPal !== undefined && k.palWanted !== undefined && k.url !== undefined) {
                    k.url = "file:///nowhere/cover-" + n + ".jpg"
                    n++
                }
                walk(k)
            }
        }
        walk(browseLanding)
        browseSections = []
        return n
    })()
    """

    torn = 0
    for round_no in range(4):
        fill(round_no)
        settle(700)
        before = len(messages)
        torn += int(q(tear) or 0)
        settle(400)
        del before
    # A Loader that lets its item go clears the item's context at once and
    # deletes the object later: the state the report's TypeError comes from
    # (the object is still there, its functions are not).
    qml_dir = QML_MAIN.parent.as_uri()
    loader = q(
        "Qt.createQmlObject("
        f'\'import QtQuick; import "{qml_dir}"; Loader {{ width: 80; height: 80; '
        "sourceComponent: Component { Main.Art { width: 80; height: 80 } } }', browseLanding)"
    )
    loaded = 0
    for i in range(6):
        if loader is None:
            break
        loader.setProperty("active", True)
        settle(200)
        item = loader.property("item")
        if item is None:
            break
        # No event loop turn between the two: the deferred ask is pending
        # when the Loader lets the cover go.
        item.setProperty("url", f"file:///nowhere/loaded-{i}.jpg")
        loader.setProperty("active", False)
        loaded += 1
        settle(300)
    torn += loaded
    if torn == 0 or loaded == 0:
        print(f"found nothing to tear down (cards {torn - loaded}, loader {loaded})", file=sys.stderr)
        print("\n".join(messages[-6:]), file=sys.stderr)
        return _EXIT_PRECONDITION

    noisy = [m for m in messages if "_fxPalFetch" in m or "artRoot" in m or "fxPal" in m]
    if noisy:
        print(f"{len(noisy)} warning(s) from {torn} torn-down covers:", file=sys.stderr)
        print("\n".join(sorted(set(noisy))[:6]), file=sys.stderr)
        return _EXIT_REGRESSED
    print(f"ok: {torn} covers torn down with a pending palette ask, nothing logged")
    return _EXIT_OK


if __name__ == "__main__":
    if "--run-scenario" in sys.argv:
        raise SystemExit(_run_scenario())
    raise SystemExit(_EXIT_PRECONDITION)
