"""A running queue row's progress bar is the dense grid, and its slot fits it.

WHAT THIS FENCES OFF
--------------------
The queue drawer's per-row bar was two rows of 4px cells with 4px gaps in a
12px slot while the download button's running face had moved to a dense grid
(five rows of 3px cells, 1px gaps). The queue progress lab (2026-08-17, six
densities side by side in the real drawer) picked FOUR rows of 3px cells with
1px gaps: the same family as the button, 15px tall, so the running row grows
three pixels for it. Two things can drift apart here: the matrix geometry and
the slot the row keeps for it (the slot clips, so a slot shorter than the
matrix silently cuts off its bottom row).

HOW THIS STAYS FIXED
--------------------
Static: the row's DotMatrix (objectName queueRowMatrix) declares rows 4, dot 3,
gap 1, and is the only site that shades its outer rows (edgeSoft). Live: the
real Main.qml is booted offscreen, a running row is seeded through the bridge,
and the row's matrix must be exactly as tall as its slot, span the row (its
width is the row's content width, not a fixed number), fade its ends over 28px
(rounds 2-4 of the lab: the download face's conveyor fade, a shade shorter
than a shelf's) and shade its top and bottom rows' cells from 15% at their
outer edge (a gradient on those cells only; the middle rows stay flat).

Runs in a SUBPROCESS like the other Main.qml scenarios (shares the sibling's
``_boot``).
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

from test_progress_matrix_stable_width import _EXIT_OK, _EXIT_REGRESSED, _boot

QML_MAIN = Path(__file__).resolve().parent.parent / "waves" / "waves_ui" / "qml" / "Main.qml"

_WALK = """
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


def test_queue_row_bar_is_the_dense_grid():
    src = QML_MAIN.read_text()
    m = re.search(r'objectName: "queueRowMatrix"\s*\n.*\n\s*rows: (\d+); dot: (\d+); gap: (\d+)', src)
    assert m, "the queue row's DotMatrix (queueRowMatrix) moved or lost its geometry line"
    assert (m.group(1), m.group(2), m.group(3)) == ("4", "3", "1"), m.groups()
    assert src.count("edgeSoft: 0.15") == 1, "exactly one site (the queue row) shades its outer rows"
    a = src.index("component DotMatrix:")
    dm = src[a : src.index("component ", a + 1)]
    assert "property real edgeSoft: -1" in dm, "DotMatrix.edgeSoft must default to off"


def test_running_queue_row_slot_fits_its_bar():
    code, tail = _run_here()
    assert code == _EXIT_OK, f"the queue row's bar and its slot disagree again:\n{tail}"


def test_a_queue_row_that_is_not_running_builds_no_bar():
    """Hiding the bar is not sparing it.

    The slot collapses to height 0 and opacity 0 when a row is not running, but
    an invisible subtree is still BUILT, so every QUEUED row used to pay for the
    full grid: 364 cells at the drawer's 420px floor and past a thousand with
    the drawer dragged wide. A flick through a long queue dropped frames on rows
    that show nothing. The download button's smaller matrix has sat behind a
    Loader for exactly this reason; this one did not."""
    code, tail = _run_here("--run-queue-row-idle-scenario")
    assert code == _EXIT_OK, f"a queue row is building its bar with nothing to show:\n{tail}"


def test_a_running_queue_row_bar_fills_evenly_between_reports():
    """The bar glides to each new reading, it does not sit and jump.

    A collection's row hears its progress twice a second (the track poll), and
    drawn as told the bar held still for half a second and then lit a column
    and a half of blocks at once, over and over, for the whole download. A rise
    is now filled linearly over a little more than one poll. What must still
    land at once: the first reading (a drawer opened mid-run), a fall (a
    retry), and a row turning done (its bar reads full before it fades)."""
    code, tail = _run_here("--run-queue-row-glide-scenario")
    assert code == _EXIT_OK, f"the queue row's bar is jumping between progress reports again:\n{tail}"


def _run_here(flag: str = "--run-queue-row-scenario") -> tuple[int, str]:
    """The sibling runner points at ITS file; run this file's scenario the same way."""
    import os
    import tempfile

    import pytest

    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="waves-queue-row-density-test-")
    env["HOME"] = env["XDG_CONFIG_HOME"]
    proc = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), flag],
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
    return proc.returncode, tail


