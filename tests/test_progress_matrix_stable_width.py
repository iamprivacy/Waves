"""A running download's dot matrix must not shed columns when the % readout grows.

WHAT THIS FENCES OFF
--------------------
The download button draws ``[dot matrix][NN%]``: the matrix is anchored to the
left edge of the percentage Text, so the Text's width IS the matrix's right
edge. That Text was unbounded, so every digit the readout gained (…→ 5%, 9% →
10%, 99% → 100%) narrowed the matrix beside it.

DotMatrix answers a width change by holding its old column count for 300ms (its
``_settledWidth``, which exists so dragging the queue drawer's edge doesn't
rebuild every dot per mouse move). For those 300ms the matrix laid its last
columns out past its own new width, so a download visibly LOST its last two
columns of dots exactly as it reached 100%, then popped them back when the
settle timer fired. Measured on a 25fps screen capture: the lit dot area fell
from 4189px to 3901px for ten frames at the finish.

The fix is to reserve the readout's widest value ("100%") so the matrix never
resizes mid-run at all.

HOW THIS STAYS FIXED
--------------------
This drives the real Main.qml: put an album's DownloadButton into a running
download and step its percentage through both digit boundaries, reading the
matrix geometry between steps WITHOUT waiting out the settle interval. Two
things are asserted at every step:

* the matrix width never moves (the reservation itself), and
* the matrix's laid-out extent (``cols * (dot + gap) - gap``) still fits inside
  its width, which is the defect the user could see.

The second is the backstop: it fails for ANY route back to a matrix laid out
wider than the item drawing it, not just an unbounded readout.

THE START-UP TWIN (the Browse card scenario)
--------------------------------------------
On a Browse card the same button widens from its queued face to the full
strip the moment the run starts, and its matrix is built by a Loader in that
same cascade, while the button is still the narrow width. DotMatrix used to
latch its settled width in Component.onCompleted, i.e. mid-cascade, so the
bar opened two columns too wide for the settle interval and then shed them
(a livetest report: "an extra set of blocks that quickly disappear"). The
settle binding now rides through the creating turn. The card scenario reads
the matrix 60ms into the run, inside the settle, and requires its columns
to fit from the first frame.

Runs in a SUBPROCESS like the other Main.qml scenarios: building the bridge
installs process-global handlers that must not leak into the suite.
"""

from __future__ import annotations

import json
import os
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

_ALBUM = json.dumps(
    {
        "id": "al-roll",
        "title": "Rolling Album",
        "artist": "Artist R",
        "artist_id": "r1",
        "art": "",
        "year": "2026",
        "date": "2026-01-01",
        "tracks": 10,
        "quality": "LOSSLESS",
        "popularity": 50,
    }
)

# The steps that used to resize the bar: the first real percent (the "…"
# placeholder is one character), the 9 -> 10 boundary, and the 99 -> 100 one
# the capture caught.
_STEPS = (5, 9, 10, 42, 99, 100)

# Read between steps, well inside DotMatrix's 300ms settle: waiting it out is
# exactly what hid the bug from everyone who looked at a still.
_STEP_SETTLE_MS = 60


def _run_in_subprocess(flag: str) -> tuple[int, str]:
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    # Sandboxed: this scenario builds a REAL WavesBridge, and a bridge that
    # finds the packaged app's config dir adopts its settings, writes its log,
    # and starts a real scan of the user's music library.
    env["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="waves-dot-matrix-width-test-")
    proc = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), flag],
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    tail = "\n".join((proc.stdout + proc.stderr).strip().splitlines()[-12:])
    if proc.returncode == _EXIT_NO_QT:
        pytest.skip("PySide6 / offscreen Qt unavailable")
    if proc.returncode == _EXIT_PRECONDITION:
        pytest.skip(f"could not set up the scenario in this environment:\n{tail}")
    return proc.returncode, tail


def test_progress_matrix_keeps_its_columns_as_the_readout_grows():
    code, tail = _run_in_subprocess("--run-scenario")
    assert code == _EXIT_OK, f"the progress matrix resizes mid-download again:\n{tail}"


