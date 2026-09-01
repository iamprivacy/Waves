"""Regression: reopening an already-keyed browse page still records history.

THE BUG WE ARE FENCING OFF
--------------------------
``browsePageKey`` survives leaving Browse via the nav tabs. ``openBrowseItem``
treated a matching key as "already there" and returned before ``navPush()``,
so reopening that page from ANOTHER surface (a playlist inside a My Tidal
folder, a Home shelf card) switched to the cached page without recording
where the user came from. Back then skipped the folder entirely and fell
through to whatever sat under it in the history (Search, typically), which
is exactly how it surfaced in livetesting issue #11's folder view.

HOW THIS STAYS FIXED
--------------------
The guard now pushes a snapshot whenever Browse is not the active surface
(the cached page is still reused, nothing is re-fetched). This scenario boots
the REAL Main.qml and walks the reported flow: open a playlist page, leave it
via the My Tidal tab, drill into a playlist folder, reopen the same playlist,
then assert one snapshot was pushed and that Back returns to the folder.

Runs in a SUBPROCESS for the same reason as test_browse_back_scroll: building
the bridge installs process-global handlers that must not leak into the rest
of the suite.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

_EXIT_OK = 0  # push recorded and Back landed in the folder
_EXIT_REGRESSED = 1  # the guard swallowed the history push again
_EXIT_NO_QT = 77
_EXIT_PRECONDITION = 78

QML_MAIN = Path(__file__).resolve().parent.parent / "waves" / "waves_ui" / "qml" / "Main.qml"


def test_reopening_keyed_page_from_folder_pushes_history():
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="waves-folderback-test-")
    proc = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), "--run-scenario"],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    tail = "\n".join((proc.stdout + proc.stderr).strip().splitlines()[-8:])
    import pytest

    if proc.returncode == _EXIT_NO_QT:
        pytest.skip("PySide6 / offscreen Qt unavailable")
    if proc.returncode == _EXIT_PRECONDITION:
        pytest.skip(f"could not set up the scenario in this environment:\n{tail}")
    assert (
        proc.returncode == _EXIT_OK
    ), f"Back from a reopened playlist page skipped the folder again. Scenario exit={proc.returncode}:\n{tail}"


def _run_scenario() -> int:
    # THIS checkout's waves, not the venv's editable install: the scenario
    # drives this tree's Main.qml against this tree's bridge (the folder slot
    # only exists here while the branch is unmerged).
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    try:
        from PySide6.QtCore import QEventLoop, QTimer, QUrl
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

    def settle(ms: int = 120) -> None:
        loop = QEventLoop()
        QTimer.singleShot(ms, loop.quit)
        loop.exec()

    settle()
    # 1. Open a playlist page, then leave it via the My Tidal nav tab: the
    #    browse key stays behind, which is the bug's precondition.
    q('openPlaylistPage("p1")')
    settle()
    q("openLibrary()")
    settle()
    if q("browsePageKey") != "item:playlist:p1":
        print("precondition lost: browsePageKey did not survive the tab switch", file=sys.stderr)
        return _EXIT_PRECONDITION

    # 2. Playlists -> a folder -> the SAME playlist again.
    q('loadLib("playlists")')
    settle()
    q('openPlFolder("f1", "Some Music")')
    settle()
    before = q("navHistory.length")
    q('openPlaylistPage("p1")')
    settle()
    pushed = q("navHistory.length") - before
    label = q("navBackLabel()")

    # 3. Back must land in the folder, not fall through the skipped snapshot.
    q("navBack()")
    settle()
    in_folder = bool(q("libraryOpen")) and q("plCurrentFolder") == "f1" and q("libraryCategory") == "playlists"

    print(f"pushed={pushed} backLabel={label!r} backInFolder={in_folder}", flush=True)
    return _EXIT_OK if pushed == 1 and label == "My Tidal" and in_folder else _EXIT_REGRESSED


if __name__ == "__main__":
    raise SystemExit(_run_scenario())
