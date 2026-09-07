"""A search refresh swaps the rows in place, and swaps them CORRECTLY.

WHAT THIS FENCES OFF
--------------------
The search page paints from a cached result and then corrects itself when the
wire answers (the backend's stale-then-revalidate). That correction runs with
the build veil down, on purpose: the page the user is already reading just
becomes current, with no loading flash. Every result Loader reads that same
veil flag for its ``asynchronous`` property, so with the veil down a
clear-and-rebuild refill incubates the whole result set SYNCHRONOUSLY on the
GUI thread. Measured offscreen on the real Main.qml with the page laid out, a
cap-sized correction differing by one album cost 258 to 319 ms of frozen
window (median 294), against 13 to 14 ms for the fresh handler, on the one
code path whose entire purpose is to feel instant. The same measurement after
the in-place reconcile: 12.8 to 14.5 ms, median 13.7.

Speed is only half of it, and the half a test cannot see. What this pins is
the other half: that reconciling by id still produces exactly what a refill
produced. A row added, a row removed, a row reordered and a row whose fields
changed all have to land, or the page is fast and wrong.

Runs in a SUBPROCESS like the other Main.qml scenarios: building the bridge
installs process-global handlers that must not leak into the suite.
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


def test_a_refresh_reconciles_the_rows_in_place_and_gets_them_right():
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="waves-search-refresh-test-")
    proc = subprocess.run(  # (fixed argv: this file, one flag)
        [sys.executable, str(Path(__file__).resolve()), "--run-scenario"],
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    tail = "\n".join((proc.stdout + proc.stderr).strip().splitlines()[-20:])
    import pytest

    if proc.returncode == _EXIT_NO_QT:
        pytest.skip("PySide6 / offscreen Qt unavailable")
    if proc.returncode == _EXIT_PRECONDITION:
        pytest.skip(f"could not set up the scenario in this environment:\n{tail}")
    assert proc.returncode == _EXIT_OK, f"the in-place refresh regressed. Scenario exit={proc.returncode}:\n{tail}"


def _album(ident: str, title: str, *, year: int = 2020, quality: str = "LOSSLESS") -> dict:
    return {
        "id": ident,
        "title": title,
        "artist": "Lab Artist",
        "artist_id": "ar0",
        "art": "",
        "year": year,
        "date": f"{year}-01-01",
        "tracks": 10,
        "duration_sec": 2400,
        "quality": quality,
        "popularity": 10,
        "explicit": False,
        "kind": "album",
        "artists": [{"id": "ar0", "name": "Lab Artist"}],
    }


def _artist(ident: str, name: str) -> dict:
    return {"id": ident, "name": name, "art": "", "kind": "artist"}


def _payload(albums: list, artists: list, *, refresh: bool) -> dict:
    out = {
        "artists": artists,
        "albums": albums,
        "tracks": [],
        "videos": [],
        "playlists": [],
        "mixes": [],
        "top": None,
    }
    if refresh:
        out["refresh"] = True
    return out


def _run_scenario() -> int:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    try:
        from PySide6.QtCore import QEventLoop, QTimer, QUrl
        from PySide6.QtGui import QGuiApplication
        from PySide6.QtQml import QQmlApplicationEngine, QQmlEngine, QQmlExpression
    except Exception as exc:
        print(f"Qt unavailable: {exc}", file=sys.stderr)
        return _EXIT_NO_QT

    from _qml_offline import PARK_LOGIN_QML, patch_offline

    patch_offline()
    app = QGuiApplication.instance() or QGuiApplication([])
    try:
        from waves.waves_ui.app import _load_mono
        from waves.waves_ui.backend import WavesBridge
    except Exception as exc:
        print(f"Qt platform/backend unavailable: {exc}", file=sys.stderr)
        return _EXIT_NO_QT

    WavesBridge._library_root = lambda self: ""  # type: ignore[method-assign]
    WavesBridge.loadBrowse = lambda self: None  # type: ignore[method-assign]
    WavesBridge.refreshBrowse = lambda self: None  # type: ignore[method-assign]

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

    def q(expr: str):
        e = QQmlExpression(QQmlEngine.contextForObject(root), root, expr)
        r = e.evaluate()
        if e.hasError():
            raise RuntimeError(e.error().toString())
        if isinstance(r, tuple):
            r = r[0]
        return r.toVariant() if hasattr(r, "toVariant") else r

    def settle(ms: int = 250) -> None:
        loop = QEventLoop()
        QTimer.singleShot(ms, loop.quit)
        loop.exec()

    failures: list[str] = []

    def check(cond, what: str) -> None:
        if not cond:
            failures.append(what)

    def ids(model: str) -> list:
        return [q(f"{model}.get({i}).id") for i in range(int(q(f"{model}.count")))]

    q(PARK_LOGIN_QML)
    settle(200)
    q("root._searchSeq = root._navSeq")
    q("root.browseOpen = false; root.libraryOpen = false; root.settingsOpen = false")
    settle(50)

    # Relevance order, so applySort leaves the wire's order alone and the
    # assertions below are about the reconcile rather than about sorting.
    q("sortBox.currentIndex = 0; root.sortAsc = false")

    first = [_album("al1", "One"), _album("al2", "Two"), _album("al3", "Three")]
    bridge.searchResults.emit(_payload(first, [_artist("ar1", "Alpha"), _artist("ar2", "Beta")], refresh=False))
    settle(500)
    # Realise the page: without a layout the rows are never built and this
    # scenario would pass against a reconcile that does nothing at all.
    q("results.contentHeight")
    settle(150)
    check(ids("albumsModel") == ["al1", "al2", "al3"], f"the fresh search did not fill: {ids('albumsModel')}")
    # The delegate sitting at row 0 right now. A refill destroys every one of
    # these and builds new ones, which is the cost; the reconcile keeps them.
    # The model's contents cannot show the difference, so identity is asked.
    # Identity via the object's own printed form (it carries the address):
    # comparing the Python wrappers is no good, PySide can hand back a fresh
    # wrapper for the same C++ object.
    # Follow ONE album across the correction. al3 sits at index 2 now and at
    # index 0 afterwards, so this asks whether its delegate was carried over or
    # thrown away and built again. Comparing a fixed INDEX would prove nothing:
    # index 0 holds a different album after the move, quite correctly.
    before = q("String(albumsRep.itemAt(2))")
    check(bool(before), "the album rows were never built, so this scenario proves nothing")

    # The correction: al2 is gone, al3 moved up, al4 is new, and al1's title
    # and quality both changed. Every kind of edit the reconcile has to make.
    second = [
        _album("al3", "Three"),
        _album("al1", "One (Remastered)", quality="HI_RES_LOSSLESS"),
        _album("al4", "Four"),
    ]
    q("root._searchSeq = root._navSeq")
    bridge.searchResults.emit(_payload(second, [_artist("ar2", "Beta"), _artist("ar9", "Gamma")], refresh=True))
    settle(300)

    got = ids("albumsModel")
    check(got == ["al3", "al1", "al4"], f"the refresh left the wrong rows, or the wrong order: {got}")
    check(
        q("albumsModel.get(1).title") == "One (Remastered)",
        f"a changed field was not written: {q('albumsModel.get(1).title')!r}",
    )
    check(
        q("albumsModel.get(1).quality") == "HI_RES_LOSSLESS",
        f"a second changed field on the same row was not written: {q('albumsModel.get(1).quality')!r}",
    )
    check(q("albumsModel.get(0).title") == "Three", "an unchanged row lost its title")
    after = q("String(albumsRep.itemAt(0))")  # al3 again, one row up
    check(bool(after), "the album rows went away entirely")
    check(
        after == before,
        f"the refresh rebuilt the row delegates instead of reconciling them: {before} -> {after}",
    )
    check(ids("artistsModel") == ["ar2", "ar9"], f"the artist strip did not reconcile: {ids('artistsModel')}")

    # The page must not have been veiled: this path is the one that never
    # flashes, and a veil here would mean it fell back to a full rebuild.
    check(q("root.searchBuilding") is False, "the in-place refresh raised the build veil")

    if failures:
        for f in failures:
            print("REGRESSED:", f, file=sys.stderr)
        return _EXIT_REGRESSED
    print("search refresh reconciles in place: OK")
    return _EXIT_OK


if __name__ == "__main__":
    if "--run-scenario" in sys.argv:
        raise SystemExit(_run_scenario())
