"""A Browse card's running download bar keeps the pill's green outline.

WHAT THIS FENCES OFF
--------------------
On an ArtCard the live download bar is a DownloadButton loaded ``bare`` inside
a RollSwap, and the RollSwap's pill draws the outline. The bare button used to
keep painting its own opaque fill (accentCont) over the exact same geometry,
and Qt draws a Rectangle's border INSIDE its bounds, so that fill covered the
pill's border ring: the bar on every Browse card lost its green edge and kept
only a ragged sliver of it at the corners (v0.1.18 through v0.1.21).

Now a bare button paints nothing and the pill takes the button's ``fill``
through ``liveColor``: one rectangle draws fill and border, as the standalone
button does.

HOW THIS STAYS FIXED
--------------------
Drives the real Main.qml: a fake Browse landing with one playlist card, put into
a running download, then the card is grabbed to an image and its bar's left
edge is probed one pixel inside the pill. That pixel must be the outline green
(clearly brighter than the fill), and the property side is checked too: the
bare button's colour is fully transparent and the pill's colour is the button's
fill.

Runs in a SUBPROCESS like the other Main.qml scenarios.
"""

from __future__ import annotations

import os
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

_LANDING = (
    "{sections: [{title: 'The Hits', rowKind: 'cards', kind: 'playlists', items: ["
    "{kind: 'playlist', id: 'pl-outline', title: 'Top Hits', art: '', tracks: 50}]}],"
    " genres: [], moods: [], decades: []}"
)


def test_card_progress_bar_keeps_its_outline():
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    # Sandboxed: a bridge that finds the packaged app's config dir adopts its
    # settings and starts a real scan of the user's music library.
    sandbox = tempfile.mkdtemp(prefix="waves-card-outline-test-")
    env["XDG_CONFIG_HOME"] = sandbox
    env["HOME"] = sandbox
    proc = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), "--run-scenario"],
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
    assert proc.returncode == _EXIT_OK, f"the card's progress bar lost its outline again:\n{tail}"


_WALK = """
 function walk(it, pred){
  if (!it) return null;
  if (pred(it)) return it;
  for (var i = 0; i < it.children.length; i++) {
   var hit = walk(it.children[i].item || it.children[i], pred);
   if (hit) return hit;
  }
  return null;
 }
 function card(){ return walk(root.contentItem, function(it){
   return it.card !== undefined && it.card && it.card.id === 'pl-outline' && it.artSize !== undefined }) }
 function btn(){ return walk(card(), function(it){ return it.mediaId === 'pl-outline' && it.st !== undefined && it.bare !== undefined }) }
"""


def _run_scenario() -> int:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    try:
        from PySide6.QtCore import QEventLoop, QTimer, QUrl
        from PySide6.QtGui import QGuiApplication, QImage
        from PySide6.QtQml import QQmlApplicationEngine, QQmlEngine, QQmlExpression
        from PySide6.QtQuick import QQuickWindow
    except Exception as exc:  # pragma: no cover - environment guard
        print(f"Qt unavailable: {exc}", file=sys.stderr)
        return _EXIT_NO_QT

    from _qml_offline import PARK_LOGIN_QML, patch_offline

    patch_offline()

    app = QGuiApplication.instance() or QGuiApplication([])
    try:
        from tidaler.waves_ui.app import _load_mono
        from tidaler.waves_ui.backend import WavesBridge
    except Exception as exc:  # pragma: no cover - environment guard
        print(f"Qt platform/backend unavailable: {exc}", file=sys.stderr)
        return _EXIT_NO_QT

    bridge = WavesBridge(tidal=None)
    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty("waves", bridge)
    engine.rootContext().setContextProperty("monoFont", _load_mono())
    engine.rootContext().setContextProperty("uiFontFamily", app.font().family())
    engine.load(QUrl.fromLocalFile(str(QML_MAIN)))
    roots = engine.rootObjects()
    if not roots or not isinstance(roots[0], QQuickWindow):
        print("Main.qml failed to load as a window", file=sys.stderr)
        return _EXIT_PRECONDITION
    root = roots[0]

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
    q("root.browseStyle = 'art'")
    q("root.browseOpen = true")
    q(f"root.applyBrowseLanding({_LANDING})")
    settle(1200)
    q("root.browseBuilding = false")
    q("(function(){ var h = root.dlHolder('pl-outline'); h.st = 'running'; h.pct = 42; return 1 })()")
    settle(900)  # past the roll (230ms) and the colour fade (420ms)

    shape = q(
        "(function(){" + _WALK + " var b = btn(); if (!b) return 'no button';"
        " return [b.st, b.width, b.height, b.color.a, b.parent.parent.color.a, b.parent.parent.color.r,"
        " b.parent.parent.color.g, b.parent.parent.color.b, b.fill.r, b.fill.g, b.fill.b].join(',') })()"
    )
    if not shape or shape == "no button":
        print("could not find the card's running download button", file=sys.stderr)
        return _EXIT_PRECONDITION
    st, _bw, _bh, ba, _pa, pr, pg, pb, fr, fg, fb = shape.split(",")
    if st != "running":
        print(f"button is not running: {st}", file=sys.stderr)
        return _EXIT_PRECONDITION
    failures: list[str] = []
    if float(ba) > 0.001:
        failures.append(f"a bare download button still paints its own fill (alpha {ba})")
    if abs(float(pr) - float(fr)) > 0.02 or abs(float(pg) - float(fg)) > 0.02 or abs(float(pb) - float(fb)) > 0.02:
        failures.append(f"the pill's colour {pr},{pg},{pb} is not the button's fill {fr},{fg},{fb}")

    # Pixel probe: grab the card, look at the bar's left edge, one pixel in.
    png = os.path.join(tempfile.mkdtemp(prefix="waves-card-outline-"), "card.png")
    q(
        "(function(){" + _WALK + " var c = card(); var b = btn();"
        " var p = b.mapToItem(c, 0, b.height / 2);"
        " c.grabToImage(function(r){ r.saveToFile('" + png.replace("\\", "/") + "') });"
        " return p.x + ',' + p.y })()"
    )
    for _ in range(50):
        settle(100)
        if os.path.exists(png):
            break
    if not os.path.exists(png):
        print("grabToImage produced no file", file=sys.stderr)
        return _EXIT_PRECONDITION
    edge = q(
        "(function(){" + _WALK + " var c = card(); var b = btn();"
        " var p = b.mapToItem(c, 0, b.height / 2); return p.x + ',' + p.y })()"
    )
    ex, ey = (float(v) for v in edge.split(","))
    img = QImage(png)
    if img.isNull():
        print("grabbed image unreadable", file=sys.stderr)
        return _EXIT_PRECONDITION
    edge_px = img.pixelColor(int(ex) + 1, int(ey))
    # 3px in is pure fill: the outline is 1px and the matrix starts 6px inside
    # the button (it ran 12px in until the progress pill lab of 2026-08-17).
    mid_px = img.pixelColor(int(ex) + 3, int(ey))
    # The outline is accentDim (#22a64a): its green channel is far above the
    # fill's (#06210f). Before the fix this pixel WAS the fill.
    if edge_px.green() < 100:
        failures.append(f"the bar's edge pixel is {edge_px.name()}: fill, not outline")
    if edge_px.green() <= mid_px.green() + 60:
        failures.append(f"edge {edge_px.name()} is not clearly brighter than the fill {mid_px.name()}")

    if failures:
        for f in failures:
            print(f, file=sys.stderr)
        return _EXIT_REGRESSED
    return _EXIT_OK


if __name__ == "__main__" and "--run-scenario" in sys.argv:
    sys.exit(_run_scenario())
