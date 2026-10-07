"""A track row wears the explicit mark beside its title (issue #51).

A song TIDAL carries both explicit and clean lists as two rows with the same
title, artist, album, date, duration and quality, and nothing told them apart
but a sticker the cover may or may not carry. The row now wears the same "E"
the video cells already do, read off the track's own flag (always parsed, so
it is trustworthy, unlike an album's).

The scenario seeds search with a twin pair and a long, fresh, explicit title,
then checks the mark is on the explicit row only, sits after the title text,
and stays clear of the NEW tag and the row's edge when the title elides.

It runs in a SUBPROCESS like the other Main.qml scenarios: building the bridge
installs process-global handlers that must not leak into the suite.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

import pytest

_EXIT_OK = 0
_EXIT_REGRESSED = 1
_EXIT_NO_QT = 77
_EXIT_PRECONDITION = 78

QML_MAIN = Path(__file__).resolve().parent.parent / "waves" / "waves_ui" / "qml" / "Main.qml"


def test_explicit_rows_wear_the_mark_and_clean_rows_do_not() -> None:
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    sandbox = tempfile.mkdtemp(prefix="waves-explicit-mark-")
    env["XDG_CONFIG_HOME"] = sandbox
    env["HOME"] = sandbox
    proc = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), "--run-scenario"],
        env=env,
        capture_output=True,
        text=True,
        timeout=240,
    )
    tail = "\n".join((proc.stdout + proc.stderr).strip().splitlines()[-16:])
    if proc.returncode == _EXIT_NO_QT:
        pytest.skip("PySide6 / offscreen Qt unavailable")
    if proc.returncode == _EXIT_PRECONDITION:
        pytest.skip(f"could not set up the scenario in this environment:\n{tail}")
    assert proc.returncode == _EXIT_OK, f"the explicit mark is wrong on screen:\n{tail}"


# Every visible track row: its title, the title Text's box, the rendered text's
# width, and the scene x of its explicit mark and NEW tag (null when absent).
_ROWS = """
JSON.stringify((function() {
    var out = []
    function find(it, name) {
        if (!it) return null
        if (it.objectName === name && it.visible && it.width > 0) return it
        var kids = it.children || []
        for (var i = 0; i < kids.length; i++) { var f = find(kids[i], name); if (f) return f }
        return null
    }
    function titleOf(row) {
        var t = find(row, "trackTitle")
        return t && t.text === row.title ? t : null
    }
    function walk(it) {
        if (!it) return
        if (it.visible && it.tId !== undefined && it.durationSec !== undefined) {
            var t = titleOf(it), e = find(it, "explicitMark"), n = find(it, "newTag")
            out.push({
                title: "" + it.title,
                tx: t ? t.mapToItem(null, 0, 0).x : -1, tw: t ? t.width : -1,
                tcw: t ? t.contentWidth : -1, elided: t ? t.truncated : false,
                ex: e ? e.mapToItem(null, 0, 0).x : null, ew: e ? e.width : 0,
                nx: n ? n.mapToItem(null, 0, 0).x : null
            })
            return
        }
        var kids = it.children || []
        for (var i = 0; i < kids.length; i++) walk(kids[i])
    }
    walk(root.contentItem)
    return out
})())
"""


def _run_scenario() -> int:
    here = Path(__file__).resolve()
    sys.path.insert(0, str(here.parent.parent))
    sys.path.insert(0, str(here.parent))
    try:
        from PySide6.QtCore import QEventLoop, QTimer, QUrl
        from PySide6.QtGui import QGuiApplication
        from PySide6.QtQml import QQmlApplicationEngine, QQmlEngine, QQmlExpression
        from PySide6.QtQuick import QQuickWindow  # noqa: F401 (types the root, for the optional grab)
    except Exception as exc:  # pragma: no cover - environment guard
        print(f"Qt unavailable: {exc}", file=sys.stderr)
        return _EXIT_NO_QT

    app = QGuiApplication.instance() or QGuiApplication([])
    try:
        from _qml_offline import PARK_LOGIN_QML, patch_offline

        patch_offline()
        from waves.waves_ui.app import _load_mono
        from waves.waves_ui.backend import WavesBridge
    except Exception as exc:  # pragma: no cover - environment guard
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
    root.setProperty("width", 1280)
    root.setProperty("height", 900)

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

    old = "2019-05-03"
    fresh = (date.today() - timedelta(days=2)).isoformat()
    long_title = "A Very Long Song Title That Keeps Going " * 6

    def track(tid: str, title: str, explicit, when: str = old) -> dict:
        return {
            "id": tid, "kind": "track", "title": title, "artist": "Some Artist", "artist_id": "ar0",
            "album": "Some Album", "album_id": "al" + tid, "num": 1, "vol": 1, "art": "",
            "year": when[:4], "date": when, "duration": "3:20", "duration_sec": 200,
            "quality": "LOSSLESS", "popularity": 50, "explicit": explicit, "added": "",
        }  # fmt: skip

    results = {
        "artists": [],
        "albums": [],
        "tracks": [
            track("t1", "Something I Need", True),
            track("t2", "Something I Need ", False),
            track("t3", long_title, True, when=fresh),
            track("t4", "Fresh Explicit", True, when=fresh),
        ],
        "videos": [],
        "playlists": [],
        "mixes": [],
        "top": None,
    }
    settle()
    q("bootOverlay.done = true")
    q("bootContentShown = 1")
    q(PARK_LOGIN_QML)
    q("openSearch()")
    settle()
    q("_searchSeq = _navSeq")
    bridge.searchResults.emit(results)
    if not pump(lambda: not q("searchBuilding")):
        print("search never finished building", file=sys.stderr)
        return _EXIT_PRECONDITION
    settle(500)
    rows = {r["title"]: r for r in json.loads(q(_ROWS))}
    needed = {"Something I Need", "Something I Need ", long_title, "Fresh Explicit"}
    if not needed <= set(rows):
        print(f"search did not show every seeded row: missing {sorted(needed - set(rows))}", file=sys.stderr)
        return _EXIT_PRECONDITION

    failures: list[str] = []
    if any(rows[t]["tx"] < 0 for t in needed):
        failures.append(f"a row's title Text was not found: {rows}")
    if rows["Something I Need"]["ex"] is None:
        failures.append("the explicit twin wears no mark")
    if rows["Something I Need "]["ex"] is not None:
        failures.append("the clean twin wears the explicit mark")

    for title in ("Something I Need", "Fresh Explicit", long_title):
        r = rows[title]
        if r["tx"] < 0:
            continue
        if r["ex"] is None:
            failures.append(f"{title[:24]!r}: no mark")
            continue
        text_end = r["tx"] + r["tcw"]
        if r["ex"] < text_end + 2:
            failures.append(f"{title[:24]!r}: the mark overlaps the title text ({r['ex']} < {text_end})")
        if r["ex"] + r["ew"] > r["tx"] + r["tw"] + 0.5:
            failures.append(f"{title[:24]!r}: the mark spills past the title's box")
        if r["nx"] is not None and r["nx"] < r["ex"] + r["ew"] + 4:
            failures.append(f"{title[:24]!r}: the NEW tag overlaps the mark ({r['nx']} vs {r['ex']} + {r['ew']})")

    # These are what the row must do on screen, so they fail rather than skip.
    if not rows[long_title]["elided"]:
        failures.append("the long title did not elide")
    if rows[long_title]["nx"] is None or rows["Fresh Explicit"]["nx"] is None:
        failures.append("a fresh row wears no NEW tag")

    shot = os.environ.get("WAVES_EXPLICIT_SHOT")
    if shot:
        root.grabWindow().save(shot)

    if failures:
        print("\n".join(failures), file=sys.stderr)
        return _EXIT_REGRESSED
    print("ok")
    return _EXIT_OK


if __name__ == "__main__" and "--run-scenario" in sys.argv:
    sys.exit(_run_scenario())
