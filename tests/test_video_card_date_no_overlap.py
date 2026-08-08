"""A video card's release date must never run under its download button.

The card's meta column already reserves the download button's width, but the
artist/date row inside it anchored the date to the right edge of an
unbounded ArtistLinks row: a long credit line (several artists) pushed the
dot and the date past the column and under the button. The fix reserves the
date's (short, fixed) width first and clips the artist list to what remains.
This drives the real Main.qml: build a video result with a long credit line
and assert the date text stays clear of the download button.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

QML_MAIN = Path(__file__).resolve().parent.parent / "tidaler" / "waves_ui" / "qml" / "Main.qml"

_EXIT_OK = 0
_EXIT_FAIL = 1
_EXIT_NO_QT = 3
_EXIT_PRECONDITION = 4

_VIDEO = (
    '{"id":"v1","title":"Let The Good Times Roll","artist":"Electric Callboy","artists":[],'
    '"art":"","art_big":"","duration":"3:37","explicit":false,"added":"",'
    '"date":"2026-06-06","quality":"1080p"}'
)

# Enough combined name width to overflow a grid cell several times over.
_ARTISTS = (
    "[{id:'a1',name:'Electric Callboy'},{id:'a2',name:'The Offspring'},"
    "{id:'a3',name:'A Needlessly Long Touring Ensemble'},"
    "{id:'a4',name:'And Their Extended Orchestra'}]"
)


def test_video_card_date_stays_clear_of_the_download_button():
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    proc = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), "--run-scenario"],
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    tail = "\n".join((proc.stdout + proc.stderr).strip().splitlines()[-8:])
    import pytest

    if proc.returncode == _EXIT_NO_QT:
        pytest.skip("PySide6 / offscreen Qt unavailable")
    if proc.returncode == _EXIT_PRECONDITION:
        pytest.skip(f"could not set up the scenario in this environment:\n{tail}")
    assert proc.returncode == _EXIT_OK, f"the video card's date overlaps its download button again:\n{tail}"


def _run_scenario() -> int:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    try:
        from PySide6.QtCore import QEventLoop, QTimer, QUrl
        from PySide6.QtGui import QGuiApplication
        from PySide6.QtQml import QQmlApplicationEngine, QQmlEngine, QQmlExpression
        from PySide6.QtQuick import QQuickWindow
    except Exception as exc:  # pragma: no cover - environment guard
        print(f"Qt unavailable: {exc}", file=sys.stderr)
        return _EXIT_NO_QT

    app = QGuiApplication.instance() or QGuiApplication([])
    try:
        from tidaler.waves_ui.app import _load_mono
        from tidaler.waves_ui.backend import WavesBridge
    except Exception as exc:  # pragma: no cover - environment guard
        print(f"Qt platform/backend unavailable: {exc}", file=sys.stderr)
        return _EXIT_NO_QT

    from tests._qml_offline import PARK_LOGIN_QML, patch_offline

    patch_offline()  # BEFORE the bridge: its __init__ fires the sign-in check
    # Bridge BEFORE engine: constructing WavesBridge with a live
    # QQmlApplicationEngine crashed engine.load() natively (PySide6
    # 6.11.1, offscreen, seen when the host audio setup changed), and
    # the sibling harnesses that build the bridge first never crashed.
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
        r = QQmlExpression(QQmlEngine.contextForObject(root), root, expr).evaluate()
        return r[0] if isinstance(r, tuple) else r

    def settle(ms: int = 150) -> None:
        loop = QEventLoop()
        QTimer.singleShot(ms, loop.quit)
        loop.exec()

    root.resize(1280, 900)
    root.show()
    settle(400)
    q("bootOverlay.done = true")
    q("bootContentShown = 1")
    q(PARK_LOGIN_QML)
    q("root.openSearch()")
    q("videosModel.clear()")
    q(f"videosModel.append({_VIDEO})")
    # A FRESH map object: reassigning the same reference back to the var
    # property does not signal, and the cell may already exist, so its
    # ArtistLinks binding would keep the stale empty list.
    q(f"(function(){{ var m = {{}}; m['v1'] = {_ARTISTS}; root.artistsById = m; return 1 }})()")
    q("root.searchReveal = 1")
    q("root.searchBuilding = false")
    q("root.searchVideosExpanded = true")
    settle(700)

    # Walk the first video cell: [BigVideoThumb, meta Item]; the meta Item is
    # [meta Column, DownloadButton] and the Column's second row ends with the
    # date Text. Compare window-mapped edges.
    report = q(
        "(function(){"
        " for (var i = 0; i < videoGrid.children.length; i++) {"
        "  var cell = videoGrid.children[i];"
        "  if (!cell || !cell.item) continue;"
        "  var thumb = cell.item.children[0];"
        "  if (!thumb || thumb.videoId === undefined) continue;"
        "  var meta = cell.item.children[1];"
        "  if (!meta || meta.children.length < 2) return 'shape:meta';"
        "  var col = meta.children[0], dl = meta.children[1];"
        "  if (!dl || dl.mediaId === undefined) return 'shape:button';"
        "  var row = col.children[1];"
        "  if (!row || row.children.length < 3) return 'shape:row';"
        "  var date = row.children[2];"
        "  if (!date || !date.visible || date.width <= 0) return 'shape:date';"
        "  var dr = date.mapToItem(null, date.width, 0).x;"
        "  var bl = dl.mapToItem(null, 0, 0).x;"
        "  return 'edges:' + Math.round(dr) + ',' + Math.round(bl);"
        " }"
        " return 'shape:cell';"
        "})()"
    )
    report = str(report or "")
    if not report.startswith("edges:"):
        print(f"could not locate the video card's date and button ({report})", file=sys.stderr)
        return _EXIT_PRECONDITION
    date_right, button_left = (int(n) for n in report[len("edges:") :].split(","))
    if date_right > button_left - 4:
        print(
            "the date text runs into the download button: date right edge "
            f"{date_right}px vs button left edge {button_left}px",
            file=sys.stderr,
        )
        return _EXIT_FAIL
    return _EXIT_OK


if __name__ == "__main__":
    if "--run-scenario" in sys.argv:
        raise SystemExit(_run_scenario())
    raise SystemExit("run this file through pytest")
