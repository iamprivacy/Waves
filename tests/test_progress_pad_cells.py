"""The download face's opening percent is visible: pad cells at the edges.

WHAT THIS FENCES OFF
--------------------
The Browse card's running face runs its dot matrix to the button outline and
fades the outer cells to zero (26px at the ends, 8px top and bottom), so the
field sits in a soft frame. The bar fills column-major from the bottom left,
which put the first several percent of every run entirely inside that dead
zone: on a 56-track playlist at 9 tracks the pill read as an empty bar
(livetest report, 2026-08-17). The fade was meant to soften the ends, not to
mask the progress.

The fix keeps the fade and the geometry verbatim and makes the outermost
cells PADS (progress pill lab round 7, design V): the two outer columns at
either end draw as field but take no part in the fill, so the fade spends
itself on cells that carry no progress and the first real block lights two
columns in, where the fade is at 36% (the next at 60%). Rows are NOT padded
and no more columns are: four pad columns plus pad rows (design Q) shipped and
was reverted the same day because the fill visibly started several blocks in
from the start of the bar. A pad mirrors its nearest real neighbour, so a full
bar's ends still light and fade exactly as before, and a pad never carries the
pulse.

HOW THIS STAYS FIXED
--------------------
Static: only the download face names pads (padCols 2, no pad rows, mirror), and
the component itself sizes the fill and the pulse by the pad-free area. Live:
the real Main.qml is booted offscreen, a running Browse card is seeded, and at
1% the first lit real cell must sit at column 2 (not further in) with an edge
multiplier above 0.3, the fill must reach the bottom row, no pad may pulse, at
100% every cell must be lit (pads mirroring), and the pads at rest must be
indistinguishable from field.

Runs in a SUBPROCESS like the other Main.qml scenarios (shares the sibling's
``_boot``).
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

QML_MAIN = Path(__file__).resolve().parent.parent / "waves" / "waves_ui" / "qml" / "Main.qml"

_EXIT_OK = 0
_EXIT_REGRESSED = 1
_EXIT_NO_QT = 77
_EXIT_PRECONDITION = 78


def _run_in_subprocess(flag: str) -> tuple[int, str]:
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="waves-pad-cells-test-")
    proc = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), flag],
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    tail = "\n".join((proc.stdout + proc.stderr).strip().splitlines()[-16:])
    if proc.returncode == _EXIT_NO_QT:
        pytest.skip("PySide6 / offscreen Qt unavailable")
    if proc.returncode == _EXIT_PRECONDITION:
        pytest.skip(f"could not set up the scenario in this environment:\n{tail}")
    return proc.returncode, tail


def test_only_the_download_face_pads_its_edges():
    src = QML_MAIN.read_text()
    a = src.index("component DotMatrix:")
    dm = src[a : src.index("component ", a + 1)]
    assert "property int padCols: 0" in dm and "property int padRows: 0" in dm, "DotMatrix lost its pad knobs"
    assert "readonly property int fillTotal: fillRows * fillCols" in dm, "the fill must be sized by the pad-free area"
    assert re.search(r"litCount:.*\* fillTotal\)", dm), "litCount must count over fillTotal, not the whole grid"
    assert "pulsing: !pad &&" in dm, "a pad must never carry the pulse"
    assert (
        src.count("padCols: 2; mirrorPads: true") == 1
    ), "exactly one site (the download face) pads its edges, with design V's values"
    face = src[src.index('objectName: "dbMatrix"') :]
    face = face[: face.index("}\n")]
    assert "padCols: 2; mirrorPads: true" in face, "the pads belong on the download face's matrix"
    assert "padRows" not in face, "the download face pads no rows (design Q's pad rows were reverted)"
    assert "edgeFadeW: 26; edgeFadeH: 8" in face, "the fade itself is unchanged (26 / 8)"


def test_the_first_percent_lights_where_the_fade_has_let_go():
    code, tail = _run_in_subprocess("--run-pad-scenario")
    assert code == _EXIT_OK, f"the download face hides its opening percent again:\n{tail}"


# ---------------------------------------------------------------------------
# The scenario itself
# ---------------------------------------------------------------------------

_WALKERS = """
 function walkBtn(it){
  if (!it) return null;
  if (it.mediaId === 'al-roll' && it.st !== undefined) return it;
  for (var i = 0; i < it.children.length; i++) {
   var hit = walkBtn(it.children[i].item || it.children[i]);
   if (hit) return hit;
  }
  return null;
 }
 function walkMx(it){
  if (!it) return null;
  if (it.objectName === 'dbMatrix') return it;
  for (var i = 0; i < it.children.length; i++) {
   var hit = walkMx(it.children[i].item || it.children[i]);
   if (hit) return hit;
  }
  return null;
 }
 function cells(m){
  var out = [];
  for (var i = 0; i < m.children.length; i++) {
   var c = m.children[i];
   if (c.barOpacity === undefined) continue;
   out.push([c.col, c.rowTop, c.pad ? 1 : 0, c.lit ? 1 : 0, c.pulsing ? 1 : 0,
             Math.round(c.opacity * 1000) / 1000, Math.round(c.edgeMul * 1000) / 1000]);
  }
  return out;
 }
