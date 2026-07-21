"""Regression: Back to Browse restores your scroll position, never the top.

THE BUG WE ARE FENCING OFF
--------------------------
Going Back from a drilled page (a playlist, an album, any long listing) to the
Browse landing jumped the page to the top instead of the spot the user left.

The browse scroll restore is armed on Back (``browsePane.pendingRestoreY``) and
re-applied during layout. It disarmed itself once ``maxY >= pendingRestoreY``
("we reached the target"), but ``browsePane._pageKey`` is notified BEFORE the
section Repeater's model swaps, so on the page-key change ``contentHeight`` was
still the OUTGOING page's. A playlist taller than the saved position cleared
that check against a height the landing does not have yet, disarming the
restore. The landing's shelves then rebuild through asynchronous Loaders:
``contentHeight`` collapses (clamping ``contentY`` to the top) and the genuine
layout pass that should have re-applied the restore finds it already spent.

HOW THIS STAYS FIXED
--------------------
``applyRestore`` takes a ``mayDisarm`` flag: the page-key pass restores but may
NOT spend the restore; only a real layout pass (``onContentHeightChanged``) may.
The artist pane carries an identical copy of the mechanism and the same fix; it
is exercised through the same navigation code, so this browse-level guard
covers the shared logic.

HOW IT IS RUN
-------------
The scenario boots the REAL ``Main.qml`` and drives ``openBrowseItem`` ->
``navBack`` with a tall playlist over the async landing rebuild, asserting the
landing returns to the saved offset. It lands at the top (exit 1) on the pre-fix
tree and on the saved spot (exit 0) after. It runs in a SUBPROCESS: constructing
the bridge installs a process-global Qt message handler / diagnostics logging
that would otherwise leak into unrelated tests in the same interpreter (the
repo's other QML checks, e.g. scratchpad/gate_qml.py, run standalone for the
same reason). Promotion of ``scratchpad/browse_back_scroll_check.py``.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

# Exit codes the standalone scenario uses to talk back to the pytest wrapper.
_EXIT_RESTORED = 0  # Back landed on the saved spot: fix present.
_EXIT_REGRESSED = 1  # Back jumped to the top: the bug is back.
_EXIT_NO_QT = 77  # PySide6 / a usable Qt platform is unavailable: skip.
_EXIT_PRECONDITION = 78  # environment could not set up a scrollable scenario.

QML_MAIN = Path(__file__).resolve().parent.parent / "tidaler" / "waves_ui" / "qml" / "Main.qml"

# A fixed window size makes contentHeight/maxY deterministic across machines, so
# the saved offset the scenario restores to does not depend on the CI window size.
_WIN_W, _WIN_H = 1100, 720


# ===========================================================================
# pytest wrapper: run the scenario isolated, assert the outcome by exit code.
# ===========================================================================
def test_back_from_long_playlist_restores_browse_scroll():
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    # A throwaway config dir: the real WavesBridge writes settings/waves.json, and
    # a window-geometry save firing in the event loop must not clobber the user's
    # remembered window frame with the offscreen 0,0 placement.
    env["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="waves-backscroll-test-")

    # Fixed argv: this interpreter re-runs this very file (see S603 per-file-ignore).
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
        pytest.skip(f"could not build a scrollable scenario in this environment:\n{tail}")
    assert proc.returncode == _EXIT_RESTORED, (
        "Back to Browse did not restore the scroll position (scroll-restore "
        f"regression). Scenario exit={proc.returncode}:\n{tail}"
    )


# ===========================================================================
# Standalone scenario (runs in its own interpreter via the subprocess above).
# ===========================================================================
def _landing() -> dict:
    """Ten card shelves: a tall, scrollable Browse landing that rebuilds through
    asynchronous Loaders (the async rebuild is what collapses contentHeight on
    the way back and exposed the bug)."""

    def card(i: int, j: int) -> dict:
        return {"id": f"a{i}_{j}", "kind": "album", "title": f"Album {i}.{j}", "artist": f"Artist {j}"}

    return {
        "sections": [
            {"title": f"Shelf {i}", "rowKind": "cards", "items": [card(i, j) for j in range(12)]} for i in range(10)
        ],
        "genres": [],
        "moods": [],
        "decades": [],
        "error": False,
    }


def _playlist() -> dict:
    """A long track listing: taller than the saved landing offset, which is the
    precondition that let the stale-height check spend the restore early. No
    ``data``/``total`` keys, so browseCanGrow stays false and the endless-scroll
    path can never move contentY behind the scenario's back."""
    return {
        "key": "item:playlist:p1",
        "title": "Long Playlist",
        "header": {"title": "Long Playlist", "kind": "playlist"},
        "sections": [
            {
                "title": "Tracks",
                "rowKind": "tracks",
                "items": [
                    {
                        "id": f"t{n}",
                        "kind": "track",
                        "title": f"Track {n}",
                        "artist": f"Artist {n}",
                        "duration": "3:20",
                        "num": n + 1,
                    }
                    for n in range(120)
                ],
            }
        ],
        "error": False,
    }


