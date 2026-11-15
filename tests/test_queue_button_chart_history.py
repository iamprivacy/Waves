"""The QUEUE button's chart draws the batch's speed over time, its figure in the bottom left.

WHAT THIS FENCES OFF
--------------------
The button's chart is the batch's speed, a sample every 250 ms of wall time,
drawn over the download's own clock from the batch's start at the left edge
to a front that keeps step with the download, so a short album spans the
button as its last bytes land; a slow stretch stands lower than a fast one
behind it. The download complete, the clock stops and the trace holds still
through the finish steps (merge, extract, tagging) and the landing, instead
of sliding over to fill the floor's last share. The download speed reads in
the bottom left corner, clear of the floor's line, and nothing else is
written over the chart (the time left it used to carry is gone), so the
speed's shape and the floor read whole.

HOW THIS STAYS FIXED
--------------------
Boot the real Main.qml offscreen and drive one row through the bridge, the
speed set on the bridge's own property, the percent creeping:

* a quarter of the way in, the speed reaches the batch's front and no
  further;
* the speed's words sit in the bottom left, leave the floor's line and the
  chart's upper half untouched, and stay there;
* 100 Mb/s for a stretch, then 4 Mb/s: both stretches are in the trace in
  that order, a sample per 250 ms, and on screen the fast one stands tall
  and the slow one low;
* a fast sprint to the end of that short batch carries the speed to the
  right edge;
* the download complete, the drawn speed holds still, column for column,
  through half the finish steps and all of them, and its clock stops;
* a long batch's trace merges past two entries a pixel, keeping its start
  and each stretch's mean speed.

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


def _band(js):
    """The chart's pixels, its inside box and the lower band's rows."""
    import base64

    from PySide6.QtGui import QImage

    url = str(js("return chart().toDataURL('image/png')"))
    img = QImage.fromData(base64.b64decode(url.split(",", 1)[1]))
    s = float(js("return chart().s"))
    x0, y0, w, h = (float(v) * s for v in str(js("return chart().box().join(',')")).split(","))
    rows = range(int(y0 + h * 0.72), int(y0 + h - 3 * s))

    def lit(xa: float, xb: float) -> int:
        return sum(1 for x in range(int(xa), int(xb)) for y in rows if img.pixelColor(x, y).alpha() > 24)

    return img, s, x0, w, lit


def _follows_the_speed(js, settle) -> list[str]:
    """The drawn speed rises and falls with the speed, in time order.

    A fast stretch then a slow one: with the words hidden, the upper half of
    the chart is lit over the fast stretch (the left part of the speed) and
    dark over the slow one (the right part), not one flat level throughout.
    """
    import base64

    from PySide6.QtGui import QImage

    # A settled chart paints four times a second: ask for the frame.
    js("chart().wordAlpha = 0; chart().keyAlpha = 0; chart().requestPaint(); return 0")
    settle(150)
    url = str(js("return chart().toDataURL('image/png')"))
    js("chart().wordAlpha = 1; chart().keyAlpha = 0.9; chart().requestPaint(); return 0")
    img = QImage.fromData(base64.b64decode(url.split(",", 1)[1]))
    if img.isNull():
        return ["the chart's pixels could not be read"]
    s = float(js("return chart().s"))
    x0, y0, w, h = (float(v) * s for v in str(js("return chart().box().join(',')")).split(","))
    p = float(js("return chart().es.Pd"))
    n = int(js("return chart().es.tr.length"))
    span = min(w, max(w * p, min(n * s, w * 0.12)))
    rows = range(int(y0 + h * 0.15), int(y0 + h * 0.5))

    def lit(fa: float, fb: float) -> int:
        xs = range(int(x0 + span * fa), int(x0 + span * fb))
        return sum(1 for x in xs for y in rows if img.pixelColor(x, y).alpha() > 24)

    fast, slow = lit(0.2, 0.4), lit(0.62, 0.85)
    if fast == 0 or slow > fast // 4:
        return [f"the drawn speed does not follow the speed: {fast} lit over the fast stretch, {slow} over the slow"]
    return []


def _reaches_the_end(js) -> list[str]:
    """Near the end of a short batch the speed reaches the right side.

    The batch was seconds long, a few dozen samples; drawn a pixel a sample
    they would end a third of the way across. Spread over the download's
    span, the speed's front stands where the download does: at the right
    edge once its last bytes are in, whatever the finish steps still do.
    """
    img, s, x0, w, lit = _band(js)
    if img.isNull():
        return ["the chart's pixels could not be read"]
    p = float(js("return chart().es.Pd"))
    if p < 0.97:
        return [f"the sprint left the download {p:.2f} along, wanted it about complete to check the end"]
    if lit(x0 + w * 0.9, x0 + w * 0.97) == 0:
        return ["the download is complete but the speed stops short of the right edge"]
    return []


def _words_bottom_left(js, settle) -> list[str]:
    """The speed's words sit in the bottom left, clear of the floor's line.

    Drawn with and without the words (the speed itself hidden both times,
    the chart settled so nothing else moves between them), the frames differ only in the chart's lower left: the floor's line (its
    1.5 px and a pixel over it), the upper half and the right half match
    exactly, and the lower left quarter holds the words.
    """
    import base64

    from PySide6.QtGui import QImage

    def grab(words: bool) -> QImage:
        a = "1" if words else "0"
        js(
            "var c = chart(); c.areaAlpha = 0; c.lineAlpha = 0; c.wordAlpha = " + a + ";"
            " c.keyAlpha = " + ("0.9" if words else "0") + "; c.requestPaint(); return 0"
        )
        settle(150)
        url = str(js("return chart().toDataURL('image/png')"))
        return QImage.fromData(base64.b64decode(url.split(",", 1)[1]))

    on, off = grab(True), grab(False)
    js("var c = chart(); c.areaAlpha = 0.22; c.lineAlpha = 0.8; c.wordAlpha = 1; c.keyAlpha = 0.9; return 0")
    if on.isNull() or off.isNull():
        return ["the chart's pixels could not be read"]
    s = float(js("return chart().s"))
    x0, y0, w, h = (float(v) * s for v in str(js("return chart().box().join(',')")).split(","))
    bot = y0 + h

    def differ(xa: float, xb: float, ya: float, yb: float) -> int:
        return sum(
            1
            for x in range(int(xa), int(xb))
            for y in range(int(ya), int(yb))
            if on.pixelColor(x, y).rgba() != off.pixelColor(x, y).rgba()
        )

    out = []
    if differ(x0, x0 + w, bot - 2.5 * s, bot):
        out.append("the speed's words reach the floor's line along the bottom")
    if differ(x0, x0 + w, y0, y0 + h * 0.4):
        out.append("words are still drawn in the chart's upper part (the time left should be gone)")
    if differ(x0 + w * 0.6, x0 + w, y0, bot):
        out.append("words are drawn on the chart's right side, wanted the bottom left only")
    if differ(x0, x0 + w * 0.5, y0 + h * 0.4, bot - 2.5 * s) == 0:
        out.append("no speed words in the chart's bottom left")
    return out


def _columns(js, settle) -> list[int]:
    """The lower band's lit height per column, the words hidden."""
    # A settled chart paints four times a second: ask for the frame.
    js("chart().wordAlpha = 0; chart().keyAlpha = 0; chart().requestPaint(); return 0")
    settle(150)
    img, s, x0, w, _lit = _band(js)
    js("chart().wordAlpha = 1; chart().keyAlpha = 0.9; chart().requestPaint(); return 0")
    _x, y0, _w, h = (float(v) * s for v in str(js("return chart().box().join(',')")).split(","))
    rows = range(int(y0 + h * 0.15), int(y0 + h - 3 * s))
    return [sum(1 for y in rows if img.pixelColor(x, y).alpha() > 24) for x in range(int(x0), int(x0 + w))]


