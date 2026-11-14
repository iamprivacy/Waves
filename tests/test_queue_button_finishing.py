"""The QUEUE button draws the whole batch by tracks and lands it quietly.

WHAT THIS FENCES OFF
--------------------
While anything is active the button's inside is the batch's throughput chart
(queue item lab, T1): a minute of speed history, the batch along the floor by
tracks (landed bright, in flight pale), the time left over the speed. The
word hides under it and the arrow folds away. It reads a few totals counted
once a tick, so a thousand-item queue costs a frame what five do.

The totals weigh rows by tracks: an album lands as more of the batch than a
single, a running row's download fills 85% of its weight and its finish
steps (merge, extract, tagging) the rest, and a settled row stays counted
after it has left the active groups.

Mid batch a landing does not flash DONE (with long queues it would strobe):
the chart keeps the button and the count sparks. The batch landing whole
flashes DONE and the last download shatters the count.

The button holds the arrow's and QUEUE's width itself; a word change or the
arrow folding must never move the header.

HOW THIS STAYS FIXED
--------------------
Boot the real Main.qml offscreen and drive a 10-track album and a single:

* album at 40%, single queued: the chart is on, the word hidden, the arrow
  folded; the totals read 11 tracks with 3.4 in flight and the floor eases to
  that share; with nothing arriving it settles off the full frame rate;
* album at 100% with half its finish steps in: 9.25 in flight, one building;
* album done: 10 tracks landed, no DONE word, the chart still on, the count's
  mid batch sparks play (not the shatter);
* single done: DONE, the chart goes, the shatter plays inside the window and
  the count comes back at full opacity after it;
* idle again the arrow is back, and the next batch opens on a fresh history;
* through all of it the button keeps one width, and the button's code logs
  no QML error.

Runs in a SUBPROCESS like the other Main.qml scenarios (shares the sibling's
``_boot``).
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from test_progress_matrix_stable_width import _EXIT_OK, _EXIT_REGRESSED, QML_MAIN, _boot

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


_HANDLERS: list = []


def watch_queue_button_errors() -> list[str]:
    """Collect QML messages that point into the QUEUE button's code (its
    components and root.queueBatchTotals). The app's diagnostics put their
    own Qt message handler in during the boot and route every message to the
    ``waves.qt`` logger, so that is listened to as well as Qt itself."""
    import logging

    from PySide6.QtCore import qInstallMessageHandler

    src = QML_MAIN.read_text().splitlines()

    def at(prefix: str) -> int:
        return next(i for i, ln in enumerate(src) if ln.startswith(prefix)) + 1

    spans = [
        (at("    component QueueButton:"), at("    component DecryptText:")),
        (at("    function queueBatchTotals("), at("    function queueBatchTotals(") + 40),
    ]
    pat = re.compile(r"Main\.qml:(\d+)")
    seen: list[str] = []

    def note(msg: str) -> None:
        if any(lo <= int(m.group(1)) <= hi for m in pat.finditer(msg) for lo, hi in spans):
            seen.append(msg)

    def handler(_mode, _ctx, msg):
        note(msg)
        sys.stderr.write(msg + "\n")

    class _Listen(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            note(record.getMessage())

    log = logging.getLogger("waves.qt")
    log.addHandler(_Listen())
    log.setLevel(min(log.level or logging.WARNING, logging.WARNING))
    # PySide keeps no reference of its own: held here, or it is collected.
    _HANDLERS.append(handler)
    qInstallMessageHandler(handler)
    return seen


def run_scenario_file(path: str, prefix: str, what: str) -> None:
    import pytest

    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix=prefix)
    env["HOME"] = env["XDG_CONFIG_HOME"]
    proc = subprocess.run(
        [sys.executable, str(Path(path).resolve()), "--run-scenario"],
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
    assert proc.returncode == _EXIT_OK, f"{what} regressed:\n{tail}"


def test_the_queue_button_draws_the_batch_by_tracks_and_lands_it():
    run_scenario_file(__file__, "waves-queue-finishing-test-", "the queue button's batch chart")


def _scenario() -> int:
    errors = watch_queue_button_errors()
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
            "var b = qb(), c = walk(b, 'queueButtonChart'), w = walk(b, 'queueButtonWord'), g = b.agg;"
            " return [c.on ? 1 : 0, w.opacity, w.target, b.fz, b.width, g.W, g.L, g.F, g.nb,"
            "  c.es.P === undefined ? -1 : c.es.P, c.es.Lw === undefined ? -1 : c.es.Lw,"
            "  (c.es.hist || []).length].join('|')"
        )
        on, op, word, fz, width, W, L, F, nb, P, Lw, hist = str(got).split("|")
        return {
            "on": on == "1",
            "op": float(op),
            "word": word,
            "fz": float(fz),
            "width": float(width),
            "W": float(W),
            "L": float(L),
            "F": float(F),
            "nb": int(nb),
            "P": float(P),
            "Lw": float(Lw),
            "hist": int(hist),
        }

    arrow = "walk(qb(), 'queueButtonArrow')"
    failures = []
    widths = {state()["width"]}
    a = bridge._enqueue("Batch Album", "album", media_id="lab-batch-1", artist="Lab", tracks=10)
    b = bridge._enqueue("Batch Single", "track", media_id="lab-batch-2", artist="Lab", tracks=0)
    bridge._set_queue_status(a, "running")
    bridge._set_queue_progress(a, 40)
    settle(1500)
    s = state()
    widths.add(s["width"])
    if not s["on"]:
        failures.append(f"an album running, the chart is off: {s}")
    if s["op"] > 0.02 or s["fz"] < 0.99:
        failures.append(f"while running the word shows over the chart or the arrow is out: {s}")
    if float(js(f"return {arrow}.opacity")) > 0.02:
        failures.append("while running the arrow still shows next to the chart")
    if abs(s["W"] - 11) > 1e-6 or s["L"] != 0 or abs(s["F"] - 3.4) > 0.01:
        failures.append(f"album at 40% and a queued single: wanted 11 tracks, 0 landed, 3.4 in flight: {s}")
    if abs(s["P"] - 3.4 / 11) > 0.01:
        failures.append(f"the floor did not ease to the batch's share in flight ({s['P']:.3f})")
    # Nothing arrives in this scenario, so once eased the chart has settled
    # and must stop drawing at full rate.
    settle(400)
    if js("return walk(qb(), 'queueButtonChart').busy") is not False:
        failures.append("a settled chart with nothing arriving still repaints at full rate")

    bridge._set_queue_progress(a, 100)
    bridge._track_lifecycle(a, {"id": "lab-batch-1", "status": "running", "fpct": 50.0})
    settle(600)
    s = state()
    if abs(s["F"] - 9.25) > 0.01 or s["nb"] != 1:
        failures.append(f"album downloaded with half its finish steps in: wanted 9.25 in flight, 1 building: {s}")

    bridge._set_queue_status(a, "done")
    played, last_mid, words = False, False, set()
    for _ in range(30):
        settle(20)
        got = js("var d = walk(qb(), 'queueButtonDone'); return [d.playing ? 1 : 0, d.last ? 1 : 0].join('|')")
        p, ls = str(got).split("|")
        played |= p == "1"
        last_mid |= p == "1" and ls == "1"
        words.add(state()["word"])
    settle(1400)
    s = state()
    widths.add(s["width"])
    if abs(s["L"] - 10) > 1e-6 or abs(s["W"] - 11) > 1e-6:
        failures.append(f"the album landed and the totals did not keep its 10 tracks: {s}")
    if abs(s["Lw"] - 10 / 11) > 0.01:
        failures.append(f"the floor's landed part did not ease to the album's share ({s['Lw']:.3f})")
    if "DONE" in words:
        failures.append("a landing mid batch flashed DONE")
    if not s["on"]:
        failures.append("mid batch the chart gave the button up")
    if not played or last_mid:
        failures.append(f"mid batch the count's sparks did not play, or played the shatter (played {played})")

    bridge._set_queue_status(b, "running")
    bridge._set_queue_progress(b, 50)
    settle(300)
    bridge._set_queue_status(b, "done")
    last, badge_op, top_seen, done_seen = False, 1.0, 1e9, False
    for _ in range(100):
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
        if s["word"] == "DONE" and s["op"] > 0.5:
            done_seen = True
            if s["on"]:
                failures.append("DONE shows and the chart is still on")
    if not done_seen:
        failures.append("the batch landed whole and the word never said DONE")
    if not last:
        failures.append("the last download did not play the shatter")
    if badge_op > 0.02:
        failures.append(f"the shatter left the count showing (opacity {badge_op:.2f})")
    if top_seen < -0.5:
        failures.append(f"the shatter reached {-top_seen:.1f}px above the window's top edge")
    settle(800)
    if js("var d = walk(qb(), 'queueButtonDone'); return d.playing || d.badgeOp < 0.99"):
        failures.append("the shatter never ended, or the count stayed faded after it")

    for _ in range(40):
        if js("return qb().flash") == "":
            break
        settle(100)
    settle(400)
    if float(js(f"return {arrow}.opacity")) < 0.99:
        failures.append("idle again, the arrow did not come back")
    c = bridge._enqueue("Batch Next", "track", media_id="lab-batch-3", artist="Lab", tracks=0)
    bridge._set_queue_status(c, "running")
    bridge._set_queue_progress(c, 10)
    settle(300)
    s = state()
    if not s["on"] or s["hist"] > 4:
        failures.append(f"the next batch did not open on a fresh history: {s}")
    if s["L"] != 0 or abs(s["W"] - 1) > 1e-6:
        failures.append(f"the next batch kept the last one's totals: {s}")
    if len(widths) != 1:
        failures.append(f"the button changed width: {sorted(widths)}")
    failures.extend(f"QML error in the queue button: {e}" for e in errors[:5])

    for f in failures:
        print(f, file=sys.stderr)
    return _EXIT_REGRESSED if failures else _EXIT_OK


if __name__ == "__main__" and "--run-scenario" in sys.argv:
    sys.exit(_scenario())
