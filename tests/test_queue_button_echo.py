"""The header's QUEUE button counts, morphs, and plays its finish inside the window.

WHAT THIS FENCES OFF
--------------------
The button shows the batch while the queue works: its word turns into the
batch percent, the corner count rolls down as downloads finish, and a finish
mid batch bumps the count and bursts a ring of sparks off it (the last one
shatters it, see test_queue_button_finishing). The header starts at the
window's top edge, so a burst drawn at the size it has in the design lab
would be cut off by the edge; it is fitted to the room above the count when
it plays.

The finish keys off ``queueRowSettled``, which the queue's delta bookkeeping
fires when a row leaves the active count. A row that leaves without the signal
would roll the count down with no finish, and a failure that took the finish
route would celebrate a failed download.

HOW THIS STAYS FIXED
--------------------
Boot the real Main.qml offscreen and drive two rows through the bridge:

* both running: the count reads 2 and the word is a percent, not QUEUE, set
  in the header buttons' UI face with tabular digits (so it keeps one width
  as it counts);
* one finishes: the count drops to 1, the word says DONE, the mid batch
  finish plays (the count bumps and stays shown, the sparks burst), and
  sampled through the whole finish nothing it draws reaches above the
  window's top edge;
* the other fails: the word says FAIL, no finish plays, and the failed dot
  shows.

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
"""


def test_the_queue_button_counts_and_finishes_inside_the_window():
    import pytest

    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="waves-queue-button-test-")
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
    assert proc.returncode == _EXIT_OK, f"the queue button regressed:\n{tail}"


def _scenario() -> int:
    booted = _boot()
    if not isinstance(booted, tuple):
        return booted
    _root, q, settle, bridge = booted

    def js(body: str):
        return q("(function(){" + _FIND + body + "})()")

    if not js("return qb() !== null"):
        print("no queueButton in the header", file=sys.stderr)
        return 78

    def state() -> tuple[int, str, bool, int]:
        got = js(
            "var b = qb(), badge = walk(b, 'queueButtonBadge'), w = walk(b, 'queueButtonWord');"
            " return [badge.value, w.target, walk(b, 'queueButtonDone').playing ? 1 : 0, root.failedCount].join('|')"
        )
        v, word, playing, failed = str(got).split("|")
        return int(v), word, playing == "1", int(failed)

    a = bridge._enqueue("Echo One", "track", media_id="lab-echo-1", artist="Lab", tracks=0)
    b = bridge._enqueue("Echo Two", "track", media_id="lab-echo-2", artist="Lab", tracks=0)
    for qid in (a, b):
        bridge._set_queue_status(qid, "running")
        bridge._set_queue_progress(qid, 40)
    settle(500)

    failures = []
    count, word, _tick, _f = state()
    if count != 2:
        failures.append(f"two running rows, the count reads {count}")
    if not word.endswith("%"):
        failures.append(f"while the queue works the word reads {word!r}, wanted the batch percent")
    face = js(
        "var w = walk(qb(), 'queueButtonWord'), f = w.font;"
        " return [f.family === root.uiFont, f.pixelSize, (f.features || {}).tnum].join('|')"
    )
    if str(face) != "true|13|1":
        failures.append(f"the word's face is {face!r}, wanted the UI font at 13px with tabular digits")

    bridge._set_queue_status(a, "done")
    top_seen, played, last, bump, badge_op = 1e9, False, False, 1.0, 1.0
    for _ in range(40):
        settle(20)
        got = js(
            "var d = walk(qb(), 'queueButtonDone');"
            " return [d.playing ? 1 : 0, d.last ? 1 : 0, d.bump, d.badgeOp,"
            "  d.playing && d.reach > 0 ? d.mapToItem(null, d.width / 2, d.height / 2).y - d.reach : 1e9].join('|')"
        )
        p, ls, bm, op, top = str(got).split("|")
        if p == "1":
            played, last = True, last or ls == "1"
            bump, badge_op = max(bump, float(bm)), min(badge_op, float(op))
        top_seen = min(top_seen, float(top))
    count, word, _playing, _f = state()
    if count != 1:
        failures.append(f"one of two finished, the count reads {count}")
    if not played:
        failures.append("a finish played nothing off the count")
    if last:
        failures.append("a finish mid batch played the last download's shatter")
    if bump < 1.05:
        failures.append(f"a finish mid batch did not bump the count (peak {bump:.2f})")
    if badge_op < 0.99:
        failures.append(f"a finish mid batch faded the count (opacity {badge_op:.2f})")
    if top_seen == 1e9:
        failures.append("the finish drew nothing")
    elif top_seen < -0.5:
        failures.append(f"the finish reached {-top_seen:.1f}px above the window's top edge")

    settle(900)
    bridge._set_queue_status(b, "failed", "lab failure")
    settle(80)
    count, word, playing, failed = state()
    if word != "FAIL":
        failures.append(f"a failure reads {word!r}, wanted FAIL")
    if playing:
        failures.append("a failure played the finish")
    if count != 0:
        failures.append(f"nothing active, the count reads {count}")
    if failed < 1 or not js(
        "var b = qb(); for (var i = 0; i < b.children.length; i++)"
        " { var c = b.children[i]; if (c.width === 7 && c.height === 7 && c.visible) return true } return false"
    ):
        failures.append("a failed row shows no failed dot")

    for f in failures:
        print(f, file=sys.stderr)
    return _EXIT_REGRESSED if failures else _EXIT_OK


if __name__ == "__main__" and "--run-scenario" in sys.argv:
    sys.exit(_scenario())
