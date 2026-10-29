"""The Releases feed shows a page of cards at a time, on screen.

- A first page of months holding many releases puts only the first
  releasesFeedPage cards on the list (so the scrollbar is sized to what is
  shown, not to the whole payload).
- Scrolling near the end shows the next cards already here, and only once
  every one of them is shown does it ask the bridge for the next months.
- A search's answer is shown a page at a time too, and clearing it lands
  back at a depth reached after several pages were shown.

Runs in a SUBPROCESS like the other Main.qml scenarios.
"""

from __future__ import annotations

import itertools
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


def test_the_feed_shows_a_page_of_cards_at_a_time() -> None:
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    sandbox = tempfile.mkdtemp(prefix="waves-rel-reveal-")
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
    assert proc.returncode == _EXIT_OK, f"the feed's paging regressed:\n{tail}"


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

    page = int(q("releasesFeedPage"))
    cols = int(q("_relCols"))

    def shown() -> int:
        return int(q(_SHOWN))

    def to_end() -> None:
        q("relFeedList.contentY = relFeedList.originY + Math.max(0, relFeedList.contentHeight - relFeedList.height)")
        settle(250)

    def depth() -> float:
        return float(q("relFeedList.contentY - relFeedList.originY"))

    if int(q("releasesShown")) < 96:
        print(f"the first page holds only {q('releasesShown')} releases", file=sys.stderr)
        return _EXIT_PRECONDITION

    # 1. The first page of months shows one page of cards, not all of them.
    first = shown()
    if not page <= first < page + cols:
        failures.append(f"the feed opened with {first} cards shown, wanted one page ({page}) in whole rows")
    if int(q("releasesMonths")) != int(q("releasesPageMonths")):
        failures.append("the feed asked for more months while its first page still had cards to show")

    # 2. Scrolling to the end shows the next cards already here, and asks
    # for the next months only once every one of them is on the list.
    months0 = int(q("releasesMonths"))
    grew = [first]
    asked_early = []

    def asked() -> bool:
        # Judged the moment the ask goes out: while the next months are
        # still on their way, _relFeedMore still speaks for the cards here.
        if int(q("releasesMonths")) <= months0:
            return False
        if q("releasesLoadingMore") and not asked_early:
            asked_early.append(bool(q("_relFeedMore")))
        return True

    for _ in range(40):
        q("relFeedList.contentY = relFeedList.originY + Math.max(0, relFeedList.contentHeight - relFeedList.height)")
        if asked() or pump(lambda: asked() or shown() > grew[-1], 1500) and asked():
            break
        grew.append(shown())
        settle(150)
    if len(grew) < 2 or any(b <= a for a, b in itertools.pairwise(grew)):
        failures.append(f"scrolling to the end did not show the next cards a page at a time: {grew}")
    if asked_early and asked_early[0]:
        failures.append("the feed asked for the next months while cards already here were still hidden")
    if int(q("releasesMonths")) == months0 and q("releasesHasMore"):
        failures.append("the feed never asked for the next months after showing every card")
    if not pump(lambda: not q("releasesLoadingMore")):
        failures.append("the next months never arrived")
    settle(300)

    # 3. A search's answer comes a page at a time; clearing it lands back
    # at the depth browsed to, several pages down.
    q("releasesRewind()")
    q("releasesLoad()")
    pump(lambda: not q("releasesLoadingMore"), 3000)
    settle(400)
    for _ in range(3):
        to_end()
        pump(lambda: not q("_relRevealWait"), 1500)
    settle(300)
    back = depth()
    if back < 3000:
        print(f"the feed is too short to test a deep restore ({back:.0f}px)", file=sys.stderr)
        return _EXIT_PRECONDITION
    q('releasesFeedNeedle = "T"')
    q("relNeedleApply()")
    if not pump(lambda: q("relFindHave")):
        failures.append("the search never answered")
    settle(300)
    found = shown()
    if not page <= found < page + cols:
        failures.append(f"a search answer of {q('releasesShown')} showed {found} cards, wanted one page")
    q('releasesFeedNeedle = ""')
    q("relNeedleApply()")
    settle(500)
    if abs(depth() - back) > 2:
        failures.append(f"clearing a search landed at {depth():.0f}px, not back at {back:.0f}")

    if failures:
        print("\n".join(failures), file=sys.stderr)
        return _EXIT_REGRESSED
    print("ok")
    return _EXIT_OK


# How many release cards the list holds.
_SHOWN = """
(function () {
    var n = 0
    for (var i = 0; i < releasesModel.count; i++) n += (_relCards[releasesModel.get(i).uid] || []).length
    return n
})()
"""


if __name__ == "__main__" and "--run-scenario" in sys.argv:
    sys.exit(_run_scenario())
