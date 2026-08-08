"""Search playlist rows behave like album rows.

A playlist result used to be an inert card: only its DOWNLOAD PLAYLIST
button did anything; the title was not a link and clicking the row was a
no-op, while album rows both expand in place and link their title to a
dedicated page. The PLAYLISTS section now uses PlaylistBlock (the playlist
counterpart of AlbumBlock): clicking the row expands the track list inline,
and clicking the title opens the playlist's page. This drives the real
Main.qml: click the row, feed it tracks, count the rendered rows, then
click the title and assert the browse surface keys to the playlist page.
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

_PLAYLIST = '{"id":"pl1","title":"DMX Essentials","art":"","tracks":25,"creator":"TIDAL"}'

_ROWS = [
    {
        "id": "t1",
        "kind": "track",
        "num": 1,
        "title": "Song One",
        "artist": "DMX",
        "duration": "3:10",
        "popularity": 40,
        "explicit": False,
    },
    {
        "id": "v1",
        "kind": "video",
        "num": 2,
        "title": "Video Two",
        "artist": "DMX",
        "duration": "4:01",
        "popularity": 55,
        "explicit": True,
    },
]


def test_playlist_rows_expand_and_title_opens_the_page():
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
    assert proc.returncode == _EXIT_OK, f"search playlist rows stopped behaving like album rows:\n{tail}"


def _run_scenario() -> int:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    try:
        from PySide6.QtCore import QEventLoop, QPoint, Qt, QTimer, QUrl
        from PySide6.QtGui import QGuiApplication
        from PySide6.QtQml import QQmlApplicationEngine, QQmlEngine, QQmlExpression
        from PySide6.QtQuick import QQuickWindow
        from PySide6.QtTest import QTest
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

    # The bridge's cached sign-in check raced this scenario's clicks against
    # live TIDAL latency (loginPanel's scrim swallowed whichever clicks it
    # preceded, the full-suite-only failures this harness used to produce).
    # See tests/_qml_offline.py; the patch must precede the bridge.
    from tests._qml_offline import PARK_LOGIN_QML, patch_offline

    patch_offline()
    bridge = WavesBridge(tidal=None)

    # No live TIDAL API from a unit test. tidal=None still builds a real,
    # unauthenticated session, and the expand's refetch fallback (pl1 is
    # never in _objs) would GET the real API: TIDAL answered 404 on its own
    # network schedule, which both slowed the scenario and let the layout
    # drift under full-suite load. Every fetch now fails instantly instead;
    # the harness exercises the same doomed-refetch path, deterministically.
    class _NoNetSession:
        def _fail(self, *a, **k):
            raise RuntimeError("live TIDAL API disabled in this test")

        playlist = album = artist = track = video = mix = search = _fail

    bridge.tidal.session = _NoNetSession()
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
    q("playlistsModel.clear()")
    q(f"playlistsModel.append({_PLAYLIST})")
    q("root.searchReveal = 1")
    q("root.searchBuilding = false")
    q("root.searchPlaylistsExpanded = true")
    settle(700)

    # Locate the PlaylistBlock's header row inside the results column.
    _find = (
        "(function(){"
        " function walk(it){"
        "  if (!it) return null;"
        "  if (it.plId === 'pl1') return it;"
        "  for (var i = 0; i < it.children.length; i++) {"
        "   var hit = walk(it.children[i].item || it.children[i]);"
        "   if (hit) return hit;"
        "  }"
        "  return null;"
        " }"
        " var pb = walk(contentCol);"
        " if (!pb) return '';"
        " var row = pb.children[0];"
        " var p = row.mapToItem(null, 0, 0);"
        " return Math.round(p.x) + ',' + Math.round(p.y) + ',' + Math.round(row.width) + ',' + Math.round(row.height);"
        "})()"
    )

    def find_geo() -> tuple[int, int, int, int] | None:
        """Current window-space geometry of the block's header row.

        Polled until STABLE (two identical consecutive reads), and re-captured
        immediately before every click: the search page builds sections
        asynchronously and the reveal animates, so a row's y from a moment
        ago can be stale by the time a click lands (the cause of the
        full-suite-only step-3 misses this harness used to produce), and a
        row that merely EXISTS may still be sliding to its resting place.
        """
        prev = ""
        for _ in range(80):  # up to ~8s for the async build + animations
            geo = str(q(_find) or "")
            if geo and geo == prev:
                return tuple(int(n) for n in geo.split(","))
            prev = geo
            settle(100)
        return None

    g = find_geo()
    if g is None:
        # A FAIL, not a precondition: the appended playlist must always
        # render a PlaylistBlock; its absence IS the regression.
        print("no PlaylistBlock rendered for the appended playlist", file=sys.stderr)
        return _EXIT_FAIL
    x, y, w, h = g

    # 1) Row click expands. The expand also fires the REAL loadPlaylistTracks,
    # whose doomed refetch (the no-net session above fails it instantly)
    # replies with an empty list on its own schedule; listen for that reply
    # so step 2's injected rows land AFTER it instead of racing it (the race
    # read as a flaky 0-row expand).
    backend_replies: list[str] = []
    bridge.playlistTracksLoaded.connect(lambda pid, _rows: backend_replies.append(str(pid)))
    QTest.mouseClick(root, Qt.LeftButton, Qt.NoModifier, QPoint(x + w - 300, y + h // 2))
    settle(200)
    if q("root.expandedPlaylists['pl1'] === true ? 1 : 0") != 1:
        print("clicking the playlist row did not expand it", file=sys.stderr)
        return _EXIT_FAIL
    for _ in range(100):  # generous, but the no-net reply arrives in ms
        if backend_replies:
            break
        settle(100)

    # 2) Delivered tracks render as rows (count via the block's trackList).
    import json

    bridge.playlistTracksLoaded.emit("pl1", json.loads(json.dumps(_ROWS)))
    settle(300)
    n = q(
        "(function(){"
        " function walk(it){"
        "  if (!it) return null;"
        "  if (it.plId === 'pl1') return it;"
        "  for (var i = 0; i < it.children.length; i++) {"
        "   var hit = walk(it.children[i].item || it.children[i]);"
        "   if (hit) return hit;"
        "  }"
        "  return null;"
        " }"
        " var pb = walk(contentCol);"
        " return pb ? pb.trackList.length : -1;"
        "})()"
    )
    if n != 2:
        print(f"expanded playlist shows {n} rows, expected 2", file=sys.stderr)
        return _EXIT_FAIL

    # 3) Title click opens the playlist page (the title starts right after
    # the chevron and the 46px art tile). Fresh geometry: the waits above
    # gave the rest of the page time to reflow, so the captured y may have
    # drifted since step 1.
    g = find_geo()
    if g is None:
        print("the PlaylistBlock vanished before the title click", file=sys.stderr)
        return _EXIT_FAIL
    x, y, w, h = g
    QTest.mouseClick(root, Qt.LeftButton, Qt.NoModifier, QPoint(x + 95, y + 22))
    settle(300)
    key = str(q("root.browsePageKey") or "")
    if key != "item:playlist:pl1" or q("root.browseOpen ? 1 : 0") != 1:
        print(f"clicking the title did not open the playlist page (key={key})", file=sys.stderr)
        return _EXIT_FAIL
    return _EXIT_OK


if __name__ == "__main__":
    if "--run-scenario" in sys.argv:
        raise SystemExit(_run_scenario())
    raise SystemExit("run this file through pytest")
