"""An expanded queue row builds a bounded number of ledger rows.

WHAT THIS FENCES OFF
--------------------
The per-track ledger was written for albums, where a row is 10 to 30 tracks,
and it builds every row at once (the Repeater is not virtualised, and a ledger
row is four texts, two DecryptText cells and three running Behaviors). Opening
the ledger to playlists and mixes pointed that at collections which are
routinely hundreds of tracks and can be thousands, so an unbounded count spent
a quarter of a second of the GUI thread building rows nobody could see, and
then paid for all of them again on every progress tick.

Worse, the hover PEEK reached the same code: the 30px sliver clips the height
but not the work, so merely dragging the pointer across a large playlist row
built the whole ledger.

Both are now ceilings on how many delegates exist (root.queueLedgerMax and
root.queueLedgerPeek). Nothing else changed: the whole list is still in
root.queueTracks and every row's live state still arrives, so a smaller
collection is untouched and shows every track exactly as before. An expansion
that cannot show everything says how many rows it is not showing.

The expanded ceiling is proved on the real Main.qml (subprocess, like its
siblings), in both directions: a small collection shows every track and no
trailing line, a large one stops at the ceiling and says what is left. The peek
ceiling cannot be driven headlessly (it hangs off a real hover), so what is
pinned here is that it exists and is small, and that a row which is neither
expanded nor hovered builds nothing at all.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

QML_MAIN = Path(__file__).resolve().parent.parent / "waves" / "waves_ui" / "qml" / "Main.qml"

_EXIT_OK = 0
_EXIT_REGRESSED = 1
_EXIT_NO_QT = 77
_EXIT_PRECONDITION = 78

# Comfortably past the ceiling, and not a multiple of it, so an off-by-one in
# either the cap or the remainder shows up in the count.
BIG = 1207
SMALL = 12


def test_a_huge_playlist_ledger_stops_at_the_ceiling():
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    sandbox = tempfile.mkdtemp(prefix="waves-queue-ledger-cap-")
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
    assert proc.returncode == _EXIT_OK, f"the ledger did not bound itself:\n{tail}"


def _run_scenario() -> int:  # (one straight line of scene setup)
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
        from waves.waves_ui.app import _load_mono
        from waves.waves_ui.backend import WavesBridge
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

    cap = int(q("root.queueLedgerMax"))
    peek = int(q("root.queueLedgerPeek"))
    bad: list[str] = []

    if not 0 < peek <= 5:
        bad.append(f"the hover peek ceiling is {peek}; it must stay small, the sliver shows about one row")
    if not 0 < cap <= 2000:
        bad.append(f"the expanded ceiling is {cap}; an unbounded ledger is what this test exists to stop")

    def rows_built() -> int:
        """How many ledger delegates actually EXIST, which is the cost."""
        return int(q("""(function () {
                var n = 0;
                function walk(o) {
                    if (!o) return;
                    if (o.objectName === 'qTrackTitle') n += 1;
                    var kids = o.children;
                    for (var i = 0; i < (kids ? kids.length : 0); ++i) walk(kids[i]);
                }
                walk(queueList.itemAtIndex(0));
                return n;
            })()"""))

    def more_line() -> str:
        return str(q("""(function () {
                var out = '';
                function walk(o) {
                    if (!o) return;
                    if (o.objectName === 'qTrackMore' && o.visible) out = '' + o.text;
                    var kids = o.children;
                    for (var i = 0; i < (kids ? kids.length : 0); ++i) walk(kids[i]);
                }
                walk(queueList.itemAtIndex(0));
                return out;
            })()"""))

    def seed(count: int, kind: str, name: str) -> int:
        qid = bridge._enqueue(name, kind, media_id=f"c-{count}", collection=True, tracks=count)
        bridge.queueChanged.emit(list(bridge._queue))
        settle(150)
        bridge._merge_queue_tracks(
            qid,
            [{"id": str(i), "num": i + 1, "title": f"track {i + 1}", "duration": "3:00"} for i in range(count)],
        )
        settle(150)
        return qid

    # A collection far past the ceiling. Collapsed and unhovered first: the
    # cost of a row nobody opened must be zero, whatever its size.
    qid = seed(BIG, "playlist", "Enormous Mixtape")
    if not bool(q("queueList.itemAtIndex(0) !== null")):
        print("no drawer row", file=sys.stderr)
        return _EXIT_PRECONDITION
    built = rows_built()
    if built != 0:
        bad.append(f"a collapsed {BIG}-track row already built {built} ledger rows, want 0")

    q(f"root.queueExpanded = ({{ {qid}: true }})")
    settle(500)
    built = rows_built()
    if built != cap:
        bad.append(f"an expanded {BIG}-track row built {built} ledger rows, want the ceiling of {cap}")
    said = more_line()
    if said != f"{BIG - cap} more tracks":
        bad.append(f"the capped ledger said {said!r}, want {f'{BIG - cap} more tracks'!r}")

    # An ordinary album is untouched: every track, and nothing trailing.
    q("root.queueExpanded = ({})")
    settle(200)
    bridge._queue.clear()
    bridge._queue_index.clear()
    qid = seed(SMALL, "album", "Ordinary Record")
    q(f"root.queueExpanded = ({{ {qid}: true }})")
    settle(500)
    built = rows_built()
    if built != SMALL:
        bad.append(f"an expanded {SMALL}-track album built {built} ledger rows, want all {SMALL}")
    said = more_line()
    if said != "":
        bad.append(f"an album that fits still said {said!r}, want no trailing line")

    if bad:
        for b in bad:
            print(b, file=sys.stderr)
        return _EXIT_REGRESSED
    return _EXIT_OK


if __name__ == "__main__":
    if "--run-scenario" in sys.argv:
        raise SystemExit(_run_scenario())
