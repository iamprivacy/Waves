"""An expanded queue row reaches every track, however long the collection.

WHAT THIS FENCES OFF
--------------------
The per-track ledger was written for albums, where a row is 10 to 30 tracks,
and built every row at once. Opened to playlists and mixes it was capped at
500 rows to keep the GUI thread alive, and the cap hid the rest: a 610-track
playlist listed 500 tracks and then "110 more tracks", a line that did
nothing but fold the card when clicked. The tracks that failed or that TIDAL
refused past track 500 could not be seen anywhere (issue #54).

The ledger is now a scrolling list that builds only the rows in view, with a
MISSING view of just the tracks that did not land. Proved on the real
Main.qml (subprocess, like its siblings):

- a row nobody opened builds no ledger rows at all;
- a huge playlist builds a bounded handful of rows, yet its LAST track is
  reachable;
- the wheel scrolls the ledger, and at its end carries on with the drawer
  instead of trapping the pointer inside one row;
- a click on the scrolling list leaves the card open;
- MISSING lists only the failed and unavailable tracks, under their own
  numbers, and the bar offering it is absent when nothing is missing;
- the open list stops at a fixed box of rows (a setting, followed live), with
  the pages' scroll fades
  only on a side that has rows past it, and folds by closing with the card;
- an open list that scrolls shows a hairline scrollbar beside its rows,
  which keep their full width;
- an ordinary album shows every track and does not scroll.
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

# Well past the old ceiling of 500, and not a round number, so an off-by-one
# at either end shows up.
BIG = 1207
SMALL = 12
# The open box: 12 rows of 15px with 3px between them; the peek is 3 pitches.
BOX = 12 * 18 - 3
PEEK_BOX = 3 * 18
# Two tracks that did not land, one of them past where the old cap stopped.
MISSING = {5: "unavailable", 1100: "failed"}


def test_every_track_of_a_huge_playlist_is_reachable():
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    sandbox = tempfile.mkdtemp(prefix="waves-queue-ledger-scroll-")
    env["XDG_CONFIG_HOME"] = sandbox
    env["HOME"] = sandbox
    proc = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), "--run-scenario"],
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    tail = "\n".join((proc.stdout + proc.stderr).strip().splitlines()[-14:])
    if proc.returncode == _EXIT_NO_QT:
        pytest.skip("PySide6 / offscreen Qt unavailable")
    if proc.returncode == _EXIT_PRECONDITION:
        pytest.skip(f"could not set up the scenario in this environment:\n{tail}")
    assert proc.returncode == _EXIT_OK, f"the scrolling ledger regressed:\n{tail}"


def _run_scenario() -> int:  # (one straight line of scene setup)
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    try:
        from PySide6.QtCore import QEvent, QEventLoop, QPoint, QPointF, Qt, QTimer, QUrl
        from PySide6.QtGui import QGuiApplication, QMouseEvent, QWheelEvent
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

    root.resize(1280, 900)
    settle(120)
    q(PARK_LOGIN_QML)
    q("queueDrawer.open()")
    settle(400)
    if not bool(q("queueDrawer.visible")):
        print("the queue drawer would not open", file=sys.stderr)
        return _EXIT_PRECONDITION

    # Finds a named object inside the first drawer row (the row under test).
    finder = """(function (name) {
            var hit = null;
            function walk(o) {
                if (!o || hit) return;
                if (o.objectName === name) { hit = o; return; }
                var kids = o.children;
                for (var i = 0; i < (kids ? kids.length : 0); ++i) walk(kids[i]);
            }
            walk(queueList.itemAtIndex(0));
            return hit;
        })"""

    def find(name: str) -> str:
        return f"{finder}('{name}')"

    def built_titles() -> list[str]:
        """The ledger rows that actually EXIST (the cost), by title."""
        out = q(f"""(function () {{
                var out = [];
                function walk(o) {{
                    if (!o) return;
                    if (o.objectName === 'qTrackTitle') out.push('' + o.text);
                    var kids = o.children;
                    for (var i = 0; i < (kids ? kids.length : 0); ++i) walk(kids[i]);
                }}
                walk({find('qLedgerList')});
                return out.join('|');
            }})()""")
        return [t for t in str(out or "").split("|") if t]

    def scene_point(expr: str, fx: float = 0.5, fy: float = 0.5) -> QPointF:
        x = float(q(f"(function(o){{ return o.mapToItem(null, o.width * {fx}, o.height * {fy}).x }})({expr})"))
        y = float(q(f"(function(o){{ return o.mapToItem(null, o.width * {fx}, o.height * {fy}).y }})({expr})"))
        return QPointF(x, y)

    def wheel(at: QPointF, notches: int) -> None:
        ev = QWheelEvent(
            at,
            QPointF(root.mapToGlobal(at.toPoint())),
            QPoint(0, 0),
            QPoint(0, -120 * notches),
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
            Qt.ScrollPhase.NoScrollPhase,
            False,
        )
        QGuiApplication.sendEvent(root, ev)
        settle(60)

    def click(at: QPointF) -> None:
        for kind in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonRelease):
            ev = QMouseEvent(
                kind,
                at,
                QPointF(root.mapToGlobal(at.toPoint())),
                Qt.MouseButton.LeftButton,
                Qt.MouseButton.LeftButton if kind == QEvent.Type.MouseButtonPress else Qt.MouseButton.NoButton,
                Qt.KeyboardModifier.NoModifier,
            )
            QGuiApplication.sendEvent(root, ev)
            settle(30)
        settle(200)

    def seed(count: int, kind: str, name: str, missing=None) -> int:
        qid = bridge._enqueue(name, kind, media_id=f"c-{name}", collection=True, tracks=count)
        bridge.queueChanged.emit(list(bridge._queue))
        settle(150)
        if missing:
            bridge._job_tracks[qid] = {
                str(n - 1): {"id": str(n - 1), "status": st, "pct": 100.0} for n, st in missing.items()
            }
        bridge._merge_queue_tracks(
            qid,
            [{"id": str(i), "num": i + 1, "title": f"track {i + 1}", "duration": "3:00"} for i in range(count)],
        )
        settle(150)
        return qid

    bad: list[str] = []

    # The huge playlist first in the drawer, with ordinary albums below it so
    # the drawer itself has somewhere to scroll.
    qid = seed(BIG, "playlist", "Enormous Mixtape", MISSING)
    for i in range(16):
        seed(SMALL, "album", f"Filler {i}")
    if not bool(q("queueList.itemAtIndex(0) !== null")):
        print("no drawer row", file=sys.stderr)
        return _EXIT_PRECONDITION
    if built_titles():
        bad.append(f"a collapsed {BIG}-track row already built {len(built_titles())} ledger rows, want 0")

    q(f"root.queueExpanded = ({{ {qid}: true }})")
    settle(600)
    if not bool(q(f"{find('qLedgerList')} !== null")):
        print("the expanded row has no scrolling ledger list", file=sys.stderr)
        return _EXIT_REGRESSED
    count = int(q(f"{find('qLedgerList')}.count"))
    if count != BIG:
        bad.append(f"the ledger lists {count} tracks, want all {BIG}")
    built = built_titles()
    if not 0 < len(built) <= 120:
        bad.append(f"an expanded {BIG}-track row built {len(built)} ledger rows, want a bounded handful")
    if not bool(q(f"{find('qLedgerList')}.interactive")):
        bad.append("a ledger longer than its cap does not scroll")
    # Its scrollbar is a hairline beside the rows, never over them: the rows
    # keep the list's full width and the bar sits past its right edge.
    led, bar = find("qLedgerList"), find("qLedgerScroll")
    if not bool(q(f"{bar} !== null && {bar}.visible")):
        bad.append("an open ledger that scrolls shows no scrollbar")
    else:
        row_w = float(q(f"{led}.itemAtIndex(0).width"))
        if abs(row_w - float(q(f"{led}.width"))) > 0.5:
            bad.append(f"ledger rows are {row_w}px wide, want the list's full width")
        bar_x = float(q(f"{bar}.mapToItem({led}, 0, 0).x"))
        if bar_x < float(q(f"{led}.width")):
            bad.append(f"the ledger scrollbar sits over the rows (x {bar_x})")
    # It stops at a fixed box of rows and scrolls inside it.
    box = float(q(f"{find('qLedgerList')}.height"))
    if abs(box - BOX) > 0.5:
        bad.append(f"the open ledger is {box}px tall, want its {BOX}px box")
    # The box's size is a setting (Advanced), and an open row follows it live.
    bridge.setWavesPref("queue_ledger_rows", 6)
    settle(300)
    box = float(q(f"{find('qLedgerList')}.height"))
    if abs(box - (6 * 18 - 3)) > 0.5:
        bad.append(f"set to 6 rows, the open ledger is {box}px tall, want {6 * 18 - 3}px")
    bridge.setWavesPref("queue_ledger_rows", 12)
    settle(300)

    def fades() -> tuple[bool, bool]:
        return (
            bool(q(f"{find('qLedgerFadeTop')}.visible")),
            bool(q(f"{find('qLedgerFadeBottom')}.visible")),
        )

    if fades() != (False, True):
        bad.append(f"a ledger at its top shows fades (top, bottom) {fades()}, want only the bottom")

    # The wheel scrolls the ledger, not the drawer, while the ledger has room.
    q("queueList.contentY = queueList.originY")
    settle(100)
    drawer_y0 = float(q("queueList.contentY"))
    wheel(scene_point(find("qLedgerList")), 3)
    ledger_y = float(q(f"{find('qLedgerList')}.contentY"))
    if ledger_y <= 0:
        bad.append("the wheel over the ledger did not scroll it")
    if float(q("queueList.contentY")) != drawer_y0:
        bad.append("the wheel scrolled the drawer while the ledger still had room")

    # A trackpad swipe over the ledger whose momentum arrives after the
    # swipe's end (see test_momentum_guard): the momentum is the ledger's,
    # never the drawer's.
    q(f"{find('qLedgerList')}.contentY = {find('qLedgerList')}.originY")
    settle(100)
    at = scene_point(find("qLedgerList"))

    def phase(kind, d: int) -> None:
        ev = QWheelEvent(
            at,
            QPointF(root.mapToGlobal(at.toPoint())),
            QPoint(0, d),
            QPoint(0, d * 2),
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
            kind,
            False,
            Qt.MouseEventSource.MouseEventSynthesizedBySystem,
        )
        QGuiApplication.sendEvent(root, ev)
        settle(16)

    phase(Qt.ScrollPhase.ScrollBegin, 0)
    for _ in range(6):
        phase(Qt.ScrollPhase.ScrollUpdate, -20)
    phase(Qt.ScrollPhase.ScrollEnd, 0)
    swiped = float(q(f"{find('qLedgerList')}.contentY"))
    for k in range(20):
        phase(Qt.ScrollPhase.ScrollMomentum, round(-20 * (1 - k / 20)))
    phase(Qt.ScrollPhase.ScrollEnd, 0)
    settle(200)
    coasted = float(q(f"{find('qLedgerList')}.contentY"))
    if not swiped > 0 or not coasted > swiped:
        bad.append(f"a late momentum over the ledger did not carry it on ({swiped} -> {coasted})")
    if float(q("queueList.contentY")) != drawer_y0:
        bad.append("a late momentum over the ledger moved the drawer instead")

    # The last track is reachable.
    q(f"{find('qLedgerList')}.positionViewAtEnd()")
    settle(300)
    built = built_titles()
    if f"track {BIG}" not in built:
        bad.append(f"after scrolling to the end, track {BIG} is not built (last built: {built[-3:]})")
    if fades() != (True, False):
        bad.append(f"a ledger at its end shows fades (top, bottom) {fades()}, want only the top")

    # At the ledger's end the wheel carries on with the drawer.
    wheel(scene_point(find("qLedgerList")), 2)
    if float(q("queueList.contentY")) <= drawer_y0:
        bad.append("at the ledger's end the wheel did not carry on with the drawer")
    q("queueList.contentY = queueList.originY")
    settle(150)

    # A click on the scrolling list leaves the card open.
    click(scene_point(find("qLedgerList")))
    if not bool(q(f"root.queueExpanded[{qid}] === true")):
        bad.append("a click on the scrolling ledger folded the card")

    # A track the list has not seen yet arriving live (the list grows) keeps
    # the reader where they were scrolled to: no jump back to the top.
    q(f"{find('qLedgerList')}.contentY = 2000")
    settle(150)
    bridge.queueTrackState.emit(qid, {"id": "late", "title": "late arrival", "status": "running", "pct": 0.0})
    settle(250)
    if int(q(f"{find('qLedgerList')}.count")) != BIG + 1:
        bad.append("a track arriving live did not join the ledger")
    y = float(q(f"{find('qLedgerList')}.contentY"))
    if abs(y - 2000) > 1:
        bad.append(f"a live track arriving threw the scrolled ledger to contentY {y}, want it kept at 2000")

    # MISSING: the bar says how many, and the view lists just those, under
    # their own numbers.
    if not bool(q(f"{find('qLedgerBar')}.visible")):
        bad.append("a ledger with missing tracks shows no MISSING bar")
    label = str(q(f"{find('qLedgerMissing')}.label"))
    if label != f"MISSING · {len(MISSING)}":
        bad.append(f"the MISSING button reads {label!r}")
    q(f"{find('qLedgerMissing')}.clicked()")
    settle(400)
    count = int(q(f"{find('qLedgerList')}.count"))
    built = built_titles()
    want = [f"track {n}" for n in sorted(MISSING)]
    if count != len(MISSING) or built != want:
        bad.append(f"MISSING lists {count} rows {built}, want {want}")
    # Every missing track retried and landed: the row goes back to ALL by
    # itself, so a later failure never drops it into MISSING unasked.
    for n in MISSING:
        bridge.queueTrackState.emit(qid, {"id": str(n - 1), "status": "done", "pct": 100.0})
    settle(300)
    if bool(q(f"root.queueLedgerMissing[{qid}] === true")):
        bad.append("MISSING stayed on after every missing track landed")
    if int(q(f"{find('qLedgerList')}.count")) != BIG + 1:
        bad.append("with nothing missing any more, the ledger did not list every track again")

    # Folding from MISSING: while the card closes it keeps showing what it
    # showed (the bar and the missing rows), so nothing swaps under the motion.
    q(f"root.queueExpanded = ({{ {qid}: true }})")
    settle(500)
    bridge.queueTrackState.emit(qid, {"id": "1099", "status": "failed", "pct": 100.0})
    settle(200)
    q(f"{find('qLedgerMissing')}.clicked()")
    settle(400)
    q("root.queueExpanded = ({})")
    settle(60)
    if not bool(q(f"{finder}('qLedgerBar') !== null && {find('qLedgerBar')}.visible")):
        bad.append("folding from MISSING dropped the bar mid-close")
    if built_titles() != ["track 1100"]:
        bad.append(f"folding from MISSING swapped the closing rows to {built_titles()[:4]}")
    settle(500)

    # Folding a scrolled ledger glides it back to its first track in the
    # same motion: no jump mid-close, and the top once it has closed.
    q(f"root.queueExpanded = ({{ {qid}: true }})")
    settle(500)
    q(f"{find('qLedgerAll')}.clicked()")
    settle(300)
    q(f"{find('qLedgerList')}.contentY = {find('qLedgerList')}.originY + 3000")
    settle(150)
    q("root.queueExpanded = ({})")
    settle(40)
    # Which track sits at the top of the list (pixel offsets are not
    # comparable mid-scroll: a ListView moves its origin as it goes).
    top_row = f"(function(L){{ var i = L.indexAt(10, L.contentY + 5); return i >= 0 ? i : L.indexAt(10, L.contentY + 10) }})({find('qLedgerList')})"
    mid = int(q(top_row))
    # The box closes WITH the card, its rows kept, not snapped to the peek.
    folding = float(q(f"{find('qLedgerList')}.height"))
    if folding <= PEEK_BOX + 0.5:
        bad.append(f"a folding ledger dropped to {folding}px at once, want it closing with the card")
    if not 0 < mid < 160:
        bad.append(f"a folding ledger showed row {mid} 40ms in, want it gliding up from about 166 to 0")
    settle(400)
    q(f"root.queueExpanded = ({{ {qid}: true }})")
    settle(500)
    top = int(q(top_row))
    if top != 0:
        bad.append(f"a folded and reopened ledger starts at row {top}, not at its first track")
    q("root.queueExpanded = ({})")
    settle(400)

    # An ordinary album is untouched: every track, no scroll, no bar.
    q("root.queueExpanded = ({})")
    settle(200)
    bridge._queue.clear()
    bridge._queue_index.clear()
    bridge.queueChanged.emit([])
    settle(150)
    qid = seed(SMALL, "album", "Ordinary Record")
    q(f"root.queueExpanded = ({{ {qid}: true }})")
    settle(600)
    built = built_titles()
    if len(built) != SMALL:
        bad.append(f"an expanded {SMALL}-track album built {len(built)} ledger rows, want all {SMALL}")
    if bool(q(f"{find('qLedgerList')}.interactive")):
        bad.append("an album that fits still scrolls")
    if fades() != (False, False):
        bad.append(f"an album that fits shows fades (top, bottom) {fades()}")
    if bool(q(f"{find('qLedgerBar')}.visible")):
        bad.append("an album with nothing missing shows the MISSING bar")

    if bad:
        for b in bad:
            print(b, file=sys.stderr)
        return _EXIT_REGRESSED
    return _EXIT_OK


if __name__ == "__main__":
    if "--run-scenario" in sys.argv:
        raise SystemExit(_run_scenario())
