"""The QUEUE button's chart shows the last 12 s of speed as bars, its words clear of each other.

WHAT THIS FENCES OFF
--------------------
The button's chart is the batch's speed as bars, one per half second of the
last 12 s, scrolling left as time passes: the newest bar grows at the right
edge, a slow stretch stands lower than the fast one before it, and the bars
stand on the button's floor with no gap under them. It keeps a sample every
100 ms of that window and no more, so a long batch costs what a short one
does. Over it the batch percent sits on the left, its digits centered top
to bottom, and the speed in the bottom right corner, the unit smaller than
its number; at their widest (99%, where running tops out, and a
three-digit speed, or a speed in Gb/s) they still clear each other and
stay inside the button.

HOW THIS STAYS FIXED
--------------------
Boot the real Main.qml offscreen and drive one row through the bridge, the
speed set on the bridge's own property:

* 100 Mb/s for two seconds: the newest bars light the right edge, down to
  the floor, and nothing is lit over the left half yet;
* then 4 Mb/s for two seconds: the fast bars stand tall left of the low
  slow ones, and the samples are 100 ms apart;
* a minute of old samples pushed in is trimmed back to the window;
* the row at 100% (finish steps running) reads 99%, and with 999 Mb/s
  and again 1.5 Gb/s the percent's right edge stays left of the speed, the speed inside the button, and the percent's
  digits centered top to bottom.

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
 function qb(){ return walk(root.contentItem, 'queueButton') }
 function chart(){ return walk(qb(), 'queueButtonChart') }
"""


def test_the_queue_button_chart_keeps_its_history():
    run_scenario_file(__file__, "waves-queue-chart-test-", "the queue button's chart history")


def _lit(js, settle):
    """The chart's frame and a counter of lit pixels in a box-relative band."""
    import base64

    from PySide6.QtGui import QImage

    js("chart().requestPaint(); return 0")
    settle(60)
    url = str(js("return chart().toDataURL('image/png')"))
    img = QImage.fromData(base64.b64decode(url.split(",", 1)[1]))
    s = float(js("return chart().s"))
    x0, y0, w, h = (float(v) * s for v in str(js("return chart().box().join(',')")).split(","))

    def lit(fa: float, fb: float, ya: float, yb: float) -> int:
        return sum(
            1
            for x in range(int(x0 + w * fa), int(x0 + w * fb))
            for y in range(int(y0 + h * ya), int(y0 + h * yb))
            if img.pixelColor(x, y).alpha() > 24
        )

    return img, lit


def _newest_at_the_right(js, settle) -> list[str]:
    """Two seconds at speed: the right edge is lit, down to the floor."""
    img, lit = _lit(js, settle)
    if img.isNull():
        return ["the chart's pixels could not be read"]
    out = []
    if lit(0.9, 0.99, 0.3, 0.9) == 0:
        out.append("two seconds at 100 Mb/s and nothing lit at the chart's right edge")
    if lit(0.9, 0.99, 0.97, 1.0) == 0:
        out.append("the bars stop short of the button's floor")
    if lit(0.02, 0.5, 0.0, 1.0):
        out.append("two seconds in, bars are lit over the left half (wanted only the last seconds, at the right)")
    return out


def _fast_then_slow(js, settle) -> list[str]:
    """Fast then slow: tall bars over the fast stretch, low over the slow one."""
    img, lit = _lit(js, settle)
    if img.isNull():
        return ["the chart's pixels could not be read"]
    # Two seconds fast, then two slow, the newest at the right edge: the fast
    # bars stand about 2 to 4 s back (0.67 to 0.83 of the width), the slow
    # ones in the last 2 s.
    fast, slow = lit(0.7, 0.8, 0.15, 0.5), lit(0.92, 0.99, 0.15, 0.5)
    if fast == 0 or slow > fast // 4:
        return [f"the bars do not follow the speed: {fast} lit high over the fast stretch, {slow} over the slow"]
    return []


