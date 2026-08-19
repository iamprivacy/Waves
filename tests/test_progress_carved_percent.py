"""The download button's progress bar fills the whole button, and its
percentage is carved into the bar on hover.

WHAT THIS FENCES OFF
--------------------
Progress pill lab, rounds 2 to 5 (2026-08-17). The running face of
DownloadButton (the SAME face on every download button: track rows, album
and playlist pages, artist pages, the Browse cards' pill) used to be a
four-row dot matrix stopping 12px in from either end, with a "NN%" readout
to its right in a slot reserved for "100%": a two-character hole beside a
short number for most of a run, and a bar that sat a pixel low on retina
displays (centred to a logical pixel; 19px in 28px is a 4.5px offset).

Now the matrix is a dense grid filling the button (round 6: SEVEN rows of
3px cells with 1px gaps, 1px inside the outline at every edge), centred to
the DEVICE pixel, with no readout beside it, and its outer cells fade toward
every edge on the shelf edge fades' curve (26px at the ends, 8px top and
bottom: DotMatrix.edgeFadeW / edgeFadeH) so the field sits in a soft frame
inside the outline. The percentage is spelled in the matrix's own cells (a
3x5 dot font on the middle five rows), always lit, on a PLATE knocked back
to near black, and only while the button (or, on a Browse card, the card) is
hovered: each cell dissolves from its bar state to its carve state on its
own random delay, driven by one eased value, so a leave mid-way reverses
from wherever it is.

Two things about the number were livetested and changed on 2026-08-17:

  * it was the INVERSE of the bar under it (a hole over a lit cell, a lit
    dot over an unlit one), which read at either end of a run but not at all
    while the fill edge crossed the digits. A stroke is now always 1.0 and
    the plate around it always 0.04, whatever the bar is doing. This
    polarity and not the other way up: a 3x5 glyph is mostly stroke, so dark
    digits on a lit plate read as a blob to be decoded from their counters
    (progress pill lab round 8);
  * the zone was a FIXED four glyphs with the word right-aligned in it, so
    that 9 -> 10 and 99 -> 100 moved nothing. That hung every ordinary
    reading two to four columns right of centre. The zone is now the word's
    own width, centred, for one, two or three digits alike.

HOW THIS STAYS FIXED
--------------------
The real Main.qml runs offscreen (a subprocess, like the sibling scenarios:
building the bridge installs process-global handlers). A search-result album
button is put into "running" at 37% and the scenario reads the matrix that
the Loader built:

  * seven rows, no readout sibling, its width the whole face, its y centred,
    the fade lengths set, an outermost column all but gone, the centre at
    full strength, and the fade the same at both ends and both edges;
  * at rest (wordHover false) every cell shows its bar state (times its own
    static edge fade);
  * hovered, the word cells spell exactly "37%" in the font, centred (the
    field left of the number and right of it match within a cell), each
    stroke is fully lit (1.0) and each plate cell around them is 0.04, both
    regardless of the bar under them;
  * part-way through the reveal the shared value is strictly between 0 and
    1 (it animates, it does not snap), and unhovered every cell is a bar
    cell again.

The Browse card's wiring (the card's hover, not just the pill's) is pinned
statically: the ArtCard's live download button must bind wordHover to the
card-wide HoverHandler.
"""

from __future__ import annotations

import math
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

QML_MAIN = Path(__file__).resolve().parent.parent / "tidaler" / "waves_ui" / "qml" / "Main.qml"

_EXIT_OK = 0
_EXIT_REGRESSED = 1
_EXIT_NO_QT = 77
_EXIT_PRECONDITION = 78

# 3x5 dot font, the same table the matrix draws from; the scenario compares
# the CELLS it finds against this rather than trusting the matrix's own idea
# of the glyphs.
_FONT = {
    "3": ["111", "001", "111", "001", "111"],
    "7": ["111", "001", "001", "001", "001"],
    "%": ["101", "001", "010", "100", "101"],
}


def _expected_rows(word: str) -> list[str]:
    """The word's own zone (glyph, gap, glyph, ...; 4 cells a glyph less the
    trailing gap), one string per row."""
    rows = []
    for r in range(5):
        row = ""
        for i, ch in enumerate(word):
            row += _FONT[ch][r]
            if i < len(word) - 1:
                row += "0"
        rows.append(row)
    return rows


