"""The QUEUE button is a speed chart that pours in while a download finishes.

WHAT THIS FENCES OFF
--------------------
While anything downloads the button's inside is the batch's speed chart, with
a tag reading the percent over the speed; the word hides under it and the
arrow folds away. Once every running download sits at 100%, only the final
steps are left (merge, extract, tagging) and the percent would just freeze.
Instead the chart pours in: it settles to one level that rises to the brim as
the real finish steps land. When the row is done the pour hands over "into
the count": it tips toward the count badge and the chart draws DONE itself
while the word stays hidden, then the handoff ends and the chart goes, with no
scrambled word on the way.

It is the last download, so the count does not just roll to nothing: it
shatters into pieces that fall back in as a check and twinkle out, and the
count comes back (hidden at 0) once that has played.

The button holds the arrow's and QUEUE's width itself; a word change or the
arrow folding must never move the header.

HOW THIS STAYS FIXED
--------------------
Boot the real Main.qml offscreen and drive one row through the bridge:

* at 40%: the chart is on and not pouring, the word hidden, the arrow folded,
  and with nothing arriving it settles off the full frame rate;
* at 100%: finishing, and the chart pours;
* as the row's finish steps land (stage events through the real lifecycle
  handler) the header's fraction follows them and the pour fills to it:
  about half at half the steps, the brim once they are all in;
* done: the handoff plays (the chart stays on and draws DONE, the word
  hidden under it), then ends and the chart goes, never a scrambled word on
  the way; the count's finish plays the last download's
  shatter, the count fades out under it, nothing it draws goes above the
  window's top edge, and once it ends the count is back at full opacity;
* once DONE has gone the arrow is back, and the next download opens on the
  chart, not on the pour;
* through all of it the button keeps one width.

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

_SCRAMBLE = set("#%&*+=<>/\\~")


def test_the_queue_button_pours_while_a_download_finishes():
    import pytest

    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="waves-queue-finishing-test-")
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
    assert proc.returncode == _EXIT_OK, f"the queue button's finishing regressed:\n{tail}"


def _scenario() -> int:
    booted = _boot()
    if not isinstance(booted, tuple):
        return booted
    _root, q, settle, bridge = booted

    def js(body: str):
        return q("(function(){" + _FIND + body + "})()")

    if not js("return qb() !== null && walk(qb(), 'queueButtonChart') !== null"):
        print("no queueButton with a speed chart in the header", file=sys.stderr)
        return 78

    def state() -> dict:
        got = js(
            "var b = qb(), c = walk(b, 'queueButtonChart'), w = walk(b, 'queueButtonWord');"
            " return [b.finishing ? 1 : 0, c.on ? 1 : 0, c.pouring ? 1 : 0, c.fv, w.opacity, w.text, b.fz, b.width].join('|')"
        )
        fin, on, pour, fv, op, text, fz, width = str(got).split("|")
        return {
            "fin": fin == "1",
            "on": on == "1",
            "pour": pour == "1",
            "fv": float(fv),
            "op": float(op),
            "text": text,
            "fz": float(fz),
            "width": float(width),
        }

    arrow = "walk(qb(), 'queueButtonArrow')"
    failures = []
    widths = {state()["width"]}
    a = bridge._enqueue("Finish One", "track", media_id="lab-finish-1", artist="Lab", tracks=0)
    bridge._set_queue_status(a, "running")
    bridge._set_queue_progress(a, 40)
    settle(600)
    s = state()
    widths.add(s["width"])
    if s["fin"] or s["pour"] or not s["on"]:
        failures.append(f"at 40% the chart is not just charting: {s}")
    if s["op"] > 0.02 or s["fz"] < 0.99:
        failures.append(f"at 40% the word shows over the chart or the arrow is out: {s}")
    if float(js(f"return {arrow}.opacity")) > 0.02:
        failures.append("at 40% the arrow still shows next to the chart")
    # Nothing arrives in this scenario, so once the sweep-in is over the
    # chart has settled and must stop drawing at full rate; the finish below
    # then has to wake it on its own.
    settle(400)
    if js("return walk(qb(), 'queueButtonChart').busy") is not False:
        failures.append("a settled chart with nothing arriving still repaints at full rate")

    bridge._set_queue_progress(a, 100)
    settle(1200)
    s = state()
    widths.add(s["width"])
    if not s["fin"]:
        failures.append("every running row at 100% and the button is not finishing")
    if not s["pour"]:
        failures.append(f"finishing, the chart does not pour: {s}")

    # The pour paces to the real finish steps: the row's stage events (the
    # engine's _note_stage, through the real lifecycle handler) roll up into
    # the fraction the header reads, and the level rises to it.
    bridge._track_lifecycle(a, {"id": "lab-finish-1", "status": "running", "fpct": 50.0})
    settle(1600)
    goal, s = float(js("return qb().finFrac")), state()
    if abs(goal - 0.5) > 0.01:
        failures.append(f"half the finish steps landed and the header reads {goal:.2f}")
    if not 0.4 <= s["fv"] <= 0.6:
        failures.append(f"half the finish steps landed and the pour is {s['fv']:.2f} full")
    bridge._track_lifecycle(a, {"id": "lab-finish-1", "status": "running", "fpct": 100.0})
    settle(2000)
    s = state()
    if s["fv"] < 0.95:
        failures.append(f"every finish step landed and the pour is {s['fv']:.2f} full")

    bridge._set_queue_status(a, "done")
    seen, handed, ended = set(), False, False
    last, badge_op, top_seen = False, 1.0, 1e9
    for _ in range(140):
        settle(20)
        got = js(
            "var d = walk(qb(), 'queueButtonDone');"
            " return [d.playing && d.last ? 1 : 0, d.badgeOp,"
            "  d.playing && d.reach > 0 ? d.mapToItem(null, d.width / 2, d.height / 2).y - d.reach : 1e9].join('|')"
        )
        ls, op, top = str(got).split("|")
        last |= ls == "1"
        badge_op = min(badge_op, float(op))
        top_seen = min(top_seen, float(top))
        s = state()
        widths.add(s["width"])
        x = str(
            js("var c = walk(qb(), 'queueButtonChart'); return [c.xT > 0 ? 1 : 0, c.xEnd ? 1 : 0].join('|')")
        ).split("|")
        if x[0] == "1":
            handed = True
            if x[1] != "1":
                failures.append("the last download's handoff did not read as the batch end")
            if not s["on"] or s["op"] > 0.02:
                failures.append(f"during the handoff the chart is off or the word shows over it: {s}")
        elif handed:
            ended = True
        # Words shown until the handoff ends: after it, DONE giving way to
        # QUEUE is the button's own word change, scramble and all.
        if s["op"] > 0.02 and not ended:
            seen.add(s["text"])
    if not handed:
        failures.append("the row finished and the pour never handed over into the count")
    elif not ended:
        failures.append("the handoff never ended")
    elif state()["on"]:
        failures.append("the handoff ended and the chart is still on")
    scrambled = sorted(t for t in seen if _SCRAMBLE & set(t) and not t.endswith("%"))
    if scrambled:
        failures.append(f"the word scrambled on its way to DONE: {scrambled}")
    if not last:
        failures.append("the last download did not play the shatter")
    if badge_op > 0.02:
        failures.append(f"the shatter left the count showing (opacity {badge_op:.2f})")
    if top_seen < -0.5:
        failures.append(f"the shatter reached {-top_seen:.1f}px above the window's top edge")
    settle(800)
    if js("var d = walk(qb(), 'queueButtonDone'); return d.playing || d.badgeOp < 0.99"):
        failures.append("the shatter never ended, or the count stayed faded after it")
    # The next download, started once DONE has gone: it opens at 0%, so the
    # button must not be finishing and the chart must not open pouring. The
    # finishing flag used to be left standing when the last row settled (its
    # timer stops with the queue), so the next start wore the finish first.
    for _ in range(40):
        if js("return qb().flash") == "":
            break
        settle(100)
    settle(400)
    if float(js(f"return {arrow}.opacity")) < 0.99:
        failures.append("idle again, the arrow did not come back")
    b = bridge._enqueue("Finish Two", "track", media_id="lab-finish-2", artist="Lab", tracks=0)
    bridge._set_queue_status(b, "running")
    bridge._set_queue_progress(b, 10)
    for _ in range(12):
        settle(30)
        s = state()
        if s["fin"] or s["pour"]:
            failures.append(f"the next download opened finishing: {s}")
            break
    if len(widths) != 1:
        failures.append(f"the button changed width: {sorted(widths)}")

    for f in failures:
        print(f, file=sys.stderr)
    return _EXIT_REGRESSED if failures else _EXIT_OK


if __name__ == "__main__" and "--run-scenario" in sys.argv:
    sys.exit(_scenario())
