"""The QUEUE button's count reads a long queue, and its sparks keep calm.

WHAT THIS FENCES OFF
--------------------
The corner count used to stop at "99+". It now reads the true count up to
999, then 1k to 9k, then 9k+ (the LED font has a k). A change that leaves the
text the same (1,500 to 1,400) does not roll, and a change that comes less
than 600 ms after the last roll switches at once, or a steady stream of
landings would keep it stuck mid-roll.

The sparks a landing bursts off the count play at most once every 1.5 s, and
never mid batch in a batch of more than 20 items; the last download's
shatter always plays.

HOW THIS STAYS FIXED
--------------------
Boot the real Main.qml offscreen and drive the count and the finish on the
real button:

* the count's text for 7, 999, 1000, 1999, 9999 and 10000;
* the LED font has a k;
* 1500 then 1400 does not roll; 2100 rolls; 3100 at once after it does not;
  4100 well after rolls again;
* a mid-batch finish in a batch of 30 plays nothing; in a batch of 5 it
  plays, and a second one 200 ms later does not start over; the last one
  plays right after it anyway.

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
 function badge(){ return walk(qb(), 'queueButtonBadge') }
 function done(){ return walk(qb(), 'queueButtonDone') }
"""


def test_the_queue_button_count_reads_long_queues():
    run_scenario_file(__file__, "waves-queue-badge-test-", "the queue button's count at scale")


def _scenario() -> int:
    errors = watch_queue_button_errors()
    booted = _boot()
    if not isinstance(booted, tuple):
        return booted
    _root, q, settle, _bridge = booted

    def js(body: str):
        return q("(function(){" + _FIND + body + "})()")

    if not js("return badge() !== null && done() !== null"):
        print("no queueButton count in the header", file=sys.stderr)
        return 78

    failures = []
    for v, want in [(7, "7"), (999, "999"), (1000, "1k"), (1999, "1k"), (9999, "9k"), (10000, "9k+")]:
        got = js(f"return badge().fmt({v})")
        if got != want:
            failures.append(f"the count for {v} reads {got!r}, wanted {want!r}")
    if not js(
        "var b = badge(); for (var i = 0; i < b.children.length; i++)"
        " { var f = b.children[i].f3; if (f) return f.k !== undefined } return false"
    ):
        failures.append("the LED font has no k")

    def count(v: int, wait: int = 40) -> tuple[float, str, str]:
        js(f"root.activeQueueCount = {v}; return 0")
        settle(wait)
        got = js(
            "var b = badge(), t = []; for (var i = 0; i < b.children.length; i++)"
            " if (b.children[i].cur !== undefined) t = [b.children[i].cur, b.children[i].old];"
            " return [b.t].concat(t).join('|')"
        )
        t, cur, old = str(got).split("|")
        return float(t), cur, old

    count(1500, 400)
    t, cur, _old = count(1400)
    if t < 1 or cur != "1k":
        failures.append(f"1,500 to 1,400 keeps the text and still rolled (t {t:.2f}, {cur!r})")
    t, cur, old = count(2100)
    if not t < 1 or cur != "2k" or old != "1k":
        failures.append(f"1k to 2k did not roll (t {t:.2f}, {old!r} to {cur!r})")
    t, cur, _old = count(3100)
    if t < 1 or cur != "3k":
        failures.append(f"a change 40 ms after a roll rolled again instead of switching (t {t:.2f}, {cur!r})")
    settle(700)
    t, cur, _old = count(4100)
    if not t < 1 or cur != "4k":
        failures.append(f"a change well after the last roll did not roll (t {t:.2f}, {cur!r})")
    settle(500)

    # The sparks: the queue still has work (count 5), so a finish is mid batch.
    js("root.activeQueueCount = 5; qb().batchTotal = 30; done().playing = false; qb().finish(); return 0")
    settle(30)
    if js("return done().playing"):
        failures.append("a landing mid batch in a batch of 30 burst the sparks")
    js("qb().batchTotal = 5; qb().finish(); return 0")
    settle(30)
    t0 = js("return done().playing ? done().t0 : -1")
    if t0 is None or t0 < 0:
        failures.append("a landing mid batch in a batch of 5 burst nothing")
    settle(200)
    js("qb().finish(); return 0")
    settle(30)
    if js("return done().t0") != t0:
        failures.append("a second landing 200 ms after the first burst the sparks again")
    js("root.activeQueueCount = 0; qb().finish(); return 0")
    settle(30)
    if not js("return done().playing && done().last"):
        failures.append("the last download's shatter did not play right after a burst")
    failures.extend(f"QML error in the queue button: {e}" for e in errors[:5])

    for f in failures:
        print(f, file=sys.stderr)
    return _EXIT_REGRESSED if failures else _EXIT_OK


if __name__ == "__main__" and "--run-scenario" in sys.argv:
    sys.exit(_scenario())
