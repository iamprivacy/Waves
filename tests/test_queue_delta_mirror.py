"""QML's queue model mirrors the bridge through the delta protocol.

The bridge now reports queue changes as deltas (rows added, rows whose fields
moved, rows gone) and Main.qml applies them in place with an index, group
counts and a linger list it maintains itself instead of walking the model
(issue #30's lag half: every change used to reconcile every row). What can
rot silently here is the BOOKKEEPING: an index entry pointing at the wrong
row after a move, a group count drifting from the model, a row landing on
the wrong side of a section boundary. So the real Main.qml is booted
offscreen and driven through the real bridge slots across every kind of
change (adds, a run/finish, failures, STOP, RETRY ALL, clears, a promote),
and after each step the model is audited against the bridge and against its
own mirrors, by walking it the slow way.

Runs in a SUBPROCESS like the other Main.qml scenarios (shares the sibling's
``_boot``).
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

from test_progress_matrix_stable_width import _EXIT_OK, _boot

# The audit: walk the whole model (the slow way, this is a test) and compare
# every mirror the delta handlers maintain against what a walk finds.
_AUDIT = """
(function(){
  var m = queueModel, msgs = []
  var c = 0, f = 0, s = 0, d = 0, q = 0, a = 0
  var groupsSeen = []
  for (var i = 0; i < m.count; ++i) {
    var row = m.get(i)
    if (qidAt[i] !== row.qid) msgs.push('qidAt[' + i + ']=' + qidAt[i] + ' but model holds ' + row.qid)
    if (queueRowIndex[row.qid] !== i) msgs.push('index[' + row.qid + ']=' + queueRowIndex[row.qid] + ' but row sits at ' + i)
    var grp = row.uiGroup
    if (groupsSeen.length === 0 || groupsSeen[groupsSeen.length - 1] !== grp) groupsSeen.push(grp)
    if (grp === 'completed') c++
    else if (grp === 'failed') f++
    else if (grp === 'stopped') s++
    else if (grp === 'downloading') d++
    else q++
    if (row.status === 'queued' || row.status === 'running') a++
  }
  if (qidAt.length !== m.count) msgs.push('qidAt holds ' + qidAt.length + ' of ' + m.count)
  if (c !== completedCount) msgs.push('completedCount ' + completedCount + ' vs ' + c)
  if (f !== failedCount) msgs.push('failedCount ' + failedCount + ' vs ' + f)
  if (s !== stoppedCount) msgs.push('stoppedCount ' + stoppedCount + ' vs ' + s)
  if (d !== downloadingCount) msgs.push('downloadingCount ' + downloadingCount + ' vs ' + d)
  if (q !== queuedCount) msgs.push('queuedCount ' + queuedCount + ' vs ' + q)
  if (a !== activeQueueCount) msgs.push('activeQueueCount ' + activeQueueCount + ' vs ' + a)
  // Groups must be contiguous and in drawer order.
  var order = ['completed', 'failed', 'stopped', 'downloading', 'queued']
  var pos = -1
  for (var g = 0; g < groupsSeen.length; ++g) {
    var at = order.indexOf(groupsSeen[g])
    if (at <= pos) msgs.push('group order broke: ' + groupsSeen.join('>'))
    pos = at
  }
  return msgs.join('; ')
})()
"""


def _audit(q, bridge, label: str, want: dict) -> list[str]:
    problems = []
    msg = q(_AUDIT)
    if msg:
        problems.append(f"{label}: {msg}")
    have = {
        "rows": int(q("queueModel.count")),
        "completed": int(q("completedCount")),
        "failed": int(q("failedCount")),
        "stopped": int(q("stoppedCount")),
        "downloading": int(q("downloadingCount")),
        "queued": int(q("queuedCount")),
    }
    for key, val in want.items():
        if have[key] != val:
            problems.append(f"{label}: {key} is {have[key]}, wanted {val}")
    if len(bridge._queue) != have["rows"]:
        problems.append(f"{label}: bridge holds {len(bridge._queue)} rows, the model {have['rows']}")
    return problems


def _scenario() -> int:
    booted = _boot()
    if not isinstance(booted, tuple):
        return booted
    _root, q, settle, bridge = booted
    problems: list[str] = []

    # 1. A spread of rows through the real enqueue, then the real mutators.
    qids = [bridge._enqueue(f"Album {n}", "album", media_id=f"m{n}", artist="Lab", tracks=10) for n in range(6)]
    settle(80)
    problems += _audit(q, bridge, "after adds", {"rows": 6, "queued": 6})

    bridge._set_queue_status(qids[0], "running")
    settle(80)
    problems += _audit(q, bridge, "first row running", {"downloading": 1, "queued": 5})

    bridge._set_queue_status(qids[0], "failed")
    bridge._set_queue_status(qids[1], "running")
    settle(80)
    problems += _audit(q, bridge, "a failure and a hand-over", {"failed": 1, "downloading": 1, "queued": 4})

    # 2. STOP: every queued or running row lands in Stopped, the failure stays.
    bridge.stopAll()
    settle(80)
    problems += _audit(q, bridge, "after STOP", {"failed": 1, "stopped": 5, "queued": 0, "downloading": 0})

    # 3. RETRY ALL on Stopped: rows re-enter the queue at the back, in order.
    #    (No real download here: the objects are gone, so retries fall back to
    #    the refetch path, which needs a login; drop the rows instead.)
    bridge.clearStopped()
    settle(80)
    problems += _audit(q, bridge, "stopped cleared", {"rows": 1, "failed": 1})
    bridge.clearFailed()
    settle(80)
    problems += _audit(q, bridge, "failed cleared", {"rows": 0})

    # 4. A finished row lingers under Downloading, then promotes to Completed.
    done_qid = bridge._enqueue("Done Album", "album", media_id="mdone", artist="Lab", tracks=10)
    bridge._set_queue_status(done_qid, "running")
    bridge._set_queue_status(done_qid, "done")
    settle(80)
    problems += _audit(q, bridge, "done lingers under downloading", {"downloading": 1, "completed": 0})
    q(f"promoteCompleted({done_qid})")
    settle(80)
    problems += _audit(q, bridge, "after promote", {"downloading": 0, "completed": 1})

    # 5. Scale: thousands of terminal rows behind live churn, still in step.
    bridge._queue_emit_suspended = True
    try:
        for n in range(700):
            fq = bridge._enqueue(f"F{n}", "album", media_id=f"f{n}", artist="Lab", tracks=10)
            bridge._queue_item(fq)["status"] = "failed"
        for n in range(700):
            sq = bridge._enqueue(f"S{n}", "album", media_id=f"s{n}", artist="Lab", tracks=10)
            bridge._queue_item(sq)["status"] = "cancelled"
        live = [bridge._enqueue(f"L{n}", "album", media_id=f"l{n}", artist="Lab", tracks=10) for n in range(120)]
    finally:
        bridge._queue_emit_suspended = False
    bridge._emit_queue()
    settle(150)
    # The queue is past the settled-history cap now, so the one done row is
    # trimmed at the flush and Completed empties: the cap holding at scale is
    # part of what this audit is for.
    problems += _audit(q, bridge, "bulk seed", {"failed": 700, "stopped": 700, "queued": 120, "completed": 0})

    # Every live row runs and fails, one by one: the blocked-account shape.
    for qid in live:
        bridge._set_queue_status(qid, "running")
        bridge._set_queue_status(qid, "failed")
    settle(200)
    problems += _audit(q, bridge, "after the blocked burst", {"failed": 820, "queued": 0, "downloading": 0})

    if problems:
        print("\n".join(problems), file=sys.stderr)
        return 1
    print("MIRROR: OK")
    return _EXIT_OK


def test_the_model_mirrors_the_bridge_through_every_kind_of_change():
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="waves-queue-delta-mirror-")
    env["HOME"] = env["XDG_CONFIG_HOME"]
    proc = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), "--run-queue-delta-mirror"],
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )
    tail = "\n".join((proc.stdout + proc.stderr).strip().splitlines()[-14:])
    if proc.returncode == 77:
        import pytest

        pytest.skip("PySide6 / offscreen Qt unavailable")
    if proc.returncode == 78:
        import pytest

        pytest.skip(f"could not set up the scenario in this environment:\n{tail}")
    assert proc.returncode == _EXIT_OK, f"the model and the bridge disagree:\n{tail}"


if __name__ == "__main__" and "--run-queue-delta-mirror" in sys.argv:
    sys.exit(_scenario())