def _run_in_subprocess(flag: str) -> tuple[int, str]:
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="waves-carved-percent-test-")
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


def test_running_face_is_a_full_width_bar_with_the_percent_carved_in_on_hover():
    code, tail = _run_in_subprocess("--run-carved-scenario")
    assert code == _EXIT_OK, f"the download button's progress face regressed:\n{tail}"


def test_browse_card_button_reveals_the_percent_for_the_whole_card():
    src = QML_MAIN.read_text()
    a = src.index("component ArtCard:")
    b = src.index("component ", a + 1)
    art_card = src[a:b]
    m = re.search(r"DownloadButton \{\s*id: acDl(.*?)\n\s*\}\n", art_card, re.S)
    assert m, "the ArtCard's live download button (id: acDl) moved"
    assert "wordHover: acWrapHover.hovered" in m.group(1), (
        "the card's download button must carve its percentage in for the CARD's hover "
        "(wordHover: acWrapHover.hovered), not only the pill's own"
    )


def test_ledger_and_scrub_matrices_do_not_carve():
    """Only the download face names a word; the other DotMatrix sites (the
    queue ledger's two-row bar, the preview scrubber, the compact running
    row) stay plain bars, and a word on fewer than five rows is ignored by
    the component itself; only the download face fades its edges."""
    src = QML_MAIN.read_text()
    a = src.index("component DotMatrix:")
    b = src.index("component ", a + 1)
    dm = src[a:b]
    assert 'rows >= 5 && word !== ""' in dm, "DotMatrix must gate the word on five rows or more"
    assert src.count("word: db.pct >= 0") == 1, "exactly one site (the download face) sets the word"
    assert src.count("edgeFadeW: 26") == 1, "exactly one site (the download face) fades its edges"


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
   out.push([c.col, c.rowTop, c.lit ? 1 : 0, c.wordCell ? 1 : 0, Math.round(c.opacity * 100) / 100,
             Math.round(c.barOpacity * c.edgeMul * 100) / 100, Math.round(c.edgeMul * 1000) / 1000,
             c.plateCell ? 1 : 0]);
  }
  return out;
 }