def test_browse_card_matrix_fits_from_its_first_frame():
    code, tail = _run_in_subprocess("--run-card-scenario")
    assert code == _EXIT_OK, f"the Browse card's progress bar opens with stale columns again:\n{tail}"


# The button for our album, then the DotMatrix inside it. Both are found by
# property signature rather than by child index, so neither walk breaks when a
# sibling is added beside them.
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
  if (it.litCount !== undefined && it.cols !== undefined) return it;
  for (var i = 0; i < it.children.length; i++) {
   var hit = walkMx(it.children[i].item || it.children[i]);
   if (hit) return hit;
  }
  return null;
 }
"""


def _boot():
    """Boot the real Main.qml offscreen; returns (root, q, settle, bridge), or
    an exit code when the environment cannot host it."""
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    try:
        from PySide6.QtCore import QEventLoop, QTimer, QUrl
        from PySide6.QtGui import QGuiApplication
        from PySide6.QtQml import QQmlApplicationEngine, QQmlEngine, QQmlExpression
        from PySide6.QtQuick import QQuickWindow
    except Exception as exc:  # pragma: no cover - environment guard
        print(f"Qt unavailable: {exc}", file=sys.stderr)
        return _EXIT_NO_QT

    from _qml_offline import PARK_LOGIN_QML, patch_offline

    patch_offline()  # BEFORE the bridge: its __init__ fires the sign-in check

    app = QGuiApplication.instance() or QGuiApplication([])
    try:
        from waves.waves_ui.app import _load_mono
        from waves.waves_ui.backend import WavesBridge
    except Exception as exc:  # pragma: no cover - environment guard
        print(f"Qt platform/backend unavailable: {exc}", file=sys.stderr)
        return _EXIT_NO_QT

    # No library scan and no browse fetch: neither is what these scenarios
    # are about, and both reach outside the sandbox.
    WavesBridge._library_root = lambda self: ""  # type: ignore[method-assign]
    WavesBridge.loadBrowse = lambda self, *a: None  # type: ignore[method-assign]

    # Bridge BEFORE engine: see the sibling harnesses.
    bridge = WavesBridge(tidal=None)
    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty("waves", bridge)
    engine.rootContext().setContextProperty("monoFont", _load_mono())
    engine.rootContext().setContextProperty("uiFontFamily", app.font().family())
    engine.load(QUrl.fromLocalFile(str(QML_MAIN)))
    roots = engine.rootObjects()
    if not roots:
        print("Main.qml failed to load", file=sys.stderr)
        return _EXIT_PRECONDITION
    root = roots[0]
    if not isinstance(root, QQuickWindow):
        print("root object is not a window", file=sys.stderr)
        return _EXIT_PRECONDITION

    def q(expr: str):
        ctx = QQmlEngine.contextForObject(root)
        e = QQmlExpression(ctx, root, expr)
        r = e.evaluate()
        if e.hasError():
            raise RuntimeError(e.error().toString())
        return r[0] if isinstance(r, tuple) else r

    def settle(ms: int) -> None:
        loop = QEventLoop()
        QTimer.singleShot(ms, loop.quit)
        loop.exec()

    root.resize(1280, 900)
    root.show()
    settle(300)
    q("bootOverlay.done = true")
    q("bootContentShown = 1")
    q(PARK_LOGIN_QML)
    # The engine owns the tree; keep it referenced for the scenario's life.
    _boot.engine = engine  # type: ignore[attr-defined]
    return root, q, settle, bridge


# The Browse card's button and its matrix: the button is the one DownloadButton
# in the tree whose mediaId is the seeded playlist's.
_CARD_WALKERS = """
 function walkBtn(it){
  if (!it) return null;
  if (it.mediaId === 'pl-roll' && it.queuedLabel !== undefined) return it;
  for (var i = 0; i < it.children.length; i++) {
   var hit = walkBtn(it.children[i].item || it.children[i]);
   if (hit) return hit;
  }
  return null;
 }
 function walkMx(it){
  if (!it) return null;
  if (it.litCount !== undefined && it.cols !== undefined) return it;
  for (var i = 0; i < it.children.length; i++) {
   var hit = walkMx(it.children[i].item || it.children[i]);
   if (hit) return hit;
  }
  return null;
 }
