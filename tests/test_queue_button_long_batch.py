"""The QUEUE button stays cheap and keeps its picture through a long batch.

WHAT THIS FENCES OFF
--------------------
The button's totals weigh the queued rows by tracks. That weight was cached
by the queued row count alone, and every row the bridge added, dropped or
retried moved the count and measured at once, so queueing N rows walked the
queued group N times (N squared reads: seconds of a frozen window for a few
thousand rows). A swap at the same count (one row starts as another is
queued) or a queued row's track count arriving later left the weight stale.

The speed chart is off mid batch only behind a FAIL flash. A run of failures
that kept it off for more than 2.5 s threw its history away, so it came back
empty with no time left.

HOW THIS STAYS FIXED
--------------------
Boot the real Main.qml offscreen and drive the model the way the bridge's
deltas do:

* 600 rows queued in one pass weigh the queued group at most twice;
* a queued row's track count going from 0 to 12 moves the batch weight;
* FAIL flashes back to back for over 3 s keep the chart's history.

Runs in a SUBPROCESS like the other Main.qml scenarios (shares the sibling's
``_boot``).
"""

from __future__ import annotations

import sys

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
 function qb(){ return walk(root.contentItem, 'queueButton') }
 function chart(){ return walk(qb(), 'queueButtonChart') }
 function row(i, st, tr){
  return { qid: 'long-' + i, name: 'Row ' + i, type: 'album', status: st, progress: 0,
           media_id: 'long-m' + i, template: '', collection: '', tracks: tr };
 }
"""


def test_the_queue_button_stays_cheap_and_keeps_its_history_in_a_long_batch():
    run_scenario_file(__file__, "waves-queue-long-test-", "the queue button in a long batch")


def _scenario() -> int:
    errors = watch_queue_button_errors()
    booted = _boot()
    if not isinstance(booted, tuple):
        return booted
    _root, q, settle, _bridge = booted

    def js(body: str):
        return q("(function(){" + _FIND + body + "})()")

    if not js("return qb() !== null && chart() !== null && root._qwWalks !== undefined"):
        print("no queueButton chart, or no walk counter on the totals", file=sys.stderr)
        return 78

    failures = []
    # One row running, so the chart is on and the timer measures.
    js("root.queueRowAdd(row(0, 'running', 4)); return 0")
    settle(600)
    before = js("return root._qwWalks")
    js("for (var i = 1; i <= 600; ++i) root.queueRowAdd(row(i, 'queued', 1)); return 0")
    settle(300)
    walks = js("return root._qwWalks") - before
    if walks > 2:
        failures.append(f"600 rows queued in one pass weighed the queued group {walks} times")
    w0 = js("return qb().agg.W")
    if abs(w0 - 604) > 1e-6:
        failures.append(f"a running album of 4 and 600 queued singles weigh {w0}, wanted 604")

    js("root.queueRowPatch(row(5, 'queued', 12)); return 0")
    settle(400)
    w1 = js("return qb().agg.W")
    if abs(w1 - 615) > 1e-6:
        failures.append(f"a queued row's track count went 1 to 12 and the batch weighs {w1}, wanted 615")

    # Let the chart gather history, then fail rows back to back for 3.5 s.
    settle(2500)
    hist = js("return (chart().es.hist || []).length")
    for _ in range(4):
        js("qb().say('fail', 1200); return 0")
        settle(950)
    settle(1500)
    after = js("return chart().on ? (chart().es.hist || []).length : -1")
    if after < hist:
        failures.append(f"a run of FAIL flashes threw the chart's history away ({hist} samples, then {after})")
    failures.extend(f"QML error in the queue button: {e}" for e in errors[:5])

    for f in failures:
        print(f, file=sys.stderr)
    return _EXIT_REGRESSED if failures else _EXIT_OK


if __name__ == "__main__" and "--run-scenario" in sys.argv:
    sys.exit(_scenario())
