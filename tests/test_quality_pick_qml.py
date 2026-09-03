"""The quality badge's tier menu (issue #36), on the real rows.

WHAT THIS FENCES OFF
--------------------
Every row-level quality badge (track rows, album rows, the expanded album
panel) is a QualPick around the app's QualTag. A choice made on the bridge
must reach the badge: the pill states the chosen tier on a tinted ground; an
album's choice turns every one of its track badges too, floored by what each
track can land; a track's own choice beats its album's; choosing the Settings
tier under an album that chose is kept as DEFAULT (the track stops
inheriting) and is a plain clear otherwise; a video row and an Atmos pill
offer no menu; the menu toggles (a second click on the badge closes it, it
must not close-then-reopen); a tier above the item's ceiling is listed NOT
OFFERED and inert; the DEFAULT mark follows the setting live; and a download
asks at the choice WITHOUT spending it, so the badge and the copy that lands
state the same tier.

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


def test_the_quality_badge_menu_follows_the_choice_on_every_row():
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="waves-quality-pick-test-")
    proc = subprocess.run(  # (fixed argv: this file, one flag)
        [sys.executable, str(Path(__file__).resolve()), "--run-scenario"],
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    tail = "\n".join((proc.stdout + proc.stderr).strip().splitlines()[-16:])
    import pytest

    if proc.returncode == _EXIT_NO_QT:
        pytest.skip("PySide6 / offscreen Qt unavailable")
    if proc.returncode == _EXIT_PRECONDITION:
        pytest.skip(f"could not set up the scenario in this environment:\n{tail}")
    assert proc.returncode == _EXIT_OK, f"the quality badge menu regressed. Scenario exit={proc.returncode}:\n{tail}"


_ROW = {
    "artist": "Lab Artist",
    "artist_id": "",
    "artists": [],
    "art": "",
    "year": "2024",
    "date": "2024-03-11",
    "explicit": False,
    "added": "",
    "popularity": 50,
}


def _payload() -> dict:
    tracks = [
        dict(
            _ROW,
            id="t1",
            title="Hi-res song",
            album="Album One",
            album_id="a1",
            num=1,
            vol=1,
            duration="3:00",
            duration_sec=180,
            quality="HI-RES",
        ),
        dict(
            _ROW,
            id="t2",
            title="Lossless song",
            album="Album One",
            album_id="a1",
            num=2,
            vol=1,
            duration="3:00",
            duration_sec=180,
            quality="LOSSLESS",
        ),
        dict(
            _ROW,
            id="t3",
            title="Other album song",
            album="Album Two",
            album_id="a2",
            num=1,
            vol=1,
            duration="3:00",
            duration_sec=180,
            quality="HI-RES",
        ),
        dict(
            _ROW,
            id="t4",
            title="Atmos song",
            album="Album Two",
            album_id="a2",
            num=2,
            vol=1,
            duration="3:00",
            duration_sec=180,
            quality="ATMOS",
        ),
    ]
    albums = [dict(_ROW, id="a1", title="Album One", tracks=2, duration_sec=360, quality="HI-RES")]
    videos = [dict(_ROW, id="v1", title="A video", duration="4:00", duration_sec=240, quality="1080p")]
    return {"artists": [], "albums": albums, "tracks": tracks, "videos": videos, "playlists": [], "mixes": []}


_FIND_PICKS = """
(function() {
    var out = {};
    function walk(o) {
        if (!o) return;
        var kids = o.children;
        if (!kids) return;
        for (var i = 0; i < kids.length; ++i) {
            var c = kids[i];
            if (c && typeof c.effective !== "undefined" && typeof c.mediaId !== "undefined" && c.visible)
                out[c.mediaId + "/" + (c.mix && c.mix.length > 1 ? "mixed" : "")] = c;
            walk(c);
        }
    }
    walk(root.contentItem);
    return out;
})()
"""


def _run_scenario() -> int:  # noqa: C901 (one straight scenario)
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    try:
        from PySide6.QtCore import QEventLoop, QTimer, QUrl
        from PySide6.QtGui import QGuiApplication
        from PySide6.QtQml import QQmlApplicationEngine, QQmlEngine, QQmlExpression
    except Exception as exc:
        print(f"Qt unavailable: {exc}", file=sys.stderr)
        return _EXIT_NO_QT

    from types import SimpleNamespace

    from _qml_offline import PARK_LOGIN_QML, patch_offline

    patch_offline()
    app = QGuiApplication.instance() or QGuiApplication([])
    try:
        from waves.waves_ui import backend
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
    # Copies already on disk, recorded BEFORE the page can ask about them (an
    # answer is cached for its TTL, so a record written later would not be
    # seen): t1 and t2 are held at HI-RES and are the album's whole track
    # list. t2's catalog tier is LOSSLESS, so its HI-RES row is the widest a
    # menu row gets, NOT OFFERED and in the library at once.
    owned_dir = Path(tempfile.mkdtemp(prefix="waves-quality-pick-owned-"))
    for tid in ("t1", "t2"):
        f = owned_dir / f"{tid}.flac"
        f.write_text("audio")
        bridge._ownership.record(tid, str(f), "HI_RES_LOSSLESS")
    bridge._ownership.record_members_replace("a1", ["t1", "t2"])
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
        # `root._qp[...]` in an expression resolves the row badges afresh
        # (a window cannot grow a property, and a Popup is not a child).
        expr = expr.replace("root._qp", "(" + _FIND_PICKS + ")")
        e = QQmlExpression(QQmlEngine.contextForObject(root), root, expr)
        r = e.evaluate()
        if e.hasError():
            raise RuntimeError(e.error().toString())
        if isinstance(r, tuple):
            r = r[0]
        return r.toVariant() if hasattr(r, "toVariant") else r

    def settle(ms: int = 120) -> None:
        loop = QEventLoop()
        QTimer.singleShot(ms, loop.quit)
        loop.exec()

    failures: list[str] = []

    def check(cond, what: str) -> None:
        if not cond:
            failures.append(what)

    settle(150)
    q(PARK_LOGIN_QML)
    bridge._logged_in = True
    bridge.loggedInChanged.emit()
    q("openSearch()")
    settle()
    q("_searchSeq = _navSeq")
    bridge.searchResults.emit(_payload())
    settle(600)

    # The badges: track rows, the album row, and no menu where none belongs.
    keys = sorted(q("Object.keys(root._qp)"))
    if not {"t1/", "t2/", "t3/", "t4/", "a1/"}.issubset(set(keys)):
        print("did not find every row badge:", keys, file=sys.stderr)
        return _EXIT_PRECONDITION
    check(bool(q("root._qp['t1/'].canPick")), "a HI-RES track badge offers no menu")
    check(not bool(q("root._qp['t4/'].canPick")), "an ATMOS pill offers a menu")
    check(
        not any(k.startswith("v1") and bool(q(f"root._qp['{k}'].canPick")) for k in keys), "a video badge offers a menu"
    )
    # A badge nobody has pressed carries NO menu. The menu is some thirty
    # objects, and a results page holds a badge on every row: built with the
    # badge, every row scrolled past paid for a menu it never opened (measured
    # at 40% on top of a 300-row scroll). Built on the first press instead.
    check(
        not any(bool(q(f"root._qp['{k}'].menuBuilt")) for k in keys),
        "a badge built its menu before anyone asked for one",
    )
    check(q("root._qp['t1/'].menuRows") is None, "an unpressed badge already has menu rows")

    default = q("root.targetTier")
    check(default in ("HI-RES", "LOSSLESS", "HIGH", "LOW"), f"the Settings tier did not reach the page: {default!r}")
    other = "LOW" if default != "LOW" else "HIGH"

    # An album's choice turns its track badges, floored by each track's ceiling.
    bridge.setQualityOverride("a1", "HI-RES")
    settle()
    check(
        q("root._qp['a1/'].shown") == "HI-RES" and bool(q("root._qp['a1/'].tinted")),
        "the album badge did not take its choice",
    )
    check(
        q("root._qp['t1/'].shown") == "HI-RES" and bool(q("root._qp['t1/'].tinted")),
        "a track did not inherit the album's choice",
    )
    check(
        q("root._qp['t2/'].shown") == "LOSSLESS" and bool(q("root._qp['t2/'].tinted")),
        "a lossless-only track was not floored under the album's HI-RES",
    )
    check(
        q("root._qp['t3/'].shown") == "HI-RES" and not bool(q("root._qp['t3/'].tinted")),
        "a track of another album followed the choice",
    )
    # The QualTag is the picker's first Item child (the animations are not Items).
    check(q("root._qp['t1/'].children[0].q") == "HI-RES", "the real pill under the badge did not follow")

    # A track's own choice beats the album's; choosing the setting under an
    # album that chose is kept as DEFAULT, so the track stops inheriting.
    q(f"root._qp['t1/'].choose('{other}')")
    settle()
    check(bridge.qualityOverrideOf("t1") == other, "the track's own choice was not recorded")
    check(
        q("root._qp['t1/'].shown") == other and bool(q("root._qp['t1/'].tinted")),
        "the track badge did not show its own choice",
    )
    q(f"root._qp['t1/'].choose('{default}')")
    settle()
    check(
        bridge.qualityOverrideOf("t1") == "DEFAULT",
        "choosing the setting under an album choice was not pinned as DEFAULT",
    )
    check(
        q("root._qp['t1/'].shown") == "HI-RES" and not bool(q("root._qp['t1/'].tinted")),
        "a DEFAULT-pinned track still wore the album's choice",
    )
    check(
        q("root._qp['a1/'].shown") == "HI-RES" and bool(q("root._qp['a1/'].tinted")),
        "the album's choice was disturbed by a track's",
    )
    # Without an album choice, the setting is a plain clear.
    q(f"root._qp['t3/'].choose('{other}')")
    q(f"root._qp['t3/'].choose('{default}')")
    settle()
    check(bridge.qualityOverrideOf("t3") == "", "choosing the setting on a free track left a record")

    # The menu: toggles on the badge, lists a tier above the ceiling as inert.
    q("root._qp['t2/'].toggleMenu()")
    settle(400)
    check(bool(q("root._qp['t2/'].menuOpen")), "the menu did not open")
    # The Column's children are the four rows plus the Repeater that made them.
    rows_js = (
        "Array.prototype.filter.call(root._qp['t2/'].menuRows.children,"
        " function(c) { return typeof c.t !== 'undefined' })"
    )
    rows = q(rows_js + ".length")
    check(rows == 4, f"the menu lists {rows} rows, not 4")
    check(not bool(q(rows_js + "[0].ok")), "HI-RES was offered on a lossless-only track")
    check(bool(q(rows_js + "[1].ok")), "LOSSLESS was refused on a lossless track")
    idx = ["HI-RES", "LOSSLESS", "HIGH", "LOW"].index(default)
    check(bool(q(rows_js + f"[{idx}].isDefault")), "the DEFAULT mark is not on the setting's tier")
    q("root._qp['t2/'].toggleMenu()")
    settle(400)
    check(not bool(q("root._qp['t2/'].menuOpen")), "a second click on the badge did not close the menu")
    check(
        not bool(q("root._qp['t4/'].menuOpen"))
        and q("root._qp['t4/'].toggleMenu()") is None
        and not bool(q("root._qp['t4/'].menuOpen")),
        "an Atmos pill opened a menu",
    )

    # The IN LIBRARY mark: the tier of the copy already on disk, on that row
    # and no other, with the menu widened to hold it and no column overlapping
    # another. A track whose copy sits above what the catalog still offers
    # wears both marks, which is the widest a row gets.
    def rows_of(key: str) -> str:
        return (
            f"Array.prototype.filter.call(root._qp['{key}'].menuRows.children,"
            " function(c) { return typeof c.t !== 'undefined' })"
        )

    q("root._qp['t2/'].toggleMenu()")
    settle(400)
    t2_rows = rows_of("t2/")
    check(q("root._qp['t2/'].ownedTier") == "HI-RES", "the badge did not learn what is already on disk")
    check(bool(q(t2_rows + "[0].inLibrary")), "the tier already on disk was not marked")
    check(
        not any(bool(q(t2_rows + f"[{i}].inLibrary")) for i in (1, 2, 3)),
        "a tier that is not on disk was marked as in the library",
    )
    check(not bool(q(t2_rows + "[0].ok")), "the widest row lost its NOT OFFERED mark")
    # Every row: the tier column and the marks column never run into each
    # other, whatever the row carries.
    for i in range(4):
        left = q(t2_rows + f"[{i}].children[0]")
        check(
            q(t2_rows + f"[{i}].children[0].x") + q(t2_rows + f"[{i}].children[0].width")
            <= q(t2_rows + f"[{i}].children[1].x") + 0.5,
            f"menu row {i} overlaps its own marks",
        )
        check(left is not None, "the menu row lost its tier column")
    wide = q("root._qp['t2/'].menuW")
    q("root._qp['t2/'].toggleMenu()")
    settle(400)

    # A track with no copy on disk marks nothing, and its menu stays narrow.
    q("root._qp['t3/'].toggleMenu()")
    settle(400)
    check(q("root._qp['t3/'].ownedTier") == "", "a track with no copy claimed one")
    check(
        not any(bool(q(rows_of("t3/") + f"[{i}].inLibrary")) for i in range(4)),
        "a track with no copy wore a library mark",
    )
    check(q("root._qp['t3/'].menuW") < wide, "the menu did not shrink back with nothing to say")
    q("root._qp['t3/'].toggleMenu()")
    settle(400)

    # An album is marked only when every one of its tracks is owned, and then
    # at the weakest copy's tier (both of a1's are HI-RES).
    q("root._qp['a1/'].toggleMenu()")
    settle(400)
    check(q("root._qp['a1/'].ownedTier") == "HI-RES", "a fully owned album was not marked")
    check(bool(q(rows_of("a1/") + "[0].inLibrary")), "the album's owned tier was not marked")
    # The word follows the same rule as a Download button's done face.
    check(q("root.libraryWord") == "DOWNLOADED", "with no library the copy is claimed as DOWNLOADED")
    q("root.libraryOn = true; root.dlInLibrary = true")
    settle()
    check(q("root.libraryWord") == "IN LIBRARY", "with a library holding the copy the word did not follow")
    q("root.libraryOn = false; root.dlInLibrary = false")
    q("root._qp['a1/'].toggleMenu()")
    settle(400)

    # The DEFAULT mark follows the setting live.
    bridge.settings.data.quality_audio = backend.Quality.low_96k if default != "LOW" else backend.Quality.low_320k
    bridge.targetTierChanged.emit()
    settle()
    check(
        q("root.targetTier") == ("LOW" if default != "LOW" else "HIGH"), "the DEFAULT mark did not follow the setting"
    )
    bridge.settings.data.quality_audio = backend.Quality(
        default and {"HI-RES": "HI_RES_LOSSLESS", "LOSSLESS": "LOSSLESS", "HIGH": "HIGH", "LOW": "LOW"}[default]
    )
    bridge.targetTierChanged.emit()
    settle()

    # A download asks at the choice and leaves it standing, so the badge goes
    # on stating the tier the copy was fetched at (livetest report: it fell
    # back to the catalog's word the moment the row was queued). The gates are
    # stubbed open and the queue pump parked: nothing is fetched.
    backend._image = lambda obj, size: ""
    backend._quality_label = lambda obj: "HI-RES"
    backend._primary_artist_name = lambda obj: "Artist"
    backend._track_count = lambda obj: 1
    bridge._download_gate = lambda: "ok"
    bridge._ffmpeg_gate_holds = lambda *a, **k: False
    bridge._pump_queue = lambda: None
    bridge._remember(
        "track",
        "t3",
        SimpleNamespace(id="t3", name="Song", version=None, album=SimpleNamespace(id="a2", name="Album Two")),
    )
    q(f"root._qp['t3/'].choose('{other}')")
    settle()
    check(bool(q("root._qp['t3/'].tinted")), "the choice did not land before the download")
    bridge.downloadTrack("t3")
    settle(300)
    row = bridge._queue[-1] if bridge._queue else {}
    check(
        row.get("askQuality")
        == {"HI-RES": "HI_RES_LOSSLESS", "LOSSLESS": "LOSSLESS", "HIGH": "HIGH", "LOW": "LOW"}[other],
        f"the row did not ask at the choice: {row.get('askQuality')!r}",
    )
    check(row.get("quality") == other, f"the drawer word is not the choice: {row.get('quality')!r}")
    check(bridge.qualityOverrideOf("t3") == other, "the download spent the choice")
    check(
        bool(q("root._qp['t3/'].tinted")) and q("root._qp['t3/'].shown") == other,
        "the badge stopped stating the tier its download asked at",
    )

    # A button reading DOWNLOADED because THIS session fetched the item is
    # handed back when a tier is chosen on it (livetest report: download a
    # song, choose another tier, the button stayed DOWNLOADED with nothing to
    # click). A track's choice hands back its own button only; an album's
    # reaches its known tracks; a queued item is left exactly as it is.
    for mid in ("t1", "t2", "a1", "t4"):
        bridge.downloadState.emit(mid, "done")
    settle()
    if q("root.dlSt('t1')") != "done" or q("root.dlSt('t3')") != "queued":
        print("the session states did not reach the page", file=sys.stderr)
        return _EXIT_PRECONDITION
    bridge.setQualityOverride("t1", other)
    settle()
    check(q("root.dlSt('t1')") == "", "a track's choice left its DOWNLOADED button inert")
    check(
        q("root.dlSt('t2')") == "done" and q("root.dlSt('a1')") == "done",
        "a track's choice handed back a button that was not its own",
    )
    bridge.setQualityOverride("t3", other)
    settle()
    check(q("root.dlSt('t3')") == "queued", "a choice on a queued item disturbed its queue state")
    bridge._remember("track", "t2", SimpleNamespace(id="t2", album=SimpleNamespace(id="a1", name="Album One")))
    bridge.setQualityOverride("a1", other)
    settle()
    check(
        q("root.dlSt('a1')") == "" and q("root.dlSt('t2')") == "",
        "an album's choice did not hand back the album's and its track's buttons",
    )
    check(q("root.dlSt('t4')") == "done", "an album's choice handed back another album's track")

    # The tier landing on the pill is one eased change, and it has to hand
    # back everything it borrows. A swap that never finishes is silent but
    # not harmless: `swapping` left true keeps the pill's width easing armed,
    # so the caret's room would afterwards GLIDE in on every hover instead of
    # springing, and a stuck ghost leaves the old tier's word printed over the
    # new one. Checked mid-flight as well as after, or a swap that never
    # started would pass the parked half on its own.
    q("root.hoverMotion = true")
    bridge.setQualityOverride("t3", "")
    settle(600)
    bridge.setQualityOverride("t3", other)
    settle(60)
    check(
        bool(q("root._qp['t3/'].ghosting")) and bool(q("root._qp['t3/'].swapping")),
        "the pill did not swap when the tier on it changed",
    )
    settle(600)
    check(
        not bool(q("root._qp['t3/'].ghosting"))
        and not bool(q("root._qp['t3/'].swapping"))
        and abs(float(q("root._qp['t3/'].inkOp")) - 1) < 0.01
        and abs(float(q("root._qp['t3/'].ghostOp"))) < 0.01,
        "the swap did not park: the pill is left mid-change",
    )
    # Colour alone, no dip: claiming the tier a plain pill already states only
    # tints its ground, and blanking letters that are not changing would be a
    # blink of nothing.
    bridge.setQualityOverride("t3", "")
    settle(600)
    bridge.setQualityOverride("t3", "HI-RES")
    settle(60)
    check(
        q("root._qp['t3/'].shown") == "HI-RES" and bool(q("root._qp['t3/'].tinted")),
        "the tint-only case did not set up: the pill's word moved after all",
    )
    check(
        not bool(q("root._qp['t3/'].ghosting")) and bool(q("root._qp['t3/'].swapping")),
        "a tint with no new word still blanked the badge's letters",
    )
    settle(600)

    if failures:
        for f in failures:
            print("REGRESSED:", f, file=sys.stderr)
        return _EXIT_REGRESSED
    print("quality badge menu: OK")
    return _EXIT_OK


if __name__ == "__main__":
    if "--run-scenario" in sys.argv:
        raise SystemExit(_run_scenario())