def _holds_still_while_finishing(js, settle, bridge, a) -> list[str]:
    """The download complete, the finish steps move nothing in the trace.

    Before, the front followed the floor, which gives a row's finish steps
    its last 15%: as they ran, the whole line slid right to fill it. Now the
    clock stops with the download, so the band reads the same, column for
    column, with none, half and all of the finish steps in, and the clock
    has not moved.
    """
    before = _columns(js, settle)
    dc = float(js("return chart().es.dc"))
    out = []
    for fpct in (50.0, 100.0):
        bridge._track_lifecycle(a, {"id": "lab-history-1", "status": "running", "fpct": fpct})
        settle(600)
        after = _columns(js, settle)
        moved = sum(1 for u, v in zip(before, after, strict=True) if abs(u - v) > 1)
        if moved:
            out.append(f"with {fpct:.0f}% of the finish steps in, {moved} of {len(before)} columns of the speed moved")
    if float(js("return chart().es.dc")) - dc > 1e-6:
        out.append("the download clock kept running through the finish steps")
    return out


def _keeps_step_with_the_batch(js) -> list[str]:
    """A young batch's speed reaches as far as the batch, and no further.

    The speed is spread over the download's clock from the left edge to a
    front that keeps step with the download, so a short album spans the
    button by its end instead of stopping a few seconds' worth of pixels in.
    A couple of seconds and about a quarter of the batch in, the lower band
    (below the time-left words, above the floor line) is lit from the left
    edge up to just short of the download's front, and dark over the right
    third, past the front and clear of the speed words that dip into that
    band.
    """
    img, s, x0, w, lit = _band(js)
    if img.isNull():
        return ["the chart's pixels could not be read"]
    p = float(js("return chart().es.Pd"))
    out = []
    if not 0.15 < p < 0.5:
        return [f"the batch is {p:.2f} along, wanted about a quarter to check the chart's front"]
    if lit(x0 + 1 * s, x0 + 5 * s) == 0:
        out.append("a young batch's speed area is not at the chart's left edge")
    if lit(x0 + w * (p - 0.08), x0 + w * (p - 0.03)) == 0:
        out.append(f"the speed area stops short of the batch's front ({p:.2f} along), wanted it keeping step")
    # Past the front, clear of the speed words that dip into the band.
    beyond = lit(x0 + w * max(p + 0.12, 0.65), x0 + w - 1 * s)
    if beyond:
        out.append(f"the speed area runs past the batch's front ({beyond} lit pixels), wanted it to end there")
    return out


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
        got = js(
            "var T = chart().es.tr || [];" " return T.map(function (h) { return h.t + ':' + (h.sum / h.n) }).join('|')"
        )
        return [(float(t), float(m)) for t, m in (p.split(":") for p in str(got).split("|") if p)]

    failures = []
    a = bridge._enqueue("History One", "track", media_id="lab-history-1", artist="Lab", tracks=0)
    bridge._set_queue_status(a, "running")
    bridge._set_queue_progress(a, 0)
    settle(300)
    if not js("return chart().on"):
        print("the chart did not come on for a running row", file=sys.stderr)
        return 78

    # A fast stretch, then a slow one, the percent creeping all along.
    pct = 0
    speed(100.0)
    for _ in range(8):
        pct += 4
        bridge._set_queue_progress(a, pct)
        settle(250)
    failures.extend(_keeps_step_with_the_batch(js))
    speed(4.0)
    for _ in range(8):
        pct += 2
        bridge._set_queue_progress(a, pct)
        settle(250)
    speed(0.0)
    settle(300)
    failures.extend(_follows_the_speed(js, settle))
    H = history()
    fast = [i for i, (_t, m) in enumerate(H) if abs(m - 100.0) < 1e-6]
    slow = [i for i, (_t, m) in enumerate(H) if abs(m - 4.0) < 1e-6]
    if len(fast) < 5 or len(slow) < 5:
        failures.append(f"the history lost a stretch: {len(fast)} fast and {len(slow)} slow samples")
    elif max(fast) > min(slow):
        failures.append("the slow stretch does not follow the fast one in the history")
    steps = [b[0] - a_[0] for a_, b in pairwise(H)]
    if any(abs(s - 250) > 1e-3 for s in steps):
        failures.append(f"the trace is not a sample per 250 ms: {sorted(set(steps))[:5]}")
    if js("return chart().es.eta !== undefined || chart().es.hist !== undefined"):
        failures.append("the chart still keeps a time left")

    # A short batch's last stretch: a fast sprint to the end carries the
    # speed across the button, a few seconds of samples or not.
    speed(100.0)
    for pct in (75, 88, 100):
        bridge._set_queue_progress(a, pct)
        settle(250)
    settle(600)
    failures.extend(_reaches_the_end(js))
    speed(0.0)
    settle(1200)
    failures.extend(_holds_still_while_finishing(js, settle, bridge, a))
    # Settled, so the two frames differ by the words alone.
    failures.extend(_words_bottom_left(js, settle))

    # A long batch: the trace merges neighbors past two entries a pixel,
    # keeping the whole batch from its start and each stretch's mean speed.
    # The download is complete here, so the merge is forced by a sample
    # pushed with the clock running again.
    cap = int(js("return 2 * Math.max(8, Math.floor(chart().box()[2]))"))
    js(
        "var E = chart().es, T = E.tr, k = " + str(cap) + " - T.length;"
        " for (var i = k; i > 0; --i) T.unshift({ t: i * 0.01, sum: 50, n: 1 }); return T.length"
    )
    bridge._set_queue_progress(a, 90)
    settle(600)
    got = str(js("var E = chart().es, T = E.tr; return [T.length, T[0].t, T[0].sum / T[0].n].join('|')"))
    n, first_t, first_mb = (float(v) for v in got.split("|"))
    if n > cap // 2 + 4:
        failures.append(f"a long batch's trace holds {n:.0f} entries, wanted it merged to about half of {cap}")
    if first_t > 1 or abs(first_mb - 50) > 1e-6:
        failures.append(f"merging lost the batch's start: first entry {first_t} ms in reads {first_mb} Mb/s, wanted 50")
    failures.extend(f"QML error in the queue button: {e}" for e in errors[:5])

    for f in failures:
        print(f, file=sys.stderr)
    return _EXIT_REGRESSED if failures else _EXIT_OK


if __name__ == "__main__" and "--run-scenario" in sys.argv:
    sys.exit(_scenario())
