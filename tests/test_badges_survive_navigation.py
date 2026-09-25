"""A badge already on the page must not arrive again when you come back to it.

WHAT THIS FENCES OFF
--------------------
Every "in library" badge faded itself in over 180ms on EVERY navigation. Moving
between two My Tidal categories, or between tabs, made the whole page's pills
and artist strips assemble themselves in front of the reader again, over
verdicts that had not changed and without one question asked of the bridge.

The cause was one binding shape: the fade read the badge's own ``visible``, and
``visible`` in Qt is EFFECTIVE visibility, false whenever ANY ancestor is
hidden. Every pane in this app is hidden by its own ``visible`` (a My Tidal
category, a tab), so leaving a pane drove every badge on it to opacity 0 behind
the reader's back, and the settle latch (which exists precisely so that a badge
the page was BUILT with is simply there) had long since armed. Coming back was
indistinguishable from news arriving.

So the badges now read their own verdict (``shown``) for the fade, for
``visible``, and for anything their row's layout asks of them.

BOTH HALVES ARE CHECKED, and that is the point: the cheap way to make the first
half pass is to delete the animation, which would cost the app the one thing it
is for. So the same scenario also proves that a verdict which really does land
while the page is on screen still arrives with a fade.

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

_EXIT_OK = 0
_EXIT_REGRESSED = 1
_EXIT_NO_QT = 77
_EXIT_PRECONDITION = 78

QML_MAIN = Path(__file__).resolve().parent.parent / "waves" / "waves_ui" / "qml" / "Main.qml"

_HELD = 4  # albums (and artists) the library holds
_ABSENT = 2  # albums it does not, so a pass cannot come from "everything is on"


def test_badges_do_not_arrive_again_on_every_navigation():
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="waves-badge-nav-")
    env["HOME"] = env["XDG_CONFIG_HOME"]  # nothing in the scenario may reach the real home
    proc = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), "--run-scenario"],
        env=env,
        capture_output=True,
        text=True,
        timeout=240,
    )
    tail = "\n".join((proc.stdout + proc.stderr).strip().splitlines()[-25:])
    if proc.returncode == _EXIT_NO_QT:
        pytest.skip("PySide6 / offscreen Qt unavailable")
    if proc.returncode == _EXIT_PRECONDITION:
        pytest.skip(f"could not set up the scenario in this environment:\n{tail}")
    assert proc.returncode == _EXIT_OK, (
        "the library badges animate on navigation again, so every page assembles itself in "
        f"front of the reader. Scenario exit={proc.returncode}:\n{tail}"
    )


# Walks the object tree collecting whatever `pick` returns non-null for.
_COLLECT = """
function collect(item, pick, out) {
    if (!item) return out
    var kids = item.children || []
    for (var i = 0; i < kids.length; i++) {
        var k = kids[i].item || kids[i]
        if (!k) continue
        var got = pick(k)
        if (got !== null && got !== undefined) out.push(got)
        collect(k, pick, out)
    }
    return out
}
"""


def _run_scenario() -> int:  # (a linear boot -> drive -> measure scenario)
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    try:
        from PySide6.QtCore import QEventLoop, QTimer, QUrl, Slot
        from PySide6.QtGui import QGuiApplication
        from PySide6.QtQml import QQmlApplicationEngine, QQmlEngine, QQmlExpression
    except Exception as exc:
        print(f"Qt unavailable: {exc}", file=sys.stderr)
        return _EXIT_NO_QT

    from _qml_offline import PARK_LOGIN_QML, patch_offline

    patch_offline()
    app = QGuiApplication.instance() or QGuiApplication([])
    try:
        from waves.matching import presence_key
        from waves.waves_ui.backend import WavesBridge
    except Exception as exc:
        print(f"Qt platform/backend unavailable: {exc}", file=sys.stderr)
        return _EXIT_NO_QT

    asked = {"n": 0}

    class CountingBridge(WavesBridge):
        # Every overload the real bridge registers, or Qt dispatches straight to
        # the base class and this counter silently measures nothing (see
        # test_presence_call_budget for the scar).
        @Slot(str, str, str, int, result="QVariant")
        @Slot(str, str, str, int, int, result="QVariant")
        @Slot(str, str, str, int, int, int, result="QVariant")
        def libraryAlbumPresence(self, artist, title, year, num_tracks, duration=0, explicit=-1):
            asked["n"] += 1
            return WavesBridge.libraryAlbumPresence(self, artist, title, year, num_tracks, duration, explicit)

    index: dict = {}
    rows: list[dict] = []
    for i in range(_HELD + _ABSENT):
        title, artist = f"Album {i}", f"Artist {i}"
        if i < _HELD:
            index[presence_key(title, artist)] = [
                {
                    "title": title,
                    "year": "2019",
                    "tracks": 11,
                    "id": f"/lib/{i}",
                    "codec": "flac",
                    "bitrate": 0,
                    "bits": 16,
                    "rate": 44100,
                }
            ]
        rows.append(
            {
                "id": f"al-{i}",
                "title": title,
                "artist": artist,
                "artist_id": f"a{i}",
                "art": "",
                "year": "2019",
                "date": "2019-01-01",
                "tracks": 11,
                "duration_sec": 2400,
                "quality": "LOSSLESS",
                "popularity": 50,
            }
        )

    engine = QQmlApplicationEngine()
    bridge = CountingBridge(tidal=None)
    bridge._library_index = index
    bridge._library_track_index = {}
    engine.rootContext().setContextProperty("waves", bridge)
    engine.rootContext().setContextProperty("monoFont", "JetBrains Mono")
    engine.rootContext().setContextProperty("uiFontFamily", app.font().family())
    engine.load(QUrl.fromLocalFile(str(QML_MAIN)))
    roots = engine.rootObjects()
    if not roots:
        print("Main.qml failed to load", file=sys.stderr)
        return _EXIT_PRECONDITION
    root = roots[0]
    root.setProperty("width", 1400)
    root.setProperty("height", 900)
    root.setProperty("visible", True)

    def q(expr: str):
        e = QQmlExpression(QQmlEngine.contextForObject(root), root, expr)
        r = e.evaluate()
        if e.hasError():
            raise RuntimeError(e.error().toString())
        return r[0] if isinstance(r, tuple) else r

    def settle(ms: int = 200) -> None:
        loop = QEventLoop()
        QTimer.singleShot(ms, loop.quit)
        loop.exec()

    settle(300)
    q("bootOverlay.done = true")
    q("bootContentShown = 1")
    q("browseBuilding = false")
    q("_browseAsyncBuild = false")
    q("libraryOn = true")
    q(PARK_LOGIN_QML)
    settle(200)

    # The album pills on the My Tidal panes, found by their own identity so a
    # pill that moves in the tree keeps being measured. `op` is the LIVE
    # opacity: strictly between 0 and 1 means mid-fade, i.e. arriving.
    pills = (
        "JSON.stringify((function() {" + _COLLECT + " return collect(libArea, function(k) {"
        "   if (k.album === undefined || k.presence === undefined) return null;"
        "   return { t: (k.album && k.album.title) ? ('' + k.album.title) : '',"
        "            shown: !!k.shown, op: Math.round(k.opacity * 1000) / 1000 }"
        " }, []) })())"
    )

    def read():
        return json.loads(q(pills))

    bad: list[str] = []

    # ---- the page, built with its badges already answered -------------------
    q("root.openLibrary()")
    q("root.libraryCategory = 'albums'")
    for row in rows:
        q(f"libAlbumsModel.append({json.dumps(row)})")
    settle(600)
    built = read()
    if len(built) != _HELD + _ABSENT:
        print(f"the albums pane built {len(built)} pills, wanted {_HELD + _ABSENT}", file=sys.stderr)
        return _EXIT_PRECONDITION
    held = [p for p in built if p["shown"]]
    if len(held) != _HELD:
        print(f"{len(held)} pills said IN LIBRARY, wanted {_HELD}: {built}", file=sys.stderr)
        return _EXIT_PRECONDITION
    if any(p["op"] < 0.999 for p in held):
        bad.append(f"a badge the page was BUILT with was still fading in: {held}")

    # ---- away to another category, and back --------------------------------
    q("root.libraryCategory = 'tracks'")
    settle(250)
    asked["n"] = 0
    q("root.libraryCategory = 'albums'")
    # Sampled ACROSS the fade's duration (180ms), not after it: a badge that
    # animates is back at 1 by the time the page has settled, so a single late
    # reading cannot see the very thing this test is about.
    for ms in (16, 32, 80):
        settle(ms)
        now = read()
        mid = [p for p in now if p["shown"] and p["op"] < 0.999]
        if mid:
            bad.append(f"category switch: {len(mid)} badge(s) arrived again {ms}ms in: {mid[:3]}")
            break
    if asked["n"]:
        bad.append(f"category switch: the pills re-asked the bridge {asked['n']} times for an unchanged library")

    # ---- away to another TAB, and back -------------------------------------
    settle(250)
    asked["n"] = 0
    q("root.openBrowse()")
    settle(250)
    q("root.openLibrary()")
    for ms in (16, 32, 80):
        settle(ms)
        now = read()
        mid = [p for p in now if p["shown"] and p["op"] < 0.999]
        if mid:
            bad.append(f"tab switch: {len(mid)} badge(s) arrived again {ms}ms in: {mid[:3]}")
            break
    if asked["n"]:
        bad.append(f"tab switch: the pills re-asked the bridge {asked['n']} times for an unchanged library")

    # ---- and news, which MUST still arrive with a fade ----------------------
    # A fresh dict is a new index object, so the bridge's own memo resets with
    # it exactly as a real publish resets it.
    settle(250)
    bridge._library_index = {}
    bridge.libraryPresenceChanged.emit()
    settle(250)
    if any(p["shown"] for p in read()):
        print("emptying the index left badges on; the scenario cannot test arrival", file=sys.stderr)
        return _EXIT_PRECONDITION
    bridge._library_index = index
    bridge.libraryPresenceChanged.emit()
    arrived = False
    for ms in (16, 32, 80):
        settle(ms)
        if any(p["shown"] and 0.0 < p["op"] < 0.999 for p in read()):
            arrived = True
            break
    if not arrived:
        bad.append(
            "a library publish turned badges present while the page was on screen and NOT ONE of them "
            "faded in: the arrival animation is gone (or this probe no longer measures it)"
        )

    for line in bad:
        print(f"REGRESSED: {line}", file=sys.stderr)
    return _EXIT_REGRESSED if bad else _EXIT_OK


if __name__ == "__main__":
    sys.exit(_run_scenario())
