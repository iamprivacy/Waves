"""The QUEUE button's chart keeps a minute of speed and reads the time left.

WHAT THIS FENCES OFF
--------------------
The button's chart is the batch's speed over the last minute, a sample every
250 ms of wall time, newest on the right, so a slow stretch stands lower
than a fast one behind it. Over the speed it reads the time left: the tracks
still to go over the rate tracks have landed at in the last 12 s, the shown
figure changing at most once a second, and "--:--" until 3 s of history make
a rate.

HOW THIS STAYS FIXED
--------------------
Boot the real Main.qml offscreen and drive one row through the bridge, the
speed set on the bridge's own property, the percent creeping:

* before 3 s of history the time left reads unknown;
* 100 Mb/s for a stretch, then 4 Mb/s: both stretches are in the history in
  that order, so the slow one stands lower than the fast one;
* with the percent moving the time left becomes a real figure, and it holds
  for at least a second between changes;
* the history keeps a sample per 250 ms of wall time, never more than a
  minute of it.

Runs in a SUBPROCESS like the other Main.qml scenarios (shares the sibling's
``_boot``).
"""

from __future__ import annotations

import sys
from itertools import pairwise

from test_progress_matrix_stable_width import _EXIT_OK, _EXIT_REGRESSED, _boot
from test_queue_button_finishing import run_scenario_file, watch_queue_button_errors

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


def test_the_queue_button_chart_keeps_its_history():
    run_scenario_file(__file__, "waves-queue-chart-test-", "the queue button's chart history")


def _scenario() -> int:
    errors = watch_queue_button_errors()
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

    def history() -> list[tuple[float, float]]:
        got = js("var H = chart().es.hist || []; return H.map(function (h) { return h.t + ':' + h.mb }).join('|')")
        return [(float(t), float(m)) for t, m in (p.split(":") for p in str(got).split("|") if p)]

    def eta():
        return js("var e = chart().es.eta; return e === undefined ? -1 : e")

    failures = []
    a = bridge._enqueue("History One", "track", media_id="lab-history-1", artist="Lab", tracks=0)
    bridge._set_queue_status(a, "running")
    bridge._set_queue_progress(a, 0)
    settle(300)
    if not js("return chart().on"):
        print("the chart did not come on for a running row", file=sys.stderr)
        return 78
    if eta() >= 0:
        failures.append(f"with under 3 s of history the time left reads {eta()}, wanted unknown")

    # A fast stretch, then a slow one, the percent creeping all along.
    pct = 0
    speed(100.0)
    for _ in range(8):
        pct += 4
        bridge._set_queue_progress(a, pct)
        settle(250)
    speed(4.0)
    for _ in range(8):
        pct += 2
        bridge._set_queue_progress(a, pct)
        settle(250)
    speed(0.0)
    settle(300)
    H = history()
    fast = [i for i, (_t, m) in enumerate(H) if abs(m - 100.0) < 1e-6]
    slow = [i for i, (_t, m) in enumerate(H) if abs(m - 4.0) < 1e-6]
    if len(fast) < 5 or len(slow) < 5:
        failures.append(f"the history lost a stretch: {len(fast)} fast and {len(slow)} slow samples")
    elif max(fast) > min(slow):
        failures.append("the slow stretch does not follow the fast one in the history")
    steps = [b[0] - a_[0] for a_, b in pairwise(H)]
    if any(abs(s - 250) > 1e-6 for s in steps):
        failures.append(f"the history is not a sample per 250 ms: {sorted(set(steps))[:5]}")

    # The time left: a figure once there is a rate, held a second at a time.
    pct_left = []
    for _ in range(14):
        pct += 1
        bridge._set_queue_progress(a, pct)
        settle(100)
        pct_left.append(eta())
    if max(pct_left) < 0:
        failures.append("with the percent moving the time left never became a figure")
    changes = sum(1 for x, y in pairwise(pct_left) if x != y)
    if changes > 2:
        failures.append(f"the time left changed {changes} times in 1.4 s, wanted at most once a second")
    if len(history()) > 240:
        failures.append("the history holds more than a minute")
    failures.extend(f"QML error in the queue button: {e}" for e in errors[:5])

    for f in failures:
        print(f, file=sys.stderr)
    return _EXIT_REGRESSED if failures else _EXIT_OK


if __name__ == "__main__" and "--run-scenario" in sys.argv:
    sys.exit(_scenario())
