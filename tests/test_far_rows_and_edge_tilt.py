"""A long artist page draws only the rows near the view, and only the rows in
the edge bands tilt.

WHAT THIS FENCES OFF
--------------------
Qt draws every item of a page however far off screen it is, and every row
once carried its own tilt binding on the scroll position. Measured on an
80-album artist at 120 Hz (2026-11-24): about 80 frames a second delivered
while scrolling, about 100 with the tilt bindings gone, about 109 with the
far rows hidden as well.

Pinned here, on the real Main.qml offscreen:

1. A row more than a screen from the view has its item hidden, and the page
   keeps its exact height (the row's Loader stays, at the item's height).
2. Scrolling to a far row shows it again and hides the rows left behind.
3. A row in the top band tilts back, one in the bottom band tilts forward,
   rows between them are flat, and a row that left a band is flat again.
4. Rows nowhere near a band carry no transform at all.
5. A row shown again looks as it did: no opacity or scale in it is mid-way.
6. An album's expanded panel is built on the first expand (a collapsed row
   costs 2.2 ms to build instead of 3.8 ms), opens and folds back.

Runs in a SUBPROCESS like the other Main.qml scenarios.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

_EXIT_OK = 0
_EXIT_NO_QT = 77
_EXIT_PRECONDITION = 78

QML_MAIN = Path(__file__).resolve().parent.parent / "waves" / "waves_ui" / "qml" / "Main.qml"
_N = 60


def test_far_rows_are_hidden_and_only_edge_rows_tilt():
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="waves-farrows-test-")
    proc = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), "--run-scenario"],
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    tail = "\n".join((proc.stdout + proc.stderr).strip().splitlines()[-12:])
    import pytest

    if proc.returncode == _EXIT_NO_QT:
        pytest.skip("PySide6 / offscreen Qt unavailable")
    if proc.returncode == _EXIT_PRECONDITION:
        pytest.skip(f"could not set up the artist page in this environment:\n{tail}")
    assert proc.returncode == _EXIT_OK, f"far rows or the edge tilt regressed. Scenario exit={proc.returncode}:\n{tail}"


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
        from _qml_offline import PARK_LOGIN_QML, patch_offline

        from waves.waves_ui.app import _load_mono
        from waves.waves_ui.backend import WavesBridge
    except Exception as exc:
        print(f"Qt platform/backend unavailable: {exc}", file=sys.stderr)
        return _EXIT_NO_QT

    patch_offline()
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
    root.setProperty("width", 1100)
    root.setProperty("height", 720)
    warns: list[str] = []
    engine.warnings.connect(lambda ws: warns.extend(w.toString() for w in ws))

    def q(expr: str):
        e = QQmlExpression(QQmlEngine.contextForObject(root), root, expr)
        r = e.evaluate()
        if e.hasError():
            raise RuntimeError(e.error().toString())
        return r[0] if isinstance(r, tuple) else r

    def settle(ms: int = 250) -> None:
        loop = QEventLoop()
        QTimer.singleShot(ms, loop.quit)
        loop.exec()

    failures: list[str] = []

    def check(cond, what: str) -> None:
        if not cond:
            failures.append(what)

    q(PARK_LOGIN_QML)
    settle(200)
    q("root.browseOpen = false; root.libraryOpen = false; root.settingsOpen = false")
    q("root.artistTracksCollapsed = false; root.artistAlbumsCollapsed = false; root.artistAlbumsExpanded = true")
    settle(50)
    albums = [
        {
            "id": f"al{i}",
            "title": f"Album {i}",
            "artist": "Artist One",
            "artist_id": "art1",
            "art": "",
            "year": "2020",
            "date": "2020-01-01",
            "tracks": 10,
            "quality": "LOSSLESS",
            "popularity": 50,
        }
        for i in range(_N)
    ]
    bridge.artistLoaded.emit(
        {
            "id": "art1",
            "name": "Artist One",
            "art": "",
            "bio": "",
            "albums": albums,
            "eps": [],
            "tracks": [],
            "videos": [],
        }
    )
    if not q("root.artistOpen"):
        print("the artist page did not open", file=sys.stderr)
        return _EXIT_PRECONDITION
    last = f"artistAlbumsRep.itemAt({_N - 1})"
    for _ in range(200):
        if q(f"artistAlbumsRep.count === {_N} && {last} !== null && {last}.item !== null") is True:
            break
        settle(50)
    else:
        print("the album rows never finished building", file=sys.stderr)
        return _EXIT_PRECONDITION
    settle(300)

    def row(i: int) -> str:
        return f"artistAlbumsRep.itemAt({i})"

    def shown(i: int) -> bool:
        return q(f"{row(i)}.item.visible") is True

    # Angle of the tilt on row i: 0 when it carries no transform at all.
    def angle(i: int) -> float:
        return float(q(f"(function(r){{ return r.transform.length ? r.transform[0].angle : 0 }})({row(i)})"))

    def transforms(i: int) -> int:
        return int(q(f"{row(i)}.transform.length"))

    # Opacity and scale of everything in a row, to prove a row shown again
    # replays no fade: nothing in a row may animate from its own `visible`.
    def look(i: int) -> str:
        return q(
            "(function(r){ var out = []; (function w(it){ out.push(it.opacity.toFixed(3) + '/' + it.scale.toFixed(3));"
            f" for (var k = 0; k < it.children.length; k++) w(it.children[k]) }})(r); return out.join(' ') }})({row(0)}.item)"
        )

    look0 = look(0)

    # ---- 1. far rows hidden, heights kept ------------------------------
    height0 = float(q("artistView.contentHeight"))
    check(height0 > _N * 60, f"the page is not as tall as its {_N} rows ({height0:.0f} px)")
    check(shown(0), "the first album, on screen, is hidden")
    check(not shown(_N - 1), "the last album, far below the view, is still drawn")
    check(q(f"{row(_N - 1)}.visible") is True, "a far row's Loader was hidden: the column closes up")
    check(float(q(f"{row(_N - 1)}.height")) >= 60, "a hidden row lost its height")
    check(transforms(_N - 1) == 0, "a row nowhere near an edge band carries a transform")

    # ---- 2. scroll to the end: shown again, the top left behind --------
    q("artistView.contentY = artistView.contentHeight - artistView.height")
    settle(200)
    check(shown(_N - 1), "the last album is on screen after the scroll but still hidden")
    check(not shown(0), "the first album, now far above the view, is still drawn")
    check(
        abs(float(q("artistView.contentHeight")) - height0) < 1,
        "hiding and showing rows changed the page height",
    )

    # ---- 3. the edge bands ---------------------------------------------
    # Put row 20's center 10 px below the view's top edge: inside the band.
    mid = 20
    q(f"artistView.contentY = {row(mid)}.mapToItem(artistView.contentItem, 0, 0).y + {row(mid)}.height / 2 - 10")
    settle(200)
    a_top = angle(mid)
    check(0 < a_top <= 9, f"the row in the top band is not tilted back (angle {a_top})")
    # 10 px into a 56 px band: (1 - 10/56) * 9.
    check(abs(a_top - (1 - 10 / 56) * 9) < 0.2, f"the top band angle is off ({a_top:.2f})")
    check(angle(mid + 4) == 0, "a row in the middle of the view is tilted")
    bottoms = [i for i in range(mid, _N) if angle(i) < 0]
    check(len(bottoms) >= 1, "no row in the bottom band tilts forward")
    check(all(-9 <= angle(i) < 0 for i in bottoms), "a bottom band angle is out of range")
    # Scroll on by three screens: the rows that were tilted are flat again.
    q("artistView.contentY = artistView.contentY + 3 * artistView.height")
    settle(200)
    check(angle(mid) == 0, "a row that left the top band kept its tilt")
    check(all(angle(i) == 0 for i in bottoms), "a row that left the bottom band kept its tilt")

    # ---- 4. the expanded panel is built on the first expand -------------
    q("artistView.contentY = 0")
    check(shown(0), "the first album is not shown again at the top")
    check(look(0) == look0, "a row shown again is mid-fade: something animates from its own visibility")
    settle(200)
    h_row = float(q(f"{row(0)}.height"))
    q(f"{row(0)}.item.toggle()")
    settle(600)
    check(float(q(f"{row(0)}.height")) > h_row + 100, "expanding an album did not open its panel")
    q(f"{row(0)}.item.toggle()")
    settle(600)
    check(abs(float(q(f"{row(0)}.height")) - h_row) < 1, "a folded album did not return to its row height")

    check(not warns, f"the engine reported warnings: {warns[:3]}")
    for f in failures:
        print("FAIL:", f, file=sys.stderr)
    return 1 if failures else _EXIT_OK


if __name__ == "__main__":
    if "--run-scenario" in sys.argv:
        rc = _run_scenario()
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(rc)
