"""An IN LIBRARY ledger row keeps its quality and speaks in the button's voices.

WHAT THIS FENCES OFF
--------------------
The queue design lab (scratchpad/queue_design_lab) showed a skipped track with
the tier of the copy you already hold beside a coloured IN LIBRARY word, and
its notes said the bridge did not put that on the wire yet: _emit_skip sent
"status": "skipped", full stop. Shipped that way, the ledger drew IN LIBRARY
with a blank tier cell.

Now the skip event carries the owned copy's tier (from the ownership ledger's
record, or the library scan's local class) plus HOW it was found, and the row:

* keeps the tier in its column at full strength (it is what the file IS);
* colours IN LIBRARY green when Waves wrote that exact file (a fact) and gold
  when the library scan matched it by tags (a guess), the download button's
  own two voices.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path
from threading import Lock

import pytest

QML_MAIN = Path(__file__).resolve().parent.parent / "tidaler" / "waves_ui" / "qml" / "Main.qml"

_EXIT_OK = 0
_EXIT_REGRESSED = 1
_EXIT_NO_QT = 77
_EXIT_PRECONDITION = 78


class _Signal:
    def __init__(self):
        self.calls = []

    def emit(self, *a):
        self.calls.append(a)


class _Stub:
    def __init__(self):
        from tidaler.waves_ui import backend

        self._job_tracks = {}
        # The ledger merge also overlays an expansion's predicted skips
        # (test_queue_owned_prediction.py); empty here, so every row in
        # this scenario is the live registry's answer alone.
        self._job_owned = {}
        self._job_fetched = {}
        self._job_signals = {}
        self._queue = []
        self._queue_index = {}
        self._outcome_lock = Lock()
        self.queueTrackState = _Signal()
        self.queueTracksLoaded = _Signal()
        self._emit_queue = lambda: None
        self._own_pool = type("P", (), {"start": lambda self_, w: None})()
        self.settings = type("S", (), {"data": type("D", (), {"download_base_path": ""})()})()
        for name in ("_track_lifecycle", "_queue_item", "_merge_queue_tracks"):
            setattr(self, name, getattr(backend.WavesBridge, name).__get__(self, _Stub))


def test_the_registry_keeps_the_owned_copy_and_how_it_was_found():
    b = _Stub()
    # Seeded as pending by an earlier merge, then the skip lands on it.
    b._track_lifecycle(1, {"id": "9", "title": "t", "status": "pending"})
    b._track_lifecycle(1, {"id": "9", "status": "skipped", "quality": "HI-RES", "owned": "own"})
    row = b._job_tracks[1]["9"]
    assert (row["status"], row["quality"], row["owned"], row["pct"]) == ("skipped", "HI-RES", "own", 100.0)
    # A first-sight skip (no earlier row) keeps them too, and the merge
    # carries both onto the fetched order.
    b._track_lifecycle(1, {"id": "8", "title": "u", "status": "skipped", "owned": "claim"})
    b._merge_queue_tracks(1, [{"id": "8", "num": 1, "title": "u", "duration": "3:00"}])
    rows = b.queueTracksLoaded.calls[-1][1]
    assert (rows[0]["owned"], rows[0]["quality"]) == ("claim", "")


def test_drawer_row_keeps_the_tier_and_colours_the_word():
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    sandbox = tempfile.mkdtemp(prefix="waves-queue-inlib-")
    env["XDG_CONFIG_HOME"] = sandbox
    env["HOME"] = sandbox
    proc = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), "--run-scenario"],
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    tail = "\n".join((proc.stdout + proc.stderr).strip().splitlines()[-12:])
    if proc.returncode == _EXIT_NO_QT:
        pytest.skip("PySide6 / offscreen Qt unavailable")
    if proc.returncode == _EXIT_PRECONDITION:
        pytest.skip(f"could not set up the scenario in this environment:\n{tail}")
    assert proc.returncode == _EXIT_OK, f"the IN LIBRARY ledger row regressed:\n{tail}"


def _run_scenario() -> int:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    try:
        from PySide6.QtCore import QEventLoop, QTimer, QUrl
        from PySide6.QtGui import QGuiApplication
        from PySide6.QtQml import QQmlApplicationEngine, QQmlEngine, QQmlExpression
    except Exception as exc:  # pragma: no cover - environment guard
        print(f"Qt unavailable: {exc}", file=sys.stderr)
        return _EXIT_NO_QT

    from _qml_offline import PARK_LOGIN_QML, patch_offline

    patch_offline()
    app = QGuiApplication.instance() or QGuiApplication([])
    try:
        from tidaler.waves_ui.app import _load_mono
        from tidaler.waves_ui.backend import WavesBridge
    except Exception as exc:  # pragma: no cover - environment guard
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
        e = QQmlExpression(QQmlEngine.contextForObject(root), root, expr)
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
    q("queueDrawer.open()")
    settle(120)
    if not bool(q("queueDrawer.visible")):
        print("the queue drawer would not open", file=sys.stderr)
        return _EXIT_PRECONDITION

    bridge._target_tier = lambda: "HI-RES"
    qid = bridge._enqueue("Album A", "album", media_id="m1", collection=True, tracks=3)
    bridge.queueChanged.emit(list(bridge._queue))
    settle(120)
    if not bool(q("queueList.itemAtIndex(0) !== null")):
        print("no drawer row", file=sys.stderr)
        return _EXIT_PRECONDITION
    bridge._merge_queue_tracks(
        qid,
        [
            {"id": "1", "num": 1, "title": "own", "duration": "3:00"},
            {"id": "2", "num": 2, "title": "claim", "duration": "3:00"},
            {"id": "3", "num": 3, "title": "todo", "duration": "3:00"},
        ],
    )
    q(f"root.queueExpanded = ({{ {qid}: true }})")
    settle(400)
    # Track 1: Waves wrote it, at LOSSLESS. Track 2: the scan matched it by
    # tags, a hi-res copy. Track 3 stays queued.
    bridge._track_lifecycle(qid, {"id": "1", "status": "skipped", "quality": "LOSSLESS", "owned": "own"})
    bridge._track_lifecycle(qid, {"id": "2", "status": "skipped", "quality": "HI-RES", "owned": "claim"})
    settle(400)

    got = str(q("""(function () {
            var out = [];
            function walk(o) {
                if (!o) return;
                if (o.objectName === 'qTrackTier')
                    out.push(('' + o.text) + '@' + (o.opacity < 0.9 ? 'faded' : 'full'));
                if (o.objectName === 'qTrackWord' && o.target === 'IN LIBRARY')
                    out.push('IN LIBRARY:' + (Qt.colorEqual(o.color, root.accent) ? 'green'
                                             : Qt.colorEqual(o.color, root.libAccent) ? 'gold' : '' + o.color));
                var kids = o.children;
                for (var i = 0; i < (kids ? kids.length : 0); ++i) walk(kids[i]);
            }
            walk(queueList.itemAtIndex(0));
            return out.join(' | ');
        })()"""))
    want = "LOSSLESS@full | IN LIBRARY:green | HI-RES@full | IN LIBRARY:gold | HI-RES@faded"
    if got != want:
        print(f"ledger read {got!r}, want {want!r}", file=sys.stderr)
        return _EXIT_REGRESSED
    return _EXIT_OK


if __name__ == "__main__" and "--run-scenario" in sys.argv:
    sys.exit(_run_scenario())