def _words_fit(js, settle, speed, bridge, a) -> list[str]:
    """At their widest the percent and the speed clear each other."""
    out = []
    bridge._set_queue_progress(a, 100)
    for mbps, label in ((999.0, "999 Mb/s"), (1500.0, "1.5 Gb/s")):
        speed(mbps)
        settle(400)
        got = str(
            js(
                "var b = qb(), p = walk(b, 'queueButtonPercent'), v = walk(b, 'queueButtonSpeed');"
                " var pp = p.mapToItem(b, 0, 0), vp = v.mapToItem(b, 0, 0);"
                " var mid = pp.y + p.baselineOffset + p.tight.y + p.tight.height / 2;"
                " return [p.text, pp.x + p.paintedWidth, vp.x, vp.x + v.width, vp.y + v.height, b.width, b.height, mid].join('|')"
            )
        )
        text, *nums = got.split("|")
        p_right, v_left, v_right, v_bot, bw, bh, mid = (float(x) for x in nums)
        if text != "99%":
            out.append(f"a row at 100% still running reads {text!r}, wanted 99%")
        if p_right + 1 > v_left:
            out.append(f"at 99% and {label} the percent (to {p_right:.1f}) runs into the speed (from {v_left:.1f})")
        if v_right > bw - 2 or v_bot > bh + 0.5:
            out.append(f"at {label} the speed leaves the button ({v_right:.1f} of {bw:.1f}, {v_bot:.1f} of {bh:.1f})")
        if abs(mid - bh / 2) > 1:
            out.append(f"the percent's digits sit {mid - bh / 2:+.1f} px off the button's middle")
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

    failures = []
    a = bridge._enqueue("History One", "track", media_id="lab-history-1", artist="Lab", tracks=0)
    bridge._set_queue_status(a, "running")
    bridge._set_queue_progress(a, 0)
    settle(500)
    if not js("return chart().on"):
        print("the chart did not come on for a running row", file=sys.stderr)
        return 78

    speed(100.0)
    pct = 0
    for _ in range(8):
        pct += 4
        bridge._set_queue_progress(a, pct)
        settle(250)
    failures.extend(_newest_at_the_right(js, settle))
    speed(4.0)
    for _ in range(8):
        pct += 2
        bridge._set_queue_progress(a, pct)
        settle(250)
    failures.extend(_fast_then_slow(js, settle))

    got = str(js("return (chart().es.hs || []).map(function (h) { return h.t }).join('|')"))
    ts = [float(t) for t in got.split("|") if t]
    steps = {round(b - a_, 3) for a_, b in pairwise(ts)}
    if len(ts) < 30 or steps != {100.0}:
        failures.append(f"the samples are not one per 100 ms: {len(ts)} samples, steps {sorted(steps)[:5]}")

    # A minute of old samples is trimmed back to the 12 s window.
    js(
        "var H = chart().es.hs, t0 = H[0].t, old = [];"
        " for (var i = 600; i > 0; --i) old.push({ t: t0 - i * 100, v: 50 });"
        " chart().es.hs = old.concat(H); chart().requestPaint(); return 0"
    )
    settle(100)
    got = str(js("var H = chart().es.hs; return [H.length, Date.now() - H[0].t].join('|')"))
    n, oldest = (float(v) for v in got.split("|"))
    if n > 130 or oldest > 12600:
        failures.append(f"old samples were kept: {n:.0f} of them, the oldest {oldest:.0f} ms back")

    failures.extend(_words_fit(js, settle, speed, bridge, a))
    failures.extend(f"QML error in the queue button: {e}" for e in errors[:5])

    for f in failures:
        print(f, file=sys.stderr)
    return _EXIT_REGRESSED if failures else _EXIT_OK


if __name__ == "__main__" and "--run-scenario" in sys.argv:
    sys.exit(_scenario())
