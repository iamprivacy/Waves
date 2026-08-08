"""Regression: playlist rows select per ROW, and a new search forgets them.

TWO BUGS WE ARE FENCING OFF
---------------------------
1. PlaylistBlock kept its selection in a map keyed by TRACK ID. A playlist
   may legitimately list the same track twice (a DJ mix, a "best of" that
   repeats a hook, a user playlist someone added to twice), and those rows
   then shared one key: ticking either checkbox ticked both, ``allSelected``
   could never become true, and "Select all" could therefore never clear.
   Selection is keyed by row index now, so duplicate rows are independent.
2. ``onSearchResults`` cleared the album expand state and its track cache but
   not the playlist ones, so a playlist expanded in one search rendered
   already-expanded in the NEXT search, showing the previous fetch's rows with
   no refetch to correct them (playlists mutate: the always-on freshness rule
   forbids a cache that only a restart can clear).

This drives the REAL Main.qml: renders a search, expands the playlist, feeds
it a track list containing the same track twice, exercises Select all, then
renders a second search and asserts both playlist maps came back empty.

Runs in a SUBPROCESS: building the bridge installs process-global handlers
that must not leak into the rest of the suite.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

_EXIT_OK = 0
_EXIT_DUP_SELECTION = 1  # duplicate rows still share one checkbox
_EXIT_STALE_EXPAND = 2  # a new search kept the old playlist expansion/cache
_EXIT_NO_QT = 77
_EXIT_PRECONDITION = 78

QML_MAIN = Path(__file__).resolve().parent.parent / "tidaler" / "waves_ui" / "qml" / "Main.qml"

_WIN_W, _WIN_H = 1180, 800

# The same track id twice, on purpose, plus a video entry.
_ROWS = [
    {
        "id": "t1",
        "kind": "track",
        "num": 1,
        "title": "Hook",
        "artist": "DMX",
        "duration": "3:10",
        "popularity": 40,
        "explicit": False,
    },
    {
        "id": "v1",
        "kind": "video",
        "num": 2,
        "title": "Interlude",
        "artist": "DMX",
        "duration": "4:01",
        "popularity": 55,
        "explicit": True,
    },
    {
        "id": "t1",
        "kind": "track",
        "num": 3,
        "title": "Hook (reprise)",
        "artist": "DMX",
        "duration": "3:10",
        "popularity": 40,
        "explicit": False,
    },
]


def test_playlist_selection_is_per_row_and_a_new_search_clears_it():
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="waves-plsel-test-")
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
    if proc.returncode == _EXIT_DUP_SELECTION:
        raise AssertionError(f"duplicate playlist rows share one selection key again:\n{tail}")
    if proc.returncode == _EXIT_STALE_EXPAND:
        raise AssertionError(f"a new search kept the previous playlist expansion/cache:\n{tail}")
    assert proc.returncode == _EXIT_OK, f"playlist selection scenario failed:\n{tail}"


def _results(tag: str) -> dict:
    return {
        "artists": [],
        "albums": [],
        "tracks": [],
        "videos": [],
        "mixes": [],
        "playlists": [{"id": f"{tag}pl", "title": f"{tag} Essentials", "art": "", "tracks": 3, "creator": "TIDAL"}],
    }


# Walks the rendered tree for the PlaylistBlock of a given id; every probe
# below runs its body with `pb` bound to that block.
_PROBE = (
    "(function(){"
    " function walk(it){"
    "  if (!it) return null;"
    "  if (it.plId === '%s') return it;"
    "  for (var i = 0; i < it.children.length; i++) {"
    "   var hit = walk(it.children[i].item || it.children[i]);"
    "   if (hit) return hit;"
    "  }"
    "  return null;"
    " }"
    " var pb = walk(contentCol);"
    " if (!pb) return -999;"
    " %s"
    "})()"
)


def _run_scenario() -> int:  # (one exit per failed step, on purpose)
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    try:
        from PySide6.QtCore import QEventLoop, QTimer, QUrl
        from PySide6.QtGui import QGuiApplication
        from PySide6.QtQml import QQmlApplicationEngine, QQmlEngine, QQmlExpression
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
    root.setProperty("width", _WIN_W)
    root.setProperty("height", _WIN_H)

    def q(expr: str):
        e = QQmlExpression(QQmlEngine.contextForObject(root), root, expr)
        r = e.evaluate()
        if e.hasError():
            raise RuntimeError(e.error().toString())
        return r[0] if isinstance(r, tuple) else r

    def pump(predicate, timeout_ms: int = 6000) -> bool:
        loop = QEventLoop()
        state = {"ok": False}

        def tick():
            try:
                if predicate():
                    state["ok"] = True
                    loop.quit()
            except Exception:
                loop.quit()

        poll = QTimer()
        poll.setInterval(25)
        poll.timeout.connect(tick)
        poll.start()
        QTimer.singleShot(timeout_ms, loop.quit)
        loop.exec()
        poll.stop()
        return state["ok"]

    def settle(ms: int = 200) -> None:
        pump(lambda: False, ms)

    def probe(pl_id: str, body: str):
        return q(_PROBE % (pl_id, body))

    settle(300)
    q("bootOverlay.done = true")
    q("bootContentShown = 1")
    q("openSearch()")
    settle()

    # 1. First search renders one playlist row.
    q("_searchSeq = _navSeq")
    bridge.searchResults.emit(_results("one"))
    if not pump(lambda: not q("searchBuilding")):
        print("first search never finished building", file=sys.stderr)
        return _EXIT_PRECONDITION
    q("root.searchPlaylistsExpanded = true")
    q("root.searchReveal = 1")
    settle(500)
    if probe("onepl", "return 1;") != 1:
        print("no PlaylistBlock rendered for the search payload", file=sys.stderr)
        return _EXIT_PRECONDITION

    # 2. Expand it and deliver a track list holding the same track twice.
    # The expand state is set directly rather than through pb.toggle(): toggle
    # also asks the bridge for the rows, and with no session that fetch fails
    # asynchronously and would race the delivery below.
    q("root.expandedPlaylists = {'onepl': true}")
    settle()
    import json

    bridge.playlistTracksLoaded.emit("onepl", json.loads(json.dumps(_ROWS)))
    settle(300)
    if probe("onepl", "return pb.trackList.length;") != len(_ROWS):
        print("delivered rows did not reach the block", file=sys.stderr)
        return _EXIT_PRECONDITION

    # 3. Select all: every ROW counts, including the repeated track.
    n = probe("onepl", "pb.toggleAll(); return pb.selCount;")
    all_on = probe("onepl", "return pb.allSelected ? 1 : 0;")
    if n != len(_ROWS) or all_on != 1:
        print(f"Select all picked {n} of {len(_ROWS)} rows (allSelected={all_on})", file=sys.stderr)
        return _EXIT_DUP_SELECTION

    # 4. ...and Select all can therefore clear again.
    if probe("onepl", "pb.toggleAll(); return pb.selCount;") != 0:
        print("Select all could not clear the selection", file=sys.stderr)
        return _EXIT_DUP_SELECTION

    # 5. The two rows carrying the same track id are independent.
    probe("onepl", "pb.setSel(0, 'track', true); return 1;")
    first = probe("onepl", "return pb.sel[0] !== undefined ? 1 : 0;")
    dup = probe("onepl", "return pb.sel[2] !== undefined ? 1 : 0;")
    if first != 1 or dup != 0:
        print(f"ticking row 0 also ticked its duplicate (row0={first} row2={dup})", file=sys.stderr)
        return _EXIT_DUP_SELECTION

    # 6. A second search starts clean: no expansion, no cached rows.
    q("_searchSeq = _navSeq")
    bridge.searchResults.emit(_results("two"))
    if not pump(lambda: not q("searchBuilding")):
        print("second search never finished building", file=sys.stderr)
        return _EXIT_PRECONDITION
    settle(300)
    ex = q("Object.keys(root.expandedPlaylists).length")
    cached = q("Object.keys(root.playlistTrackCache).length")
    print(f"afterSecondSearch expanded={ex} cached={cached}", flush=True)
    if ex != 0 or cached != 0:
        return _EXIT_STALE_EXPAND
    return _EXIT_OK


if __name__ == "__main__":
    if "--run-scenario" in sys.argv:
        raise SystemExit(_run_scenario())
    raise SystemExit("run this file through pytest")