"""


def _run_pad_scenario() -> int:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from test_progress_matrix_stable_width import _ALBUM, _boot

    booted = _boot()
    if isinstance(booted, int):
        return booted
    _root, q, settle, _bridge = booted
    q("root.openSearch()")
    q("albumsModel.clear()")
    q(f"albumsModel.append({_ALBUM})")
    q("root.searchReveal = 1")
    q("root.searchBuilding = false")
    q("root.searchAlbumsExpanded = true")
    settle(700)

    def set_pct(pct: float) -> None:
        # Long enough for the holder's jump ramp to land: a leap of the width
        # of the bar is filled over up to 1.5s (see test_progress_jump_ramp).
        q(f"(function(){{ var h = root.dlHolder('al-roll'); h.st = 'running'; h.pct = {pct}; return 1 }})()")
        settle(1800)

    def shape():
        return q(
            "(function(){" + _WALKERS + " var b = walkBtn(contentCol); if (!b) return 'shape:button';"
            " var m = walkMx(b); if (!m) return 'shape:matrix';"
            " return JSON.stringify({rows: m.rows, cols: m.cols, padCols: m.padCols, padRows: m.padRows,"
            "  mirror: m.mirrorPads, fillCols: m.fillCols, fillRows: m.fillRows, fillTotal: m.fillTotal,"
            "  total: m.total, litCount: m.litCount, fadeW: m.edgeFadeW, fadeH: m.edgeFadeH, pulse: m.pulse});"
            "})()"
        )

    def read_cells():
        return json.loads(
            q(
                "(function(){"
                + _WALKERS
                + " var b = walkBtn(contentCol); var m = walkMx(b); return JSON.stringify(cells(m)); })()"
            )
        )

    set_pct(1)
    rep = shape()
    if not str(rep).startswith("{"):
        print(f"could not locate the running face ({rep})", file=sys.stderr)
        return _EXIT_PRECONDITION
    s = json.loads(rep)
    failures: list[str] = []

    if (s["padCols"], s["padRows"], s["mirror"]) != (2, 0, True):
        failures.append(f"  the face's pads are {s['padCols']}/{s['padRows']}/{s['mirror']}, wanted 2 / 0 / mirroring")
    if (s["fadeW"], s["fadeH"]) != (26, 8):
        failures.append(f"  the fade moved to {s['fadeW']}/{s['fadeH']}: the pads must not change it")
    if s["fillCols"] != s["cols"] - 4 or s["fillRows"] != s["rows"]:
        failures.append(f"  the fill area is {s['fillCols']}x{s['fillRows']} in a {s['cols']}x{s['rows']} grid")
    if s["fillTotal"] >= s["total"] or s["litCount"] != round(s["fillTotal"] / 100):
        failures.append(f"  1% lights {s['litCount']} of a fill of {s['fillTotal']} (grid {s['total']})")

    cells = read_cells()
    by = {(c, r): (pad, lit, pul, op, mul) for c, r, pad, lit, pul, op, mul in cells}
    real_lit = sorted((c, r) for (c, r), (pad, lit, _p, _o, _m) in by.items() if lit and not pad)
    if not real_lit or real_lit[0][0] != 2:
        failures.append(f"  at 1% the first lit real cell is at {real_lit[:1]}, expected column 2 (past two pads)")
    # Column 2 sits at 36% of the end fade; the bottom row (where the fill
    # starts) is at 13% of the top/bottom fade, so read the column's best
    # cell: it must be past the dead zone (the shipped-before column 0 was
    # at 0.04) and the fill must reach the bottom row (no pad rows).
    bottom = s["rows"] - 1
    if real_lit and max(by[k][4] for k in real_lit) < 0.3:
        failures.append(
            f"  at 1% the lit column sits under the fade: multipliers {[by[k][4] for k in real_lit]} (want one >= 0.3)"
        )
    if real_lit and not any(r == bottom for _c, r in real_lit):
        failures.append("  the fill does not reach the bottom row: rows are not to be padded")
    if any(pul for (_c, _r), (pad, _l, pul, _o, _m) in by.items() if pad):
        failures.append("  a pad cell is pulsing")
    if not any(pul for (_c, _r), (pad, _l, pul, _o, _m) in by.items() if not pad):
        failures.append("  no real cell pulses at 1% while running")
    # the mirroring pads beside the lit column copy it (row-wise), the far
    # pads and the rest of the field are dark
    lit_rows = {r for _c, r in real_lit}
    for c in range(2):
        for r in range(0, bottom + 1):
            want = r in lit_rows
            if bool(by[(c, r)][1]) != want:
                failures.append(f"  pad ({c},{r}) lit={by[(c, r)][1]}, expected {want} (mirror of column 2)")
                break
    far = [k for k in by if k[0] >= s["cols"] - 2]
    if any(by[k][1] for k in far):
        failures.append("  the right-hand pads are lit at 1%")

    # --- 100%: everything lit, pads included, and nothing pulses
    set_pct(100)
    s2 = json.loads(shape())
    cells = read_cells()
    if s2["litCount"] != s2["fillTotal"]:
        failures.append(f"  at 100% litCount {s2['litCount']} != fillTotal {s2['fillTotal']}")
    unlit = [(c, r) for c, r, _pad, lit, _pul, _op, _mul in cells if not lit]
    if unlit:
        failures.append(f"  at 100% {len(unlit)} cell(s) are unlit (the pads should mirror the full fill): {unlit[:4]}")
    if any(pul for _c, _r, _pad, _lit, pul, _op, _mul in cells):
        failures.append("  something pulses at 100%")

    # --- 0%: pads read exactly as field (opacity = 0.16 * edge multiplier)
    set_pct(0)
    cells = read_cells()
    off = [
        (c, r, op, mul)
        for c, r, pad, lit, _pul, op, mul in cells
        if pad and (lit or abs(op - round(0.16 * mul, 3)) > 0.011)
    ]
    if off:
        failures.append(f"  at 0% {len(off)} pad(s) are not plain field, e.g. {off[:3]} (col,row,opacity,edgeMul)")

    if failures:
        print("the download face's pad cells regressed:", file=sys.stderr)
        print("\n".join(failures), file=sys.stderr)
        return _EXIT_REGRESSED
    print(f"pad cells ok: {s}", flush=True)
    return _EXIT_OK


if __name__ == "__main__":
    if "--run-pad-scenario" in sys.argv:
        raise SystemExit(_run_pad_scenario())
    raise SystemExit(_EXIT_PRECONDITION)
