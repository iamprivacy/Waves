"""Searching the Releases feed and leaving the search, on screen.

- Clearing a search lands the feed back at the depth it was browsed to,
  measured after the feed has re-laid out its full rows (a measure taken
  before that saw only the search's short answer and clamped to the top).
- Pressing the Releases tab again during a search goes to the top of the
  feed, never to the depth the search began at.

Runs in a SUBPROCESS like the other Main.qml scenarios.
"""

from __future__ import annotations

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


def test_leaving_a_feed_search_lands_where_it_should() -> None:
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    sandbox = tempfile.mkdtemp(prefix="waves-rel-search-")
    env["XDG_CONFIG_HOME"] = sandbox
    env["HOME"] = sandbox  # nothing in the scenario may reach the real home
    proc = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), "--run-scenario"],
        env=env,
        capture_output=True,
        text=True,
        timeout=240,
    )
    tail = "\n".join((proc.stdout + proc.stderr).strip().splitlines()[-30:])
    if proc.returncode == _EXIT_NO_QT:
        pytest.skip("PySide6 / offscreen Qt unavailable")
    if proc.returncode == _EXIT_PRECONDITION:
        pytest.skip(f"could not set up the scenario in this environment:\n{tail}")
    assert proc.returncode == _EXIT_OK, f"leaving a feed search regressed:\n{tail}"


def _rel(rid: str, day: str, title: str, artist: str, rtype: str = "album") -> dict:
    return {
        "kind": "album",
        "id": rid,
        "title": title,
        "artist": artist,
        "artist_id": "a-" + rid,
        "artists": [{"id": "a-" + rid, "name": artist}],
        "art": "",
        "date": day,
        "listed": "",
        "year": day[:4],
        "rtype": rtype,
        "explicit": False,
        "tracks": 10,
        "duration_sec": 200,
        "quality": "LOSSLESS",
    }


def _run_scenario() -> int:  # noqa: C901
    here = Path(__file__).resolve()
    sys.path.insert(0, str(here.parent.parent))
    sys.path.insert(0, str(here.parent))
    try:
        from PySide6.QtCore import QEventLoop, QTimer, QUrl
        from PySide6.QtGui import QGuiApplication
        from PySide6.QtQml import QQmlApplicationEngine, QQmlEngine, QQmlExpression
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

    import datetime as _datetime

    from waves.waves_ui import bridge_releases

    class _Day(_datetime.date):
        @classmethod
        def today(cls):
            return cls(2026, 10, 14)

    bridge_releases._dt = type("_Dt", (), {"date": _Day})

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
    root.setProperty("height", 1200)

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

    failures: list[str] = []
    settle()
    q(PARK_LOGIN_QML)
    q("bootOverlay.done = true")
    q("bootContentShown = 1")
    q("newCutoffClock.running = false")
    store = bridge._releases
    bridge._waves_prefs["releases_window_months"] = "12"
    for n in range(80):
        aid, name = f"a{n:02d}", f"Artist {n:02d}"
        store.follow(aid, name, "")
        store.record_check(
            aid,
            [_rel(f"{aid}r{k}", f"2026-{1 + (n + k) % 9:02d}-{1 + k:02d}", f"T{k}", name) for k in range(4)],
        )

    q("openReleases()")
    if not pump(lambda: q("releasesModel.count") > 0 and not q("releasesLoadingMore")):
        print("the feed never arrived", file=sys.stderr)
        return _EXIT_PRECONDITION
    settle(400)

    def depth() -> float:
        return float(q("relFeedList.contentY - relFeedList.originY"))

    def browse_to(y: int) -> None:
        q(f"relFeedList.contentY = relFeedList.originY + {y}")
        settle(300)

    def search(needle: str) -> None:
        q(f'releasesFeedNeedle = "{needle}"')
        q("relNeedleApply()")
        if needle and not pump(lambda: q("relFindHave")):
            failures.append(f"the search for {needle!r} never answered")
        settle(300)

    most = float(q("relFeedList.contentHeight - relFeedList.height"))
    if most < 1600:
        print(f"the feed is too short to test a depth ({most:.0f}px)", file=sys.stderr)
        return _EXIT_PRECONDITION

    # 1. Clearing a search lands back at the browsed depth.
    browse_to(1500)
    search("Artist 05")
    if float(q("relFeedList.contentHeight")) > 1500:
        print("the search's answer is not short enough to test the clamp", file=sys.stderr)
        return _EXIT_PRECONDITION
    search("")
    if abs(depth() - 1500) > 2:
        failures.append(f"clearing a search landed at {depth():.0f}px, not back at 1500")

    # 2. The tab pressed again during a search goes to the top.
    browse_to(1500)
    search("Artist 05")
    q("openReleases()")
    settle(500)
    if depth() > 1:
        failures.append(f"the tab pressed again during a search landed at {depth():.0f}px, not the top")
    if q("releasesFinding"):
        failures.append("the tab pressed again left the search on")

    if failures:
        print("\n".join(failures), file=sys.stderr)
        return _EXIT_REGRESSED
    print("ok")
    return _EXIT_OK


if __name__ == "__main__" and "--run-scenario" in sys.argv:
    sys.exit(_run_scenario())
