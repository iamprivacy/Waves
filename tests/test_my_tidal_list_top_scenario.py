"""A My Tidal tab lands at its true top, the 8px header in view.

WHAT THIS FENCES OFF
--------------------
The My Tidal lists carry an 8px header inside the scroll area, which puts a
ListView's top at ``originY`` (-8), not at contentY 0. A fresh load used to
scroll the list to a raw 0, so every tab opened 8px down: the first row sat
tight under the tab bar and the list still scrolled up by a hair. On the real
Main.qml, for a ListView tab (Albums) and the GridView tab (Artists):

1. A fresh load lands at the top: ``contentY == originY``.
2. A revalidate pin taken at the top restores to the top, not 8px down.

Rows are injected by emitting ``libraryLoaded`` from Python, offline, in a
SUBPROCESS like the other Main.qml scenarios.
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


def test_my_tidal_tab_lands_at_its_true_top():
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="waves-lib-top-test-")
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
    assert proc.returncode == _EXIT_OK, f"My Tidal list top regressed. Scenario exit={proc.returncode}:\n{tail}"


def _run_scenario() -> int:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    try:
        from PySide6.QtCore import QEventLoop, QTimer, QUrl
        from PySide6.QtGui import QGuiApplication
        from PySide6.QtQml import QQmlApplicationEngine, QQmlEngine, QQmlExpression
    except Exception as exc:
        print(f"Qt unavailable: {exc}", file=sys.stderr)
        return _EXIT_NO_QT

    from _qml_offline import PARK_LOGIN_QML, patch_offline

    patch_offline()

    app = QGuiApplication.instance() or QGuiApplication([])
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
    if not roots:
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

    settle(120)
    q(PARK_LOGIN_QML)
    root.setProperty("width", 1100)
    root.setProperty("height", 760)

    albums = [
        {
            "id": str(i),
            "title": f"Album {i}",
            "artist": "A",
            "art": "",
            "year": "2020",
            "date": "",
            "tracks": 10,
            "quality": "",
            "popularity": 0,
        }
        for i in range(60)
    ]
    artists = [{"id": str(i), "name": f"Artist {i}", "art": ""} for i in range(60)]
    verdicts: dict[str, bool] = {}
    for cat, view, rows in (("albums", "libAlbumsList", albums), ("artists", "libArtistsGrid", artists)):
        q(f'libraryOpen = true; libraryCategory = "{cat}"')
        settle(60)
        bridge.libraryLoaded.emit(cat, rows, False)
        settle(150)
        origin = float(q(f"{view}.originY"))
        top = float(q(f"{view}.contentY"))
        verdicts[f"{cat}_header"] = origin < 0
        verdicts[f"{cat}_fresh_top"] = abs(top - origin) < 0.5
        # A pinned revalidate taken at the top keeps the top.
        q("libPinRefill = true")
        bridge.libraryLoaded.emit(cat, list(reversed(rows)), False)
        settle(150)
        verdicts[f"{cat}_pinned_top"] = abs(float(q(f"{view}.contentY")) - float(q(f"{view}.originY"))) < 0.5
        q("libPinRefill = false")

    print(" ".join(f"{k}={v}" for k, v in verdicts.items()), flush=True)
    return _EXIT_OK if all(verdicts.values()) else _EXIT_REGRESSED


if __name__ == "__main__":
    raise SystemExit(_run_scenario())
