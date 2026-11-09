"""The QUEUE button's speed chart keeps the speed of every percent it passed.

WHAT THIS FENCES OFF
--------------------
Each percent of the chart stands as tall as the speed was while the head sat
on it, so the fill behind the head is the batch's history. The chart used to
read that height at the instant the head crossed a percent, and a stretch
with no speed reading copied the level before it. A track's percent mostly
steps when it settles, after its stream has gone quiet (the speed reads 0
once nothing has arrived for two seconds), and the first level was read as
the chart opened, with the eased speed still near 0. So every step copied a
near-zero level: the chart lay flat along the floor with only the head
bouncing up and down as each track streamed.

HOW THIS STAYS FIXED
--------------------
Boot the real Main.qml offscreen and drive one row through the bridge, the
speed set on the bridge's own property:

* streaming at 100 Mb/s at 0%, then quiet, then the percent steps to 30: the
  passed percents stand at the 100 Mb/s level, not on the floor;
* then 30 to 60 while streaming at 4 Mb/s: those percents stand lower than
  the first stretch, so a slow stretch still reads as a valley.

Runs in a SUBPROCESS like the other Main.qml scenarios (shares the sibling's
``_boot``).
"""

from __future__ import annotations

import math
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from test_progress_matrix_stable_width import _EXIT_OK, _EXIT_REGRESSED, _boot

_FIND = """
 function walk(it, name){
  if (!it) return null;
  if (it.objectName === name) return it;
  for (var i = 0; i < it.children.length; i++) {
   var hit = walk(it.children[i].item || it.children[i], name);
   if (hit) return hit;
  }
  return null;
 }
 function chart(){ return walk(walk(root.contentItem, 'queueButton'), 'queueButtonChart') }
"""


def _level(mbps: float) -> float:
    """The chart's log scale: 2 Mb/s is 0, 400 Mb/s is 1."""
    return min(1.0, max(0.0, (math.log10(mbps) - 0.301) / 2.301))


def test_the_queue_button_chart_keeps_its_history():
    import pytest

    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="waves-queue-chart-test-")
    env["HOME"] = env["XDG_CONFIG_HOME"]
    proc = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), "--run-scenario"],
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    tail = "\n".join([ln for ln in (proc.stdout + proc.stderr).strip().splitlines() if " WARN " not in ln][-12:])
    if proc.returncode == 77:
        pytest.skip("PySide6 / offscreen Qt unavailable")
    if proc.returncode == 78:
        pytest.skip(f"could not set up the scenario in this environment:\n{tail}")
    assert proc.returncode == _EXIT_OK, f"the queue button's chart history regressed:\n{tail}"


def _scenario() -> int:
    booted = _boot()
    if not isinstance(booted, tuple):
        return booted
    _root, q, settle, bridge = booted

    def js(body: str):
        return q("(function(){" + _FIND + body + "})()")

    if not js("return chart() !== null"):
        print("no queueButton with a speed chart in the header", file=sys.stderr)
        return 78

    def speed(mbps: float) -> None:
        bridge._download_mbps = mbps
        bridge.downloadMbpsChanged.emit()

    def bins(lo: int, hi: int) -> list[float]:
        got = js(f"var c = chart(); return c.prof.slice({lo}, {hi}).join('|') + '#' + c.profK")
        vals, k = str(got).split("#")
        if int(k) < hi:
            raise AssertionError(f"the chart passed only {k} percents, wanted {hi}")
        return [float(v) for v in vals.split("|")]

    failures = []
    a = bridge._enqueue("History One", "track", media_id="lab-history-1", artist="Lab", tracks=0)
    bridge._set_queue_status(a, "running")
    bridge._set_queue_progress(a, 0)
    settle(300)
    if not js("return chart().on"):
        print("the chart did not come on for a running row", file=sys.stderr)
        return 78

    # A track streams at full speed, goes quiet as it finishes, and only
    # then does the percent step.
    speed(100.0)
    settle(1500)
    speed(0.0)
    settle(400)
    bridge._set_queue_progress(a, 30)
    settle(500)
    fast = bins(0, 30)
    want = _level(100.0)
    if min(fast) < 0.8 * want:
        failures.append(
            f"streamed at 100 Mb/s then stepped while quiet, the passed percents lie at"
            f" {min(fast):.2f} (want about {want:.2f}): the chart is flat behind the head"
        )

    # A slow stretch, the speed reading as the percent creeps.
    speed(4.0)
    for p in range(31, 61):
        bridge._set_queue_progress(a, p)
        settle(60)
    speed(0.0)
    settle(200)
    slow = bins(31, 60)
    mean_fast, mean_slow = sum(fast) / len(fast), sum(slow) / len(slow)
    if not mean_slow < mean_fast - 0.3:
        failures.append(f"a 4 Mb/s stretch ({mean_slow:.2f}) does not stand below a 100 Mb/s one ({mean_fast:.2f})")

    for f in failures:
        print(f, file=sys.stderr)
    return _EXIT_REGRESSED if failures else _EXIT_OK


if __name__ == "__main__" and "--run-scenario" in sys.argv:
    sys.exit(_scenario())
