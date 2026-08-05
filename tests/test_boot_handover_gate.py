"""The launch sequence must cover the Browse landing's assembly.

WHAT THIS FENCES OFF
--------------------
Browse landing shelves incubate through asynchronous Loaders, and the
"open water" launch overlay exists precisely to hide that assembly: the
wordmark holds over the water while the page builds behind it, so the
interface appears finished. The overlay used to lift on a fixed schedule
regardless, and a landing still building (a boot-time revalidation of a
dozen shelves) was then watched dropping in shelf by shelf, as if the page
were scrolling itself (reported from livetesting).

HOW THIS STAYS FIXED
--------------------
bootOverlay.handover() refuses to start the zoom while root.browseBuilding
is true, marking itself held; the build veil clearing calls it again. A cap
timer expires the hold so a stalled build can never pin the launch screen.

Runs in a SUBPROCESS for the same reason as test_search_results_surface_steal:
building the bridge installs process-global handlers that must not leak into
the rest of the suite.
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

QML_MAIN = Path(__file__).resolve().parent.parent / "tidaler" / "waves_ui" / "qml" / "Main.qml"


def test_boot_overlay_waits_for_the_landing_build():
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="waves-boot-gate-test-")
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
    assert proc.returncode == _EXIT_OK, f"the launch handover gate regressed. Scenario exit={proc.returncode}:\n{tail}"


def _run_scenario() -> int:
    # THIS checkout's tidaler, not the venv's editable install.
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
        from tidaler.waves_ui.app import _load_mono
        from tidaler.waves_ui.backend import WavesBridge
    except Exception as exc:
        print(f"Qt platform/backend unavailable: {exc}", file=sys.stderr)
        return _EXIT_NO_QT

    class _QuietBridge(WavesBridge):
        # Flipping loggedIn mid-scenario makes the QML login handler call
        # loadBrowse(), a real network fetch whose asynchronous failure would
        # race the data-wait pin below (its error payload releases the very
        # hold being asserted). Landing payloads in this scenario are always
        # driven by hand, so the fetch is silenced at the source.
        def loadBrowse(self) -> None:
            return

    engine = QQmlApplicationEngine()
    bridge = _QuietBridge(tidal=None)
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
    # Put the overlay back to its launch state. The sequence runs on its own in
    # this headless scenario (the readiness cap starts it), so silence every
    # piece of it first: the gate is then driven purely by this scenario, and
    # anything that happens downstream provably came from the release below.
    q("bootSeq.stop()")
    q("bootHandover.stop()")
    q("bootBlk.stop()")
    q("bootZoom.stop()")
    q("handoverCap.stop()")
    q("handoverCap.expired = false")
    q("bootVer.shown = 1")
    q("bootOverlay.done = false")
    q("bootContentShown = 0")
    # Raise the build veil, as a landing assembly does.
    q("browseBuilding = true")
    settle()

    # The handover must refuse while the landing is still assembling.
    q("bootOverlay.handover()")
    settle(900)  # long enough for the version drain to have finished
    held = bool(q("bootOverlay.handoverHeld")) and not bool(q("bootOverlay.done"))
    # The version readout must still be up: draining it signals "handing over
    # now", so it belongs downstream of the gate, welded to the zoom that
    # follows it. Holding with the version already emptied out left the launch
    # screen sitting there mid-signal (reported from livetesting).
    held = held and q("bootVer.shown") == 1

    # The veil clearing releases it, and the whole handover runs: the drain
    # (nothing else can produce it now) and then the zoom.
    q("browseBuilding = false")
    settle(3000)
    handed = bool(q("bootOverlay.done")) and q("bootContentShown") == 1
    drained = q("bootVer.shown") == 0

    # The same assembly must stay hidden when it starts DURING the reveal.
    # bootOverlay.done is false for the whole ~1.4s handover, so a revalidate
    # landing in that window read as a fresh build: veil up, landing blanked,
    # under an interface that is fading in. The payload waits instead, and is
    # applied on the other side as an ordinary in-place refresh.
    q("bootHandover.stop()")
    q("bootBlk.stop()")
    q("bootZoom.stop()")
    q("bootOverlay.done = false")
    q("browseSections = [{rowKind: 'albums', title: 'Already here', items: []}]")
    q("_browseParked = null")
    q("browseBuilding = false")
    q("bootVer.shown = 1")
    q("bootVer.text = 'v0.0.0'")  # give the drain a readout to walk
    q("bootHandover.start()")
    settle(200)  # mid-reveal: the version drain is walking its cells
    # The visible handover is now the drain (bootBlk) plus the zoom it starts
    # from its own last tick (bootZoom); bootHandover itself only lights the
    # drain, so "mid-reveal" is any of the three still running.
    mid_reveal = bool(q("bootHandover.running || bootBlk.running || bootZoom.running")) and not bool(
        q("bootOverlay.done")
    )
    q(
        "waves.browseLoaded({sections: [{rowKind: 'albums', title: 'Refreshed', items: []}], genres: [], moods: [], decades: []})"
    )
    settle(150)
    parked = (
        mid_reveal
        and q("_browseParked") is not None
        and not bool(q("browseBuilding"))
        and q("browseSections.length > 0 ? browseSections[0].title : ''") == "Already here"
    )

    settle(3000)  # the reveal completes and hands the parked payload through
    applied = (
        q("_browseParked") is None
        and q("browseSections.length > 0 ? browseSections[0].title : ''") == "Refreshed"
        and not bool(q("browseBuilding"))
    )

    # The gate must also hold while the landing DATA is still in flight:
    # signed in, no sections yet, no error. The original gate only covered the
    # shelf assembly, so a login or first fetch slower than the opening frame
    # revealed onto the bare "Reading the wire…" landing (reported from
    # livetesting).
    q("bootHandover.stop()")
    q("bootBlk.stop()")
    q("bootZoom.stop()")
    q("handoverCap.stop()")
    q("handoverCap.expired = false")
    q("bootOverlay.done = false")
    q("bootVer.shown = 1")
    q("bootContentShown = 0")
    q("browseSections = []")
    q("browseBuilding = false")
    q("browseError = false")
    bridge._set_logged_in(True)
    settle()

    q("bootOverlay.handover()")
    settle(300)
    data_held = (
        bool(q("bootOverlay.handoverHeld"))
        and not bool(q("bootOverlay.done"))
        and q("bootVer.shown") == 1  # still on the opening frame, version up
    )

    # The first payload arriving releases the hold and the reveal runs.
    q("browseSections = [{rowKind: 'albums', title: 'Landed', items: []}]")
    settle(3000)
    data_handed = bool(q("bootOverlay.done")) and q("bootContentShown") == 1

    print(
        f"held={held} handed={handed} drained={drained} parked={parked} applied={applied} "
        f"data_held={data_held} data_handed={data_handed}",
        flush=True,
    )
    ok = held and handed and drained and parked and applied and data_held and data_handed
    return _EXIT_OK if ok else _EXIT_REGRESSED


if __name__ == "__main__":
    raise SystemExit(_run_scenario())