"""


def _run_card_scenario() -> int:
    booted = _boot()
    if isinstance(booted, int):
        return booted
    _root, q, settle, bridge = booted

    bridge._logged_in = True
    bridge.loggedInChanged.emit()
    q('root.browseStyle = "art"')
    q('root.browsePageKey = "lab"')
    q(
        'root.browsePage = { key: "lab", title: "Lab shelf", sections: ['
        '{ rowKind: "cards", title: "PLAYLISTS", items: ['
        '{ kind: "playlist", id: "pl-roll", title: "Rolling Playlist", artist: "", year: "",'
        ' tracks: 40, art: "", artists: [], quality: "LOSSLESS" }'
        '], more: "", data: "", total: 0, offset: 0, modType: "" }] }'
    )
    settle(900)
    # The strip only rises under the cursor; pin the card's riser open the way
    # the browse-card scenarios do (its `on` gate is the only thing changed).
    lit = int(
        q(
            "(function(){var n=0;function w(o){if(!o)return;"
            "if(o.acPvSt!==undefined){o.on=true;n++}"
            "var c=o.children;if(c)for(var i=0;i<c.length;++i)w(c[i])}"
            "w(browseDrill);return n})()"
        )
    )
    if lit < 1:
        print("no ArtCard riser found on the seeded shelf", file=sys.stderr)
        return _EXIT_PRECONDITION
    settle(500)

    def measure() -> str:
        return str(
            q(
                "(function(){" + _CARD_WALKERS + " var b = walkBtn(browseDrill); if (!b) return 'shape:button';"
                " var m = walkMx(b); if (!m) return 'shape:matrix';"
                " function r2(v){ return Math.round(v * 100) / 100 }"
                " return 'mx:' + [r2(m.width), m.cols, m.dot, m.gap, r2(m._settledWidth)].join(',');"
                "})()"
            )
            or ""
        )

    # The real sequence: the click acknowledges as queued (the narrow face),
    # then the worker flips it to running with 0% (see _download).
    q("(function(){ var h = root.dlHolder('pl-roll'); h.st = 'queued'; h.pct = -1; return 1 })()")
    settle(500)
    q("(function(){ var h = root.dlHolder('pl-roll'); h.pct = 0; h.st = 'running'; return 1 })()")
    # Read INSIDE DotMatrix's 300ms settle: a matrix that latched a
    # pre-layout width is only visibly wrong until the settle timer fires.
    settle(_STEP_SETTLE_MS)
    first = measure()
    settle(600)
    later = measure()

    for rep in (first, later):
        if not rep.startswith("mx:"):
            print(f"could not locate the card's running dot matrix ({rep})", file=sys.stderr)
            return _EXIT_PRECONDITION
    parsed = {}
    for tag, rep in (("first", first), ("settled", later)):
        width, cols, dot, gap, settled = (float(v) for v in rep[len("mx:") :].split(","))
        parsed[tag] = (width, int(cols), dot, gap, settled)

    failures: list[str] = []
    width, cols, dot, gap, settled = parsed["first"]
    extent = cols * (dot + gap) - gap
    if extent > width + 0.5:
        failures.append(
            f"  {_STEP_SETTLE_MS}ms into the run: {cols} columns need {round(extent, 2)}px but the "
            f"matrix is {width}px wide (settled width {settled}px), so the bar opens with "
            f"{max(1, int((extent - width) / (dot + gap)) + 1)} stale column(s)"
        )
    if abs(settled - width) > 0.5:
        failures.append(f"  {_STEP_SETTLE_MS}ms into the run: settled width {settled}px lags the real {width}px")
    if parsed["settled"][1] != cols:
        failures.append(f"  the column count moved after the settle: {cols} -> {parsed['settled'][1]}")
    if failures:
        print("the Browse card's progress bar opens with stale columns:", file=sys.stderr)
        print("\n".join(failures), file=sys.stderr)
        print(f"first {first}\nsettled {later}", file=sys.stderr)
        return _EXIT_REGRESSED
    print(f"card matrix fit from the first frame: {first}", flush=True)
    return _EXIT_OK


def _run_scenario() -> int:
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

    def measure() -> str:
        return str(
            q(
                "(function(){" + _WALKERS + " var b = walkBtn(contentCol); if (!b) return 'shape:button';"
                " var m = walkMx(b); if (!m) return 'shape:matrix';"
                " var readout = 0;"
                " var sibs = m.parent ? m.parent.children : [];"
                " for (var i = 0; i < sibs.length; i++)"
                "  if (sibs[i] !== m && sibs[i].width !== undefined) readout = sibs[i].width;"
                " function r2(v){ return Math.round(v * 100) / 100 }"
                " return 'mx:' + [r2(m.width), m.cols, m.dot, m.gap, m.fillTotal, m.litCount, r2(readout)].join(',');"
                "})()"
            )
            or ""
        )

    # Start the download and let the very first frame settle completely, so the
    # baseline is the matrix at rest, not one mid-roll.
    q("(function(){ var h = root.dlHolder('al-roll'); h.st = 'running'; h.pct = -1; return 1 })()")
    settle(700)

    readings: list[tuple[int, str]] = []
    for pct in _STEPS:
        q(f"(function(){{ root.dlHolder('al-roll').pct = {pct}; return 1 }})()")
        # The width is read INSIDE the step (that is the defect this guards);
        # the lit count at 100% is read only once the holder's jump ramp (a
        # forward leap is filled over up to 1.5s, not snapped) has landed.
        settle(_STEP_SETTLE_MS if pct < 100 else 1700)
        readings.append((pct, measure()))

    bad = [(pct, rep) for pct, rep in readings if not rep.startswith("mx:")]
    if bad:
        print(f"could not locate the running download's dot matrix ({bad[0][1]})", file=sys.stderr)
        return _EXIT_PRECONDITION

    parsed = []
    for pct, rep in readings:
        width, cols, dot, gap, total, lit, readout = (float(v) for v in rep[len("mx:") :].split(","))
        parsed.append((pct, width, int(cols), dot, gap, int(total), int(lit), readout))

    failures: list[str] = []
    base_width = parsed[0][1]
    for pct, width, cols, dot, gap, total, lit, readout in parsed:
        # The reservation itself: the readout's digit count must not move the
        # matrix's right edge.
        if abs(width - base_width) > 0.5:
            failures.append(
                f"  at {pct}%: matrix width {width}px, was {base_width}px at 5% "
                f"(the readout is {readout}px wide, so it is still reflowing)"
            )
        # The visible defect: the columns laid out must fit the item drawing them.
        extent = cols * (dot + gap) - gap
        if extent > width + 0.5:
            failures.append(
                f"  at {pct}%: {cols} columns need {round(extent, 2)}px but the matrix "
                f"is {width}px wide, so its last "
                f"{max(1, int((extent - width) / (dot + gap)) + 1)} column(s) draw past the edge"
            )
        # And a finished bar reads full.
        if pct == 100 and lit != total:
            failures.append(f"  at 100%: {lit} of {total} fill dots lit (pads excluded)")

    if failures:
        print("the dot matrix moves while the percentage readout grows:", file=sys.stderr)
        print("\n".join(failures), file=sys.stderr)
        print("readings (pct, width, cols, dot, gap, total, lit, readout):", file=sys.stderr)
        for row in parsed:
            print(f"  {row}", file=sys.stderr)
        return _EXIT_REGRESSED

    print(f"matrix held {base_width}px across {[p for p, *_ in parsed]}", flush=True)
    return _EXIT_OK


if __name__ == "__main__":
    if "--run-scenario" in sys.argv:
        raise SystemExit(_run_scenario())
    if "--run-card-scenario" in sys.argv:
        raise SystemExit(_run_card_scenario())
    raise SystemExit("run this file through pytest")
