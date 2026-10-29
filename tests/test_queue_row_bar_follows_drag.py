"""A running queue row's bar follows the drawer edge WHILE it is dragged.

WHAT THIS FENCES OFF
--------------------
The row's DotMatrix used to take its column count from a settled width that
only caught up 300ms after the drawer edge was let go, so dragging the queue
wider or narrower left the bar at its old size for the whole gesture and then
snapped it to the new one on release. The hold existed because the cells were
an integer Repeater model, which resets on every change: following the live
width rebuilt every dot per column gained or lost. The cells are a ListModel
kept at size by appending or dropping at its end, so the bar can follow the
hand and a column more or less only builds or frees that column.

HOW THIS STAYS FIXED
--------------------
Boot the real Main.qml offscreen, seed a running row through the bridge, open
the drawer and grab its edge with REAL mouse events. Without ever releasing:

* drag wider, then narrower, and read the matrix 40ms after each move (well
  inside the old 300ms settle): its columns must be the ones its current width
  holds, and its laid-out grid must fit inside that width;
* a cell standing before the drag is tagged, and must still be the same
  object after both moves, which is what proves a column change is an edit
  at the end of the model and not a rebuild of every dot.

Runs in a SUBPROCESS like the other Main.qml scenarios (shares the sibling's
``_boot``).
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

from test_progress_matrix_stable_width import _EXIT_OK, _EXIT_REGRESSED, _boot

_FIND = """
 function walk(it, pred){
  if (!it) return null;
  if (pred(it)) return it;
  for (var i = 0; i < it.children.length; i++) {
   var hit = walk(it.children[i].item || it.children[i], pred);
   if (hit) return hit;
  }
  return null;
 }
 function mx(){ return walk(queueDrawer.contentItem, function(it){ return it.objectName === 'queueRowMatrix' }) }
"""


def test_the_queue_row_bar_follows_the_drawer_edge_mid_drag():
    import pytest

    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="waves-queue-bar-drag-test-")
    env["HOME"] = env["XDG_CONFIG_HOME"]
    proc = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), "--run-scenario"],
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    tail = "\n".join((proc.stdout + proc.stderr).strip().splitlines()[-12:])
    if proc.returncode == 77:
        pytest.skip("PySide6 / offscreen Qt unavailable")
    if proc.returncode == 78:
        pytest.skip(f"could not set up the scenario in this environment:\n{tail}")
    assert proc.returncode == _EXIT_OK, f"the queue row's bar stopped following the drag:\n{tail}"


def _scenario() -> int:
    booted = _boot()
    if not isinstance(booted, tuple):
        return booted
    root, q, settle, bridge = booted
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest

    q("root.width = 1200")
    q("root.height = 800")
    q("root.queueWidth = 420")
    qid = bridge._enqueue("Drag Row", "track", media_id="lab-drag", artist="Lab", tracks=0)
    bridge._set_queue_status(qid, "running")
    bridge._set_queue_progress(qid, 37)
    settle(120)
    q("queueDrawer.open()")
    settle(900)

    def read() -> tuple[float, int, int, float, bool]:
        got = q(
            "(function(){" + _FIND + " var m = mx(); if (!m) return 'none';"
            " var tagged = false;"
            " for (var i = 0; i < m.children.length; i++) if (m.children[i].objectName === 'dragSurvivor') tagged = true;"
            " return [m.width, m.cols, m.total, m.cols * (m.dot + m.gap) - m.gap, tagged ? 1 : 0].join(',') })()"
        )
        if not got or got == "none":
            raise LookupError("no running row matrix in the drawer")
        w, cols, total, grid, tagged = str(got).split(",")
        return float(w), int(cols), int(total), float(grid), tagged == "1"

    try:
        w0, cols0, _t, _g, _s = read()
    except LookupError as exc:
        print(exc, file=sys.stderr)
        return 78
    # Tag a cell near the start of the bar; it must survive every move below.
    q(
        "(function(){" + _FIND + " var m = mx();"
        " for (var i = 0; i < m.children.length; i++) if (m.children[i].rowTop === 0 && m.children[i].col === 2)"
        " { m.children[i].objectName = 'dragSurvivor'; return } })()"
    )

    grab_x = int(q("queueDrawer.x")) + 6
    QTest.mousePress(root, Qt.LeftButton, Qt.NoModifier, QPoint(grab_x, 300))
    settle(40)
    if not bool(q("gripMouse.pressed")):
        print("the handle never took the press", file=sys.stderr)
        return 78

    failures = []
    expect_cols = lambda w: int((w + 1) // 4)  # (dot 3 + gap 1)
    for label, dx in (("wider", -300), ("narrower", -120)):
        QTest.mouseMove(root, QPoint(grab_x + dx, 300))
        settle(40)
        w, cols, total, grid, survived = read()
        if cols != expect_cols(w):
            failures.append(f"dragged {label}: {cols} columns in a {w:.0f}px bar, wanted {expect_cols(w)}")
        if total != cols * 4:
            failures.append(f"dragged {label}: {total} cells for {cols} columns of 4")
        if grid > w + 0.5:
            failures.append(f"dragged {label}: the grid is {grid:.0f}px in a {w:.0f}px bar")
        if not survived:
            failures.append(f"dragged {label}: the cells were rebuilt instead of edited at the end")
    if read()[1] <= cols0:
        failures.append(f"the bar never gained columns: {cols0} at {w0:.0f}px before the drag")
    QTest.mouseRelease(root, Qt.LeftButton, Qt.NoModifier, QPoint(grab_x - 120, 300))
    settle(60)

    for f in failures:
        print(f, file=sys.stderr)
    return _EXIT_REGRESSED if failures else _EXIT_OK


if __name__ == "__main__" and "--run-scenario" in sys.argv:
    sys.exit(_scenario())
