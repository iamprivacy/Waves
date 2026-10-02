"""The ATMOS badge on a release that carries Dolby Atmos beside stereo.

WHAT THIS FENCES OFF
--------------------
TIDAL ships new releases with stereo and Atmos under ONE id. With "Download
Dolby Atmos" on, such a release downloads in Atmos, so its badge says ATMOS
SPATIAL (the same pill a separate Atmos edition wears) instead of the stereo
tier, and says it the moment the setting flips. The quality menu stays: it
leads with ATMOS, marked DEFAULT and checked, then the four stereo tiers,
still offered. Choosing one is a real choice even when it is the Settings
tier (it is how the item is fetched in stereo), choosing ATMOS again clears
it, and under an album's stereo choice it is pinned as DEFAULT. A copy held
in Atmos marks the ATMOS row with the library word. A release with stereo
only, and an Atmos-only one (nothing else to pick), are untouched; with the
setting off the release reads its stereo tier again, and its menu still
offers ATMOS as a real choice (the DEFAULT row is then the Settings tier,
floored to what the release can land, so an ATMOS choice can always be taken
back). A MIXED dual album reads ATMOS, not MIXED, while the setting is on.
The setting flips through applySettings, the Settings page's own path.

Runs in a SUBPROCESS like the other Main.qml scenarios: building the bridge
installs process-global handlers that must not leak into the suite.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

_EXIT_OK = 0
_EXIT_REGRESSED = 1
_EXIT_NO_QT = 77
_EXIT_PRECONDITION = 78

QML_MAIN = Path(__file__).resolve().parent.parent / "waves" / "waves_ui" / "qml" / "Main.qml"


def test_a_release_carrying_atmos_reads_atmos_and_keeps_its_stereo_tiers():
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    # Both roots sandboxed: the bridge resolves its config under one or the
    # other by platform, and the scenario's owned copies land under it too,
    # so one sweep clears everything the run wrote.
    sandbox = tempfile.mkdtemp(prefix="waves-atmos-badge-test-")
    env["XDG_CONFIG_HOME"] = sandbox
    env["HOME"] = sandbox
    try:
        proc = subprocess.run(  # (fixed argv: this file, one flag)
            [sys.executable, str(Path(__file__).resolve()), "--run-scenario"],
            env=env,
            capture_output=True,
            text=True,
            timeout=180,
        )
    finally:
        shutil.rmtree(sandbox, ignore_errors=True)
    tail = "\n".join((proc.stdout + proc.stderr).strip().splitlines()[-16:])
    import pytest

    if proc.returncode == _EXIT_NO_QT:
        pytest.skip("PySide6 / offscreen Qt unavailable")
    if proc.returncode == _EXIT_PRECONDITION:
        pytest.skip(f"could not set up the scenario in this environment:\n{tail}")
    assert proc.returncode == _EXIT_OK, f"the ATMOS badge regressed. Scenario exit={proc.returncode}:\n{tail}"


_ROW = {
    "artist": "Lab Artist",
    "artist_id": "",
    "artists": [],
    "art": "",
    "year": "2026",
    "date": "2026-05-08",
    "explicit": False,
    "added": "",
    "popularity": 50,
    "num": 1,
    "vol": 1,
    "duration": "3:00",
    "duration_sec": 180,
}


def _payload() -> dict:
    tracks = [
        # Stereo and Atmos under one id: the case the badge is for.
        dict(_ROW, id="t5", title="Dual song", album="Dual Album", album_id="a5", quality="HI-RES", atmos=True),
        # Stereo only.
        dict(_ROW, id="t6", title="Stereo song", album="Other", album_id="a6", quality="HI-RES", atmos=False),
        # A separate Atmos edition's track: nothing but Atmos.
        dict(_ROW, id="t7", title="Atmos song", album="Spatial", album_id="a7", quality="ATMOS", atmos=True),
        # Stereo and Atmos under one id, with no hi-res master: the Settings
        # tier (Max) is NOT OFFERED here, so the DEFAULT row is the floored one.
        dict(_ROW, id="t8", title="Lossless dual", album="Dual Low", album_id="a8", quality="LOSSLESS", atmos=True),
    ]
    albums = [dict(_ROW, id="a5", title="Dual Album", tracks=1, quality="HI-RES", atmos=True)]
    return {"artists": [], "albums": albums, "tracks": tracks, "videos": [], "playlists": [], "mixes": []}


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
                out[c.mediaId] = c;
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
    bridge.settings.data.download_dolby_atmos = False
    # t5 is held in Dolby Atmos, recorded before the page can ask (an answer
    # is cached for its TTL). TIDAL files Atmos under LOW: the mark must land
    # on the ATMOS row, not on LOW.
    owned_dir = Path(tempfile.mkdtemp(prefix="waves-atmos-badge-owned-", dir=os.environ.get("XDG_CONFIG_HOME")))
    bridge.settings.data.download_base_path = str(owned_dir)
    f = owned_dir / "t5.m4a"
    f.write_text("audio")
    bridge._ownership.record("t5", str(f), "LOW", audio_mode="DOLBY_ATMOS")
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

    def atmos(on: bool) -> None:
        # Through the Settings page's own path: the save is what turns the
        # badges (atmosOnChanged) and re-asks the buttons (ownershipChanged).
        bridge.applySettings({"download_dolby_atmos": on})
        settle()

    def rows_of(key: str) -> str:
        return (
            f"Array.prototype.filter.call(root._qp['{key}'].menuRows.children,"
            " function(c) { return typeof c.t !== 'undefined' })"
        )

    settle(150)
    q(PARK_LOGIN_QML)
    bridge._logged_in = True
    bridge.loggedInChanged.emit()
    q("openSearch()")
    settle()
    q("_searchSeq = _navSeq")
    bridge.searchResults.emit(_payload())
    settle(600)
    keys = set(q("Object.keys(root._qp)"))
    if not {"t5", "t6", "t7", "t8", "a5"}.issubset(keys):
        # A badge missing from a result the page did paint IS a regression
        # (a row that lost its pill), never an environment shortfall.
        print("REGRESSED: did not find every row badge:", sorted(keys), file=sys.stderr)
        return _EXIT_REGRESSED

    # Setting off: the release states its stereo tier, and its menu still
    # offers ATMOS (a stereo-only release's does not).
    check(q("root._qp['t5'].shown") == "HI-RES", "with Atmos off the dual release did not read its stereo tier")
    check(q("root._qp['t5'].tiers.length") == 5, "with Atmos off the menu lost its ATMOS row")
    check(q("root._qp['t6'].tiers.length") == 4, "a stereo-only release grew an ATMOS row")

    # Setting on: ATMOS on the real pill, live, for the track and the album.
    atmos(True)
    for key in ("t5", "a5"):
        check(q(f"root._qp['{key}'].shown") == "ATMOS", f"{key} did not read ATMOS with the setting on")
        check(q(f"root._qp['{key}'].children[0].q") == "ATMOS", f"the real pill under {key} did not say ATMOS")
        check(not bool(q(f"root._qp['{key}'].tinted")), f"{key} reads as a choice with none made")
        check(bool(q(f"root._qp['{key}'].canPick")), f"{key} lost its quality menu")
    check(q("root._qp['t6'].shown") == "HI-RES", "a stereo-only release turned ATMOS")
    check(q("root._qp['t7'].shown") == "ATMOS", "the Atmos-only release lost its ATMOS pill")
    check(not bool(q("root._qp['t7'].canPick")), "an Atmos-only release grew a menu")

    # The menu: ATMOS first, DEFAULT and checked, then every stereo tier.
    q("root._qp['t5'].toggleMenu()")
    settle(400)
    check(bool(q("root._qp['t5'].menuOpen")), "the menu did not open on an ATMOS badge")
    rows = rows_of("t5")
    tiers = [q(rows + f"[{i}].t") for i in range(int(q(rows + ".length")))]
    check(tiers == ["ATMOS", "HI-RES", "LOSSLESS", "HIGH", "LOW"], f"the menu lists {tiers}")
    check(bool(q(rows + "[0].isDefault")) and bool(q(rows + "[0].isCurrent")), "ATMOS is not the default, checked row")
    check(all(bool(q(rows + f"[{i}].ok")) for i in range(5)), "a row was listed NOT OFFERED")
    check(
        not any(bool(q(rows + f"[{i}].isDefault")) for i in range(1, 5)),
        "a stereo tier was marked DEFAULT under the ATMOS badge",
    )
    check(q("root._qp['t5'].ownedTier") == "ATMOS", "the Atmos copy on disk was not answered as ATMOS")
    check(bool(q(rows + "[0].inLibrary")), "the ATMOS row did not carry the library word")
    check(
        not any(bool(q(rows + f"[{i}].inLibrary")) for i in range(1, 5)),
        "a stereo row was marked as held (the Atmos copy's LOW filing tier)",
    )
    for i in range(5):
        check(
            q(rows + f"[{i}].children[0].x") + q(rows + f"[{i}].children[0].width")
            <= q(rows + f"[{i}].children[1].x") + 0.5,
            f"menu row {i} overlaps its own marks",
        )
    q("root._qp['t5'].toggleMenu()")
    settle(400)

    # The Settings tier is a real choice here: stereo at that tier.
    default = q("root.targetTier")
    q(f"root._qp['t5'].choose('{default}')")
    settle()
    check(bridge.qualityOverrideOf("t5") == default, "choosing the Settings tier under ATMOS was taken as no choice")
    check(
        q("root._qp['t5'].shown") == default and bool(q("root._qp['t5'].tinted")),
        "the stereo choice did not show on the badge",
    )
    q("root._qp['t5'].choose('ATMOS')")
    settle()
    check(bridge.qualityOverrideOf("t5") == "", "choosing ATMOS did not clear the choice")
    check(q("root._qp['t5'].shown") == "ATMOS", "the badge did not return to ATMOS")

    # Under an album's stereo choice the track follows it, and ATMOS on the
    # track is kept as DEFAULT so the album's choice stops reaching it.
    bridge.setQualityOverride("a5", "LOSSLESS")
    settle()
    check(q("root._qp['a5'].shown") == "LOSSLESS", "the album's stereo choice did not show")
    check(q("root._qp['t5'].shown") == "LOSSLESS", "the track did not follow its album's stereo choice")
    q("root._qp['t5'].choose('ATMOS')")
    settle()
    check(bridge.qualityOverrideOf("t5") == "DEFAULT", "ATMOS under an album choice was not pinned as DEFAULT")
    check(q("root._qp['t5'].shown") == "ATMOS", "a DEFAULT-pinned track did not read ATMOS")
    bridge.setQualityOverride("a5", "")
    bridge.setQualityOverride("t5", "")
    settle()

    # Setting off again: back to the stereo tier, live.
    atmos(False)
    check(q("root._qp['t5'].shown") == "HI-RES", "turning Atmos off left the ATMOS badge up")
    check(q("root._qp['a5'].shown") == "HI-RES", "turning Atmos off left the album's ATMOS badge up")

    # Setting off, the menu: the Settings tier is DEFAULT and checked, ATMOS
    # first and on offer, and choosing it is a real choice held as ATMOS.
    q("root._qp['t5'].toggleMenu()")
    settle(400)
    rows = rows_of("t5")
    tiers = [q(rows + f"[{i}].t") for i in range(int(q(rows + ".length")))]
    check(tiers == ["ATMOS", "HI-RES", "LOSSLESS", "HIGH", "LOW"], f"with Atmos off the menu lists {tiers}")
    check(bool(q(rows + "[0].ok")), "ATMOS was listed NOT OFFERED with the setting off")
    check(not bool(q(rows + "[0].isDefault")) and not bool(q(rows + "[0].isCurrent")), "ATMOS read as the default")
    idx = tiers.index(default)
    check(bool(q(rows + f"[{idx}].isDefault")), "the Settings tier lost DEFAULT with Atmos off")
    q("root._qp['t5'].toggleMenu()")
    settle(400)
    q("root._qp['t5'].choose('ATMOS')")
    settle()
    check(bridge.qualityOverrideOf("t5") == "ATMOS", "choosing ATMOS with the setting off was not kept")
    check(
        q("root._qp['t5'].shown") == "ATMOS" and bool(q("root._qp['t5'].tinted")),
        "the ATMOS choice did not show on the badge",
    )
    check(q("root._qp['t5'].children[0].q") == "ATMOS", "the real pill did not say ATMOS after the choice")
    q(f"root._qp['t5'].choose('{default}')")
    settle()
    check(bridge.qualityOverrideOf("t5") == "", "choosing the Settings tier did not clear the ATMOS choice")
    check(q("root._qp['t5'].shown") == "HI-RES", "the badge did not return to its stereo tier")

    # An album's ATMOS choice reaching a track with no Atmos is no choice there.
    bridge.setQualityOverride("a6", "ATMOS")
    settle()
    check(q("root._qp['t6'].shown") == "HI-RES", "a stereo-only track took its album's ATMOS choice")
    check(not bool(q("root._qp['t6'].tinted")), "a stereo-only track read an ATMOS choice as its own")
    bridge.setQualityOverride("a6", "")
    settle()

    # A dual release the Settings tier cannot land (Max on a Lossless-only
    # master), setting off: the DEFAULT row is the floored tier, Max reads
    # NOT OFFERED, and choosing that DEFAULT row takes an ATMOS choice back.
    # On the bare Settings tier nothing was DEFAULT and every row on offer
    # stored a word, so an ATMOS choice here could not be undone.
    floored = q("root.tierFloor(root.targetTier, 'LOSSLESS')")
    check(q("root._qp['t8'].shown") == "LOSSLESS", "the lossless dual release did not read its stereo tier")
    check(q("root._qp['t8'].defaultTier") == floored, "the DEFAULT row is not the floored Settings tier")
    check(bool(q("root._qp['t8'].anyNotOffered")), "Max was listed as offered on a Lossless-only release")
    q("root._qp['t8'].toggleMenu()")
    settle(400)
    rows = rows_of("t8")
    tiers = [q(rows + f"[{i}].t") for i in range(int(q(rows + ".length")))]
    check(tiers[0] == "ATMOS" and bool(q(rows + "[0].ok")), "the lossless dual release lost its ATMOS row")
    check(not bool(q(rows + f"[{tiers.index('HI-RES')}].ok")), "HI-RES was offered with no hi-res master")
    check(bool(q(rows + f"[{tiers.index(floored)}].isDefault")), "the floored tier did not carry the DEFAULT mark")
    check(bool(q(rows + f"[{tiers.index(floored)}].isCurrent")), "the floored tier is not the checked row")
    q("root._qp['t8'].toggleMenu()")
    settle(400)
    q("root._qp['t8'].choose('ATMOS')")
    settle()
    check(bridge.qualityOverrideOf("t8") == "ATMOS", "choosing ATMOS on the lossless dual release was not kept")
    check(q("root._qp['t8'].shown") == "ATMOS" and bool(q("root._qp['t8'].tinted")), "the ATMOS choice did not show")
    q(f"root._qp['t8'].choose('{floored}')")
    settle()
    check(bridge.qualityOverrideOf("t8") == "", "the floored DEFAULT row did not take the ATMOS choice back")
    check(q("root._qp['t8'].shown") == "LOSSLESS", "the badge did not return to its stereo tier")
    # Setting on: the floored tier is a real stereo choice there, ATMOS clears.
    atmos(True)
    check(q("root._qp['t8'].shown") == "ATMOS", "the lossless dual release did not read ATMOS with the setting on")
    q(f"root._qp['t8'].choose('{floored}')")
    settle()
    check(bridge.qualityOverrideOf("t8") == floored, "the stereo tier under an ATMOS face was taken as no choice")
    q("root._qp['t8'].choose('ATMOS')")
    settle()
    check(bridge.qualityOverrideOf("t8") == "", "choosing ATMOS under the ATMOS face did not clear the choice")

    # A MIXED dual album (its tracks span tiers): with the setting on the
    # face is ATMOS, not MIXED, and the real pill draws no tier list; a
    # stereo choice replaces the whole face; with the setting off the
    # untinted album is MIXED again.
    q("root._qp['a5'].mix = [{q: 'HI-RES'}, {q: 'LOSSLESS'}]")
    settle()
    check(q("root._qp['a5'].face") == "ATMOS", "a MIXED dual album did not read ATMOS with the setting on")
    check(int(q("root._qp['a5'].children[0].mix.length")) == 0, "the ATMOS face still drew the tier list")
    bridge.setQualityOverride("a5", "LOSSLESS")
    settle()
    check(
        q("root._qp['a5'].face") == "LOSSLESS" and bool(q("root._qp['a5'].tinted")),
        "a stereo choice did not replace the MIXED face",
    )
    bridge.setQualityOverride("a5", "")
    atmos(False)
    check(q("root._qp['a5'].face") == "MIXED", "with the setting off the dual album did not read MIXED again")
    check(int(q("root._qp['a5'].children[0].mix.length")) == 2, "the MIXED face lost its tier list")
    q("root._qp['a5'].mix = []")
    settle()

    if failures:
        for f in failures:
            print("REGRESSED:", f, file=sys.stderr)
        return _EXIT_REGRESSED
    print("atmos badge: OK")
    return _EXIT_OK


if __name__ == "__main__":
    if "--run-scenario" in sys.argv:
        raise SystemExit(_run_scenario())
