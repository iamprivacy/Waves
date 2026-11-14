"""Search results sort by quality (issue #52).

Quality is the fifth entry in Sort By. It ranks a row on the catalog's own
quality word, the one its pill shows: HI-RES over LOSSLESS over an Atmos-only
release over HIGH over LOW, and a video by its resolution. The scenario boots
the REAL Main.qml offscreen, renders a reply with mixed qualities in the order
TIDAL ranked them, and asserts:

- choosing Quality puts hi-res first in albums, tracks and videos;
- two rows of the same quality keep TIDAL's order;
- a row with no quality word sits last;
- the arrow reverses the tiers, while the unknown row stays last and the
  ties keep their order;
- the choice persists as "quality";
- going back to Relevance restores TIDAL's order.

Runs in a SUBPROCESS, like the other bridge scenarios: building the
bridge installs process-global handlers that must not leak into the suite.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

_EXIT_OK = 0
_EXIT_REGRESSED = 1
_EXIT_NO_QT = 77
_EXIT_PRECONDITION = 78

QML_MAIN = Path(__file__).resolve().parent.parent / "waves" / "waves_ui" / "qml" / "Main.qml"

_WIN_W, _WIN_H = 1100, 900

ALBUM_QUALITY = ["LOSSLESS", "HI-RES", "", "LOW", "HI-RES", "ATMOS", "HIGH"]
TRACK_QUALITY = ["HIGH", "LOSSLESS", "HI-RES", "LOSSLESS"]
VIDEO_QUALITY = ["720p", "", "1080p", "480p"]


def _results() -> dict:
    """A search reply shaped like the backend's, in TIDAL's order."""

    def album(i: int, quality: str) -> dict:
        return {
            "id": f"al{i}",
            "title": f"Album {i}",
            "artist": "Some Artist",
            "artist_id": "ar0",
            "art": "",
            "year": "2026",
            "date": f"2026-01-{i + 1:02d}",
            "tracks": 1,
            "duration_sec": 200,
            "quality": quality,
            "popularity": 50,
            "explicit": False,
            "added": "",
        }

    def track(i: int, quality: str) -> dict:
        return {
            "id": f"t{i}",
            "title": f"Track {i}",
            "artist": "Some Artist",
            "artist_id": "ar0",
            "album": f"Album {i}",
            "album_id": f"al{i}",
            "num": 1,
            "vol": 1,
            "art": "",
            "year": "2026",
            "date": f"2026-01-{i + 1:02d}",
            "duration": "3:20",
            "duration_sec": 200,
            "quality": quality,
            "popularity": 50,
            "explicit": False,
            "added": "",
        }

    def video(i: int, quality: str) -> dict:
        return {
            "id": f"v{i}",
            "title": f"Video {i}",
            "artist": "Some Artist",
            "artists": [],
            "art": "",
            "art_big": "",
            "duration": "3:20",
            "explicit": False,
            "added": "",
            "date": f"2026-01-{i + 1:02d}",
            "quality": quality,
        }

    return {
        "artists": [],
        "albums": [album(i, q) for i, q in enumerate(ALBUM_QUALITY)],
        "tracks": [track(i, q) for i, q in enumerate(TRACK_QUALITY)],
        "videos": [video(i, q) for i, q in enumerate(VIDEO_QUALITY)],
        "playlists": [],
        "mixes": [],
        "top": None,
    }


def _run_scenario() -> int:  # noqa: C901 (one straight scenario)
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    try:
        from PySide6.QtCore import QEventLoop, QTimer, QUrl
        from PySide6.QtGui import QGuiApplication
        from PySide6.QtQml import QQmlApplicationEngine, QQmlEngine, QQmlExpression
    except Exception as exc:
        print(f"Qt unavailable: {exc}", file=sys.stderr)
        return _EXIT_NO_QT

    app = QGuiApplication.instance() or QGuiApplication([])
    try:
        from _qml_offline import PARK_LOGIN_QML, patch_offline

        patch_offline()
        from waves.waves_ui.app import _load_mono
        from waves.waves_ui.backend import WavesBridge
    except Exception as exc:
        print(f"Qt platform/backend unavailable: {exc}", file=sys.stderr)
        return _EXIT_NO_QT

    engine = QQmlApplicationEngine()
    bridge = WavesBridge(tidal=None)
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
        ctx = QQmlEngine.contextForObject(root)
        e = QQmlExpression(ctx, root, expr)
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

    settle()
    q(PARK_LOGIN_QML)
    q("openSearch()")
    settle()

    failures: list[str] = []

    def ids(model: str) -> list[str]:
        n = q(f"{model}.count")
        return [q(f"{model}.get({i}).id") for i in range(int(n))]

    def expect(label: str, want: dict) -> None:
        for model, order in want.items():
            got = ids(model)
            if got != order:
                failures.append(f"{label}: {model} is {got}, wanted {order}")

    api_order = {
        "albumsModel": [f"al{i}" for i in range(len(ALBUM_QUALITY))],
        "tracksModel": [f"t{i}" for i in range(len(TRACK_QUALITY))],
        "videosModel": [f"v{i}" for i in range(len(VIDEO_QUALITY))],
    }

    q("_searchSeq = _navSeq")
    bridge.searchResults.emit(_results())
    if not pump(lambda: not q("searchBuilding")):
        print("search never finished building", file=sys.stderr)
        return _EXIT_PRECONDITION
    settle()
    if q("sortBox.currentIndex") != 0 or q("root.sortAsc"):
        print("sort did not start at Relevance, descending", file=sys.stderr)
        return _EXIT_PRECONDITION
    expect("Relevance", api_order)

    # 1. Quality, the default direction: best first, ties in TIDAL's order,
    # the unknown row last.
    q("sortBox.currentIndex = 4")
    q("sortBox.activated(4)")
    settle(50)
    if q("sortBox.currentText") != "Quality":
        failures.append(f"index 4 reads {q('sortBox.currentText')!r}, not Quality")
    expect(
        "Quality, best first",
        {
            "albumsModel": ["al1", "al4", "al0", "al5", "al6", "al3", "al2"],
            "tracksModel": ["t2", "t1", "t3", "t0"],
            "videosModel": ["v2", "v0", "v3", "v1"],
        },
    )
    if q('waves.wavesPref("search_sort")') != "quality":
        failures.append("choosing Quality did not persist search_sort")

    # 2. The arrow reverses the tiers; the unknown row stays last and the
    # ties keep TIDAL's order.
    q('root.sortAsc = true; waves.setWavesPref("search_sort_asc", true); root.resortSearch()')
    settle(50)
    expect(
        "Quality, lowest first",
        {
            "albumsModel": ["al3", "al6", "al5", "al0", "al1", "al4", "al2"],
            "tracksModel": ["t0", "t1", "t3", "t2"],
            "videosModel": ["v3", "v0", "v2", "v1"],
        },
    )
    q('root.sortAsc = false; waves.setWavesPref("search_sort_asc", false); root.resortSearch()')
    settle(50)

    # 3. Back to Relevance: TIDAL's order again.
    q("sortBox.currentIndex = 0")
    q("sortBox.activated(0)")
    settle(50)
    expect("back to Relevance", api_order)
    if q('waves.wavesPref("search_sort")') != "relevance":
        failures.append("returning to Relevance did not persist search_sort")

    for f in failures:
        print(f"FAIL: {f}", flush=True)
    print(f"checks failed: {len(failures)}", flush=True)
    return _EXIT_REGRESSED if failures else _EXIT_OK


def test_search_results_sort_by_quality():
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="waves-sortquality-test-")
    proc = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), "--run-scenario"],  # noqa: S603 (fixed argv)
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    tail = "\n".join((proc.stdout + proc.stderr).strip().splitlines()[-12:])
    import pytest

    if proc.returncode == _EXIT_NO_QT:
        pytest.skip("PySide6 / offscreen Qt unavailable")
    if proc.returncode == _EXIT_PRECONDITION:
        pytest.skip(f"could not render a search page in this environment:\n{tail}")
    assert proc.returncode == _EXIT_OK, f"quality sort regression. Scenario exit={proc.returncode}:\n{tail}"


if __name__ == "__main__":
    raise SystemExit(_run_scenario())