def _run_scenario() -> int:
    # A deliberately linear boot -> drive -> assert scenario (C901 per-file-ignore).
    try:
        from PySide6.QtCore import QEventLoop, QTimer, QUrl
        from PySide6.QtGui import QGuiApplication
        from PySide6.QtQml import QQmlApplicationEngine, QQmlEngine, QQmlExpression
    except Exception as exc:
        print(f"Qt unavailable: {exc}", file=sys.stderr)
        return _EXIT_NO_QT

    app = QGuiApplication.instance() or QGuiApplication([])
    try:
        from tidaler.waves_ui.app import _load_mono
        from tidaler.waves_ui.backend import WavesBridge
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
        # Evaluate in Main.qml's own scope so its ids (browsePane, the nav
        # functions) resolve. PySide6 returns evaluate()'s valueIsUndefined
        # out-param as a tuple.
        ctx = QQmlEngine.contextForObject(root)
        e = QQmlExpression(ctx, root, expr)
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

    # 1. Land on Browse and let the async shelves finish building.
    root.setProperty("browseOpen", True)
    root.setProperty("browseSections", [])  # force the fresh-build (async) path
    bridge.browseLoaded.emit(_landing())
    if not pump(
        lambda: q("browsePageKey") == ""
        and not q("browseBuilding")
        and q("browsePane.contentHeight") > q("browsePane.height") + 200
    ):
        print("Browse landing never became scrollable", file=sys.stderr)
        return _EXIT_PRECONDITION
    settle()

    landing_ch = q("browsePane.contentHeight")
    landing_max = max(0.0, landing_ch - q("browsePane.height"))
    if landing_max <= 100:
        print("no scrollable landing in this environment", file=sys.stderr)
        return _EXIT_PRECONDITION

    # 2. Scroll partway down and remember the spot.
    saved = round(landing_max * 0.6)
    q(f"browsePane.contentY = {saved}")
    saved = q("browsePane.contentY")
    if saved <= 10:
        print("could not establish a non-top scroll offset", file=sys.stderr)
        return _EXIT_PRECONDITION

    # 3. Drill into a tall playlist: taller than the saved offset is the
    #    precondition that let the stale-height check disarm the restore.
    q('openBrowseItem("playlist", "p1")')
    bridge.browsePageLoaded.emit(_playlist())
    if not pump(
        lambda: q("browsePageKey") == "item:playlist:p1"
        and (q("browsePane.contentHeight") - q("browsePane.height")) > saved
    ):
        print("playlist page never grew taller than the saved position", file=sys.stderr)
        return _EXIT_PRECONDITION

    # 4. Back to Browse. The landing rebuilds through async Loaders, so wait for
    #    contentHeight to return to the landing's height (browseBuilding is not
    #    re-set on Back, so height, not the flag, is the settle signal).
    q("navBack()")
    if not pump(lambda: q("browsePageKey") == "" and abs(q("browsePane.contentHeight") - landing_ch) <= 1):
        print("Browse landing never rebuilt to its original height after Back", file=sys.stderr)
        return _EXIT_PRECONDITION
    settle()

    final = q("browsePane.contentY")
    restored = abs(final - saved) <= 2
    print(f"savedY={saved:.0f} finalY={final:.0f} restored={restored}", flush=True)
    return _EXIT_RESTORED if restored else _EXIT_REGRESSED


if __name__ == "__main__":
    raise SystemExit(_run_scenario())
