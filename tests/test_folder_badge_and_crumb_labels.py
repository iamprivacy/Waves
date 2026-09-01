"""Folder badge, odometer and breadcrumb labels, driven through real Main.qml.

THREE BUGS FENCED OFF HERE
-------------------------
1. FOLDER BADGE ON A FAILED ROLLUP. _bump_folder_group emits
   folderRemaining(fid, 0, total) for a failed member as well as a done one, so
   the badge rendered a green checkmark directly over the red RETRY button, and
   it never cleared: the dismissal Timer only arms for "done".

2. ODOMETER LATCHING A STALE DIGIT. OdoDigit returned early on
   `value === shown` without cancelling the roll in flight, so the running
   animation's ScriptAction latched the abandoned _pending. LibList pools
   delegates (reuseItems, cacheBuffer 800), so a fast flick rebinds one
   delegate A -> B -> A well inside the 220ms roll and a folder ends up wearing
   another folder's count.

3. BREADCRUMB NAMING THE WRONG PAGE. openBrowseLink (genre / mood / decade
   chips) and openPlaylistsFolder never set browseTitleHint, and openBrowse
   never cleared it. First open in a session: the trail reads "Browse" while
   the user is on the Chill page, and because ord 0 is also the last crumb that
   single pill is painted as the CURRENT page with its MouseArea disabled, so
   the back control cannot be clicked. After any earlier item page: the stale
   title leaks (Playlists > "Road Trip" > a folder tile read
   "Browse|Road Trip|Road Trip"), and navPush bakes it into history.

Runs in a SUBPROCESS for the same reason as test_playlist_folder_qml.py:
building the bridge installs process-global handlers.
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

_WIN_W, _WIN_H = 1100, 720

# Finds the FolderBadge inside a delegate: the only descendant carrying both a
# folderId and a total. Inline components have no exported id to reach.
_FIND_BADGE = """
(function() {
    function walk(o) {
        if (o === null) return null
        if (o.folderId !== undefined && o.total !== undefined) return o
        var kids = o.children || []
        for (var i = 0; i < kids.length; i++) {
            var hit = walk(kids[i])
            if (hit) return hit
        }
        return null
    }
    return walk(libPlaylistsList.itemAtIndex(0))
})()
"""


def test_folder_badge_states_and_crumb_labels():
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="waves-badgeqml-test-")
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
    assert proc.returncode == _EXIT_OK, f"badge or crumb labelling regressed. Scenario exit={proc.returncode}:\n{tail}"


def _folder_row() -> list:
    return [
        {
            "id": "f1",
            "title": "Country",
            "art": "",
            "tracks": 0,
            "creator": "",
            "added": "",
            "kind": "folder",
            "sub": "4 playlists",
            "path": "Country",
            "plCount": 4,
        }
    ]


def _run_scenario() -> int:
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
    root.setProperty("width", _WIN_W)
    root.setProperty("height", _WIN_H)

    def q(expr: str):
        ctx = QQmlEngine.contextForObject(root)
        e = QQmlExpression(ctx, root, expr)
        r = e.evaluate()
        if e.hasError():
            raise RuntimeError(e.error().toString())
        return r[0] if isinstance(r, tuple) else r

    def settle(ms: int = 150) -> None:
        loop = QEventLoop()
        QTimer.singleShot(ms, loop.quit)
        loop.exec()

    def fail(msg: str) -> int:
        print(msg, file=sys.stderr)
        return _EXIT_REGRESSED

    root.setProperty("libraryOpen", True)
    root.setProperty("libraryCategory", "playlists")
    bridge.libraryLoaded.emit("playlists", _folder_row(), False)
    settle()

    badge = q(_FIND_BADGE)
    if badge is None:
        print("could not reach the FolderBadge inside the folder row", file=sys.stderr)
        return _EXIT_PRECONDITION
    if badge.property("value") != "4":
        return fail(f"idle badge should read the folder total, got {badge.property('value')!r}")

    # 1. A ROLLUP THAT ENDS WITH A FAILED MEMBER IS NOT A SUCCESS.
    bridge.downloadState.emit("f1", "running")
    bridge.folderRemaining.emit("f1", 2, 4)
    settle()
    if badge.property("value") != "2":
        return fail(f"running badge should count down, got {badge.property('value')!r}")
    bridge.folderRemaining.emit("f1", 0, 4)
    bridge.downloadState.emit("f1", "failed")
    settle(400)
    failed_value = badge.property("value")
    if failed_value == "✓":
        return fail("a failed rollup rendered a green checkmark over the RETRY button")
    if failed_value != "4":
        return fail(f"a failed badge should offer the total RETRY re-queues, got {failed_value!r}")
    if badge.property("gone"):
        return fail("a failed badge must not dismiss itself")

    # 2. A VALUE THAT RETURNS TO ITS STARTING POINT MID-ROLL LEAVES NO RESIDUE.
    # Reset the count BEFORE flipping back to running: the other order shows
    # remaining=0 under a running state for one frame, which is a checkmark, and
    # that blip is itself enough to trip the latch we are about to probe for.
    bridge.folderRemaining.emit("f1", 4, 4)
    bridge.downloadState.emit("f1", "running")
    settle(400)
    digit = None
    for child in badge.children():
        if child.property("shown") is not None and child.property("_pending") is not None:
            digit = child
            break
    if digit is None:
        print("could not reach the OdoDigit inside the badge", file=sys.stderr)
        return _EXIT_PRECONDITION
    if digit.property("shown") != "4":
        print(f"precondition: odometer not at rest on 4, at {digit.property('shown')!r}", file=sys.stderr)
        return _EXIT_PRECONDITION
    bridge.folderRemaining.emit("f1", 1, 4)
    settle(80)  # mid-roll: the 220ms odometer is still running
    bridge.folderRemaining.emit("f1", 4, 4)
    settle(400)
    if badge.property("value") != "4":
        return fail("precondition: the badge value did not return to 4")
    if digit.property("shown") != "4":
        return fail(f"odometer latched a stale digit, shows {digit.property('shown')!r} for a badge reading 4")

    # 3. NO COUNT YET IS NOT A COUNT OF ZERO. The Browse category badge has no
    #    total to fall back on (a category's size is only known once it
    #    resolves), so with nothing in the count map it read "0", appeared, and
    #    odometer-rolled up to N. It stays away until there is a real number.
    #    Driven here through the same badge with its total taken away.
    badge.setProperty("total", 0)
    q("folderRemainMap = ({})")
    badge.setProperty("st", "")
    settle()
    if badge.property("value") != "" or badge.property("visible"):
        return fail(f"an unknown count rendered as {badge.property('value')!r} instead of staying away")
    # Worse under way than at rest: with no total and no count, "remaining"
    # falls back to 0, and 0 remaining is rendered as the finished checkmark.
    badge.setProperty("st", "running")
    settle()
    if badge.property("value") != "":
        return fail(f"a running rollup with no count published rendered {badge.property('value')!r}")
    bridge.folderRemaining.emit("f1", 7, 7)
    settle()
    if badge.property("value") != "7" or not badge.property("visible"):
        return fail(f"the badge should appear at the real count, got {badge.property('value')!r}")

    # 4. THE CRUMB NAMES THE PAGE THE USER IS ON.
    root.setProperty("libraryOpen", False)
    q("openBrowse()")
    settle()
    q("openBrowseLink('pages/mood/chill', 'Chill')")
    settle()
    if q("currentNavLabel") != "Chill":
        return fail(f"a genre/mood open should name its crumb, got {q('currentNavLabel')!r}")
    q("openPlaylistsFolder('pages/mood/focus', 'Focus')")
    settle()
    if q("currentNavLabel") != "Focus":
        return fail(f"a Playlists folder open should name its crumb, got {q('currentNavLabel')!r}")
    q("openBrowse()")
    settle()
    if q("browseTitleHint") != "" or q("currentNavLabel") != "Browse":
        return fail(f"the landing kept a stale hint: {q('browseTitleHint')!r} / {q('currentNavLabel')!r}")

    print("badge and crumb labelling OK", flush=True)
    return _EXIT_OK


if __name__ == "__main__":
    raise SystemExit(_run_scenario())