def _scenario() -> int:
    booted = _boot()
    if not isinstance(booted, tuple):
        return booted
    _root, q, settle, bridge = booted
    qid = bridge._enqueue("Density Row", "track", media_id="lab-density", artist="Lab", tracks=0)
    # Through the real mutators: a poked field is invisible to the delta
    # protocol (nothing marks it), so the row would stay queued on screen.
    bridge._set_queue_status(qid, "running")
    bridge._set_queue_progress(qid, 37)
    settle(120)
    q("queueDrawer.open()")
    settle(900)  # past the slot's 300ms grow and the drawer's slide
    got = q(
        "(function(){" + _WALK + " var m = mx(); if (!m) return 'none';"
        " var top = null, mid = null, bot = null;"
        " for (var i = 0; i < m.children.length; i++) { var c = m.children[i]; if (c.rowTop === undefined) continue;"
        "  if (c.rowTop === 0 && !top) top = c; if (c.rowTop === 1 && !mid) mid = c; if (c.rowTop === m.rows - 1 && !bot) bot = c; }"
        " return [m.rows, m.dot, m.gap, m.implicitHeight, m.parent.height, m.width, m.parent.width, m.litCount, m.total,"
        "  m.edgeFadeW, m.edgeFadeH, m.edgeSoft, top && top.gradient ? 1 : 0, mid && mid.gradient ? 1 : 0, bot && bot.gradient ? 1 : 0].join(',') })()"
    )
    if not got or got == "none":
        print("no running row matrix found in the drawer", file=sys.stderr)
        return 78
    rows, dot, gap, ih, slot, w, pw, lit, total, fw, fh, soft, tg, mg, bg = (float(v) for v in str(got).split(","))
    failures = []
    if (rows, dot, gap) != (4, 3, 1):
        failures.append(f"geometry {rows},{dot},{gap}, wanted 4,3,1")
    if abs(ih - 15) > 0.01:
        failures.append(f"matrix implicitHeight {ih}, wanted 15")
    if abs(slot - ih) > 0.5:
        failures.append(f"the row's slot is {slot}px for a {ih}px matrix (it clips)")
    if w < 200 or abs(w - pw) > 0.5:
        failures.append(f"matrix width {w} does not span the row's {pw}")
    if not (0 < lit < total) or abs(lit / total - 0.37) > 0.02:
        failures.append(f"lit {lit} of {total} is not 37%")
    if (fw, fh) != (28, 0):
        failures.append(f"edge fade {fw}/{fh}, wanted 28 at the ends and none top/bottom")
    if abs(soft - 0.15) > 1e-6:
        failures.append(f"edgeSoft {soft}, wanted 0.15")
    if (tg, mg, bg) != (1, 0, 1):
        failures.append(f"outer-row shading gradients top/mid/bottom = {tg}/{mg}/{bg}, wanted 1/0/1")
    for f in failures:
        print(f, file=sys.stderr)
    return _EXIT_REGRESSED if failures else _EXIT_OK


_COUNT = """
 function count(it, pred){
  if (!it) return 0;
  var n = pred(it) ? 1 : 0;
  for (var i = 0; i < it.children.length; i++) n += count(it.children[i].item || it.children[i], pred);
  return n;
 }
 function mats(c){ return count(c, function(it){ return it.objectName === 'queueRowMatrix' }) }
 function cells(c){ return count(c, function(it){ return it.rowTop !== undefined }) }
"""


def _idle_scenario() -> int:
    """40 queued rows must build no bar at all; the first running one must."""
    booted = _boot()
    if not isinstance(booted, tuple):
        return booted
    _root, q, settle, bridge = booted

    for i in range(40):
        bridge._enqueue(f"Queued Album {i}", "album", media_id=f"idle-{i}", artist="Lab", tracks=12)
    bridge._emit_queue()
    settle(200)
    q("queueDrawer.open()")
    settle(900)

    got = q("(function(){" + _COUNT + " var c = queueDrawer.contentItem; return [mats(c), cells(c)].join(',') })()")
    if not got:
        print("could not walk the queue drawer", file=sys.stderr)
        return 78
    idle_mats, idle_cells = (int(v) for v in str(got).split(","))

    run_qid = bridge._enqueue("Running", "track", media_id="idle-run", artist="Lab", tracks=0)
    bridge._set_queue_status(run_qid, "running")
    bridge._set_queue_progress(run_qid, 50)
    settle(900)
    run_mats = int(q("(function(){" + _COUNT + " return mats(queueDrawer.contentItem) })()") or 0)

    failures = []
    if idle_mats or idle_cells:
        failures.append(f"{idle_mats} bars ({idle_cells} cells) built for 40 rows that show none")
    if run_mats < 1:
        failures.append("a running row built no bar, so the gate is stuck shut")
    for f in failures:
        print(f, file=sys.stderr)
    return _EXIT_REGRESSED if failures else _EXIT_OK


def _glide_scenario() -> int:
    booted = _boot()
    if not isinstance(booted, tuple):
        return booted
    _root, q, settle, bridge = booted
    qid = bridge._enqueue("Glide Row", "album", media_id="lab-glide", artist="Lab", tracks=10)
    bridge._set_queue_status(qid, "running")
    bridge._set_queue_progress(qid, 30)
    settle(120)
    q("queueDrawer.open()")
    settle(900)

    def shown() -> float:
        got = q("(function(){" + _WALK + " var m = mx(); return m ? m.pct : -1 })()")
        return float(got)

    failures = []
    if abs(shown() - 30) > 0.01:
        failures.append(f"the first reading did not land at once: {shown()}, wanted 30")
    bridge._set_queue_progress(qid, 34)
    settle(250)
    mid = shown()
    if not 30.2 < mid < 33.8:
        failures.append(f"250ms after a 30 -> 34 report the bar shows {mid}: it snapped or never moved")
    settle(900)
    if abs(shown() - 34) > 0.01:
        failures.append(f"the bar never arrived at 34: {shown()}")
    bridge._set_queue_progress(qid, 10)
    settle(60)
    if abs(shown() - 10) > 0.01:
        failures.append(f"a fall to 10 did not snap: {shown()}")
    # The real finish: the reading reaches 100 while the row still runs (the
    # final steps), and only then does the row turn done.
    bridge._set_queue_progress(qid, 100)
    settle(100)
    if not 10 < shown() < 99:
        failures.append(f"100ms after a 10 -> 100 report the bar shows {shown()}")
    bridge._set_queue_status(qid, "done")
    settle(60)
    if abs(shown() - 100) > 0.01:
        failures.append(f"a row that turned done mid-fill shows {shown()}, not a full bar")
    for f in failures:
        print(f, file=sys.stderr)
    return _EXIT_REGRESSED if failures else _EXIT_OK


if __name__ == "__main__" and "--run-queue-row-scenario" in sys.argv:
    sys.exit(_scenario())

if __name__ == "__main__" and "--run-queue-row-idle-scenario" in sys.argv:
    sys.exit(_idle_scenario())

if __name__ == "__main__" and "--run-queue-row-glide-scenario" in sys.argv:
    sys.exit(_glide_scenario())
