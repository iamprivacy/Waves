"""A finished download neither pins an ArtCard's hover strip up nor hides
the library word.

Two things a card downloaded this session got wrong, both reported from the
Releases feed's Just in row:

* the strip (PREVIEW | DOWNLOADED) stayed raised over the cover for the
  rest of the session, hovered or not, because the session's "done" state
  counted as a live control the way queued and running do;
* the download half said DOWNLOADED even though downloads land inside the
  library, because a session download outranked every library rule, and the
  only way to IN LIBRARY was the rescan debounce (15 s after the last track).

Now a done download drops the strip like any idle card, and the done word
follows the full button's rule: IN LIBRARY when the scan proved the copy or
downloads land inside the library root, DOWNLOADED for a copy outside it.
Offscreen, in a subprocess, with the real bridge and a temp config.
"""

from __future__ import annotations

import json
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


def test_done_download_drops_the_strip_and_words_it_by_the_library():
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="waves-art-card-done-test-")
    proc = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), "--run-scenario"],
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    tail = "\n".join((proc.stdout + proc.stderr).strip().splitlines()[-14:])
    import pytest

    if proc.returncode == _EXIT_NO_QT:
        pytest.skip("PySide6 / offscreen Qt unavailable")
    if proc.returncode == _EXIT_PRECONDITION:
        pytest.skip(f"could not set up the scenario in this environment:\n{tail}")
    assert proc.returncode == _EXIT_OK, f"a done card misbehaved. Scenario exit={proc.returncode}:\n{tail}"


def _run_scenario() -> int:
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
        from waves import matching
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
    root.setProperty("width", 1400)
    root.setProperty("height", 900)

    def q(expr: str):
        e = QQmlExpression(QQmlEngine.contextForObject(root), root, expr)
        r = e.evaluate()
        if e.hasError():
            raise RuntimeError(e.error().toString())
        return r[0] if isinstance(r, tuple) else r

    def settle(ms: int = 150) -> None:
        loop = QEventLoop()
        QTimer.singleShot(ms, loop.quit)
        loop.exec()

    settle()
    q("bootOverlay.done = true")
    q("bootContentShown = 1")
    q("browseBuilding = false")
    q("_browseAsyncBuild = false")
    q('browseStyle = "art"')

    # One album the scan proved present, one the library has never seen.
    index: dict = {}
    index.setdefault(matching.presence_key("Shadows Inside", "Miss May I"), []).append(
        {
            "title": "Shadows Inside",
            "year": "2017",
            "tracks": 10,
            "id": "/lib/Miss May I/Shadows Inside",
            "codec": "flac",
            "bitrate": 900,
            "bits": 16,
            "rate": 44100,
            "declared": 10,
            "disc_no": 0,
            "disc_total": 0,
        }
    )
    bridge._library_index = index
    q("libraryOn = true")
    q("dlInLibrary = false")

    items = [
        {"id": "a1", "kind": "album", "title": "Shadows Inside", "artist": "Miss May I", "year": "2017", "tracks": 10},
        {"id": "a4", "kind": "album", "title": "Pain Remains", "artist": "Lorna Shore", "year": "2022", "tracks": 13},
    ]
    root.setProperty("browseSections", [{"title": "ALBUMS", "rowKind": "cards", "data": "", "items": items}])
    settle(900)

    # Both downloads finished this session, nothing is hovered.
    q('dlHolder("a1").st = "done"')
    q('dlHolder("a4").st = "done"')
    settle(300)

    probe = """
    JSON.stringify((function() {
        var out = []
        function find(item, prop) {
            if (!item) return null
            var kids = item.children || []
            for (var i = 0; i < kids.length; i++) {
                if (kids[i] && kids[i][prop] !== undefined) return kids[i]
                var deeper = find(kids[i], prop)
                if (deeper !== null) return deeper
            }
            return null
        }
        function walk(item) {
            if (!item) return
            var kids = item.children || []
            for (var i = 0; i < kids.length; i++) {
                var k = kids[i]
                if (k && k.libState !== undefined && k.card) {
                    var art = find(k, "controlsOn")
                    var strip = find(k, "stWord")
                    out.push({ id: "" + (k.card.id || ""), on: art ? art.controlsOn : null,
                               word: strip ? strip.stWord : null })
                } else {
                    walk(k)
                }
            }
        }
        walk(browseLanding)
        return out
    })())
    """

    def cards() -> dict:
        got = json.loads(q(probe))
        return {c["id"]: c for c in got}

    try:
        seen = cards()
    except Exception as exc:
        print(f"probe failed: {exc}", file=sys.stderr)
        return _EXIT_PRECONDITION
    if set(seen) != {"a1", "a4"}:
        print(f"expected cards a1 and a4, probed {seen}", file=sys.stderr)
        return _EXIT_PRECONDITION

    bad = []

    def expect(label: str, want: dict) -> None:
        got = cards()
        for cid, w in want.items():
            c = got[cid]
            if c["on"] != w["on"] or c["word"] != w["word"]:
                bad.append(
                    f"{label} {cid}: got on={c['on']} word={c['word']!r}, wanted on={w['on']} word={w['word']!r}"
                )

    # Done, downloads outside the library: the strip is down on both, the
    # proven copy says IN LIBRARY, the other says DOWNLOADED.
    expect(
        "done, downloads outside the library:",
        {"a1": {"on": False, "word": "IN LIBRARY"}, "a4": {"on": False, "word": "DOWNLOADED"}},
    )

    # Downloads land inside the library root: the fresh download reads
    # IN LIBRARY at once, without waiting for a rescan.
    q("dlInLibrary = true")
    settle(100)
    expect(
        "done, downloads inside the library:",
        {"a1": {"on": False, "word": "IN LIBRARY"}, "a4": {"on": False, "word": "IN LIBRARY"}},
    )

    # A live download still raises the strip without a hover.
    q('dlHolder("a4").st = "running"')
    settle(100)
    got = cards()
    if got["a4"]["on"] is not True:
        bad.append(f"running a4: strip not raised, on={got['a4']['on']}")

    if bad:
        print("\n".join(bad), file=sys.stderr)
        return _EXIT_REGRESSED
    print(f"ok: {cards()}")
    return _EXIT_OK


if __name__ == "__main__":
    if "--run-scenario" in sys.argv:
        raise SystemExit(_run_scenario())
    raise SystemExit(_EXIT_PRECONDITION)