"""


def _run_carved_scenario() -> int:
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

    q("(function(){ var h = root.dlHolder('al-roll'); h.st = 'running'; h.pct = 37; return 1 })()")
    settle(900)

    def shape():
        return q(
            "(function(){" + _WALKERS + " var b = walkBtn(contentCol); if (!b) return 'shape:button';"
            " var m = walkMx(b); if (!m) return 'shape:matrix';"
            " var sibsWithText = 0; var sibs = m.parent.children;"
            " for (var i = 0; i < sibs.length; i++) if (sibs[i] !== m && sibs[i].text !== undefined) sibsWithText++;"
            " return JSON.stringify({rows: m.rows, dot: m.dot, gap: m.gap, width: m.width, faceWidth: m.parent.width,"
            "  y: m.y, faceH: m.parent.height, ih: m.implicitHeight, word: m.word, reveal: m.wordReveal,"
            "  sibsWithText: sibsWithText, cols: m.cols, hover: b.wordHover, dpr: Screen.devicePixelRatio,"
            "  fadeW: m.edgeFadeW, fadeH: m.edgeFadeH, wordRowTop: m.wordRowTop, faceInset: m.parent.parent.anchors.leftMargin});"
            "})()"
        )

    def read_cells():
        return q(
            "(function(){"
            + _WALKERS
            + " var b = walkBtn(contentCol); var m = walkMx(b); return JSON.stringify(cells(m)); })()"
        )

    import json

    rep = shape()
    if not str(rep).startswith("{"):
        print(f"could not locate the running face ({rep})", file=sys.stderr)
        return _EXIT_PRECONDITION
    s = json.loads(rep)
    failures: list[str] = []

    # --- geometry: seven rows, to the outline, no readout, centred to the device pixel
    if s["rows"] != 7 or s["dot"] != 3 or s["gap"] != 1:
        failures.append(
            f"  the running face is not the dense seven-row grid: rows {s['rows']} dot {s['dot']} gap {s['gap']}"
        )
    if s["faceInset"] != 1:
        failures.append(f"  the face sits {s['faceInset']}px in from the outline, not 1 (the grid should run to it)")
    if (s["fadeW"], s["fadeH"]) != (26, 8):
        failures.append(f"  the edge fades are {s['fadeW']}/{s['fadeH']}, wanted 26 (ends) / 8 (top and bottom)")
    if abs(s["width"] - s["faceWidth"]) > 0.5:
        failures.append(f"  the matrix is {s['width']}px in a {s['faceWidth']}px face: something sits beside it again")
    if s["sibsWithText"]:
        failures.append(f"  {s['sibsWithText']} text item(s) beside the matrix: the readout is back")
    true_y = (s["faceH"] - s["ih"]) / 2
    dpr = s["dpr"] or 1
    if abs(s["y"] - true_y) > 0.5 / dpr + 1e-6:
        failures.append(f"  matrix y {s['y']} is off the centre {true_y} by more than a device pixel (dpr {dpr})")
    if s["word"] != "37%":
        failures.append(f"  the word is {s['word']!r}, expected '37%'")

    # --- at rest every cell is a bar cell
    if s["hover"] or s["reveal"] != 0:
        failures.append(f"  the word is revealed at rest (hover {s['hover']}, reveal {s['reveal']})")
    rest_cells = json.loads(read_cells())
    for col, row, _lit, wc, op, bar, _mul, pl in rest_cells:
        if wc or pl or abs(op - bar) > 0.011:
            failures.append(
                f"  at rest cell ({col},{row}) is not its bar state: opacity {op} vs bar {bar}, "
                f"wordCell {wc}, plateCell {pl}"
            )
            break
    # --- the conveyor fade: outermost column all but gone, centre untouched,
    # symmetric end to end and top to bottom, the outer rows dimmed too
    mul = {(c, r): m for c, r, _l, _w, _o, _b, m, _p in rest_cells}
    cols_n, rows_n = s["cols"], s["rows"]
    mid_c, mid_r = cols_n // 2, rows_n // 2
    if mul[(0, mid_r)] > 0.1 or mul[(cols_n - 1, mid_r)] > 0.1:
        failures.append(f"  the outermost columns are not faded out: {mul[(0, mid_r)]} / {mul[(cols_n - 1, mid_r)]}")
    if mul[(mid_c, mid_r)] != 1:
        failures.append(f"  the centre cell is faded ({mul[(mid_c, mid_r)]}); the fade must stay at the edges")
    if not (mul[(mid_c, 0)] < 1 and abs(mul[(mid_c, 0)] - mul[(mid_c, rows_n - 1)]) < 0.001):
        failures.append(
            f"  top/bottom rows: {mul[(mid_c, 0)]} vs {mul[(mid_c, rows_n - 1)]} (dimmed and equal expected)"
        )
    for k in range(6):
        if abs(mul[(k, mid_r)] - mul[(cols_n - 1 - k, mid_r)]) > 0.001:
            failures.append(f"  the fade is not the same at both ends at column {k}")
            break
    if not all(mul[(k, mid_r)] < mul[(k + 1, mid_r)] for k in range(5)):
        failures.append("  the end fade does not rise monotonically toward the centre")

    # --- hovered: the reveal animates (not a snap) and lands at 1
    q("(function(){" + _WALKERS + " walkBtn(contentCol).wordHover = true; return 1 })()")
    settle(160)
    mid = json.loads(shape())["reveal"]
    if not (0 < mid < 1):
        failures.append(f"  160ms into the hover the reveal is {mid}: it should be mid-way, not snapped")
    settle(900)
    s2 = json.loads(shape())
    if s2["reveal"] != 1:
        failures.append(f"  hovered and settled, the reveal is {s2['reveal']}, not 1")

    # --- hovered: the word cells spell 37% and read the inverse of the bar
    cells = json.loads(read_cells())
    cols = s2["cols"]
    # The zone is the word's own width now, centred (JS Math.round, hence the
    # + 0.5 floor rather than Python's banker's rounding).
    zone_w = len("37%") * 4 - 1
    zone_start = math.floor((cols - zone_w) / 2 + 0.5)
    if abs(zone_start - (cols - zone_start - zone_w)) > 1:
        failures.append(
            f"  the number is not centred: {zone_start} column(s) of field to its left, "
            f"{cols - zone_start - zone_w} to its right"
        )
    got_rows = ["" for _ in range(5)]
    by_pos = {(c, r): (lit, wc, op, bar) for c, r, lit, wc, op, bar, _m, _p in cells}
    top = s2["wordRowTop"]
    if top != 1:
        failures.append(f"  the word sits on rows from {top}; in a seven-row grid it should be the middle five (1..5)")
    for r in range(5):
        for z in range(zone_w):
            wc = by_pos[(zone_start + z, top + r)][1]
            got_rows[r] += "1" if wc else "0"
    exp_rows = _expected_rows("37%")
    if got_rows != exp_rows:
        failures.append(
            "  the carved cells do not spell 37%:\n    got      "
            + "\n    got      ".join(got_rows)
            + "\n    expected "
            + "\n    expected ".join(exp_rows)
        )
    # A stroke is lit and its plate is knocked back, both to a FIXED value:
    # what the bar under them is doing must not enter into it.
    stroke_bad = [(c, r, lit, op) for c, r, lit, wc, op, _bar, m, _p in cells if wc and abs(op - m * 1.0) > 0.011]
    if stroke_bad:
        failures.append(
            f"  {len(stroke_bad)} stroke cell(s) are not fully lit, e.g. {stroke_bad[:3]} (col,row,lit,opacity)"
        )
    plate_bad = [(c, r, lit, op) for c, r, lit, _wc, op, _bar, m, pl in cells if pl and abs(op - m * 0.04) > 0.011]
    if plate_bad:
        failures.append(
            f"  {len(plate_bad)} plate cell(s) are not the fixed 0.04, e.g. {plate_bad[:3]} (col,row,lit,opacity)"
        )
    # the plate is exactly the glyph box plus a cell of margin: on a
    # seven-row face that is the full height, so every column of the zone
    # (and one either side) carries it top to bottom
    want_plate = {
        (c, r)
        for c in range(zone_start - 1, zone_start + zone_w + 1)
        for r in range(top - 1, top + 6)
        if 0 <= c < cols and 0 <= r < s2["rows"]
    }
    got_plate = {(c, r) for c, r, _l, wc, _o, _b, _m, pl in cells if pl or wc}
    if got_plate != want_plate:
        failures.append(
            f"  the plate is not the glyph box plus a cell of margin: "
            f"{len(want_plate - got_plate)} missing, {len(got_plate - want_plate)} extra"
        )
    outside_bad = [
        (c, r, op, bar) for c, r, _lit, wc, op, bar, _m, pl in cells if not wc and not pl and abs(op - bar) > 0.011
    ]
    if outside_bad:
        failures.append(f"  {len(outside_bad)} cell(s) outside the number moved on hover, e.g. {outside_bad[:3]}")

    # --- unhovered: back to a plain bar
    q("(function(){" + _WALKERS + " walkBtn(contentCol).wordHover = false; return 1 })()")
    settle(900)
    s3 = json.loads(shape())
    if s3["reveal"] != 0:
        failures.append(f"  unhovered and settled, the reveal is {s3['reveal']}, not 0")
    for col, row, _lit, wc, op, bar, _m, pl in json.loads(read_cells()):
        if wc or pl or abs(op - bar) > 0.011:
            failures.append(f"  after the hover cell ({col},{row}) is not its bar state: opacity {op} vs bar {bar}")
            break

    if failures:
        print("the download button's carved-percent face regressed:", file=sys.stderr)
        print("\n".join(failures), file=sys.stderr)
        return _EXIT_REGRESSED
    print(f"carved percent ok: {s2}", flush=True)
    return _EXIT_OK


if __name__ == "__main__":
    if "--run-carved-scenario" in sys.argv:
        raise SystemExit(_run_carved_scenario())
    raise SystemExit(_EXIT_PRECONDITION)
