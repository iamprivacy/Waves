"""Source-level pins for the 2026-09-17 front-end audit fix pass.

Each of these was confirmed at runtime in an offscreen harness before the fix
and after it. The runtime scenarios are expensive (one Main.qml boot each),
so what is pinned here is the SHAPE of each fix in the source: the line a
tidy-up would most plausibly fold back into the defect.
"""

from __future__ import annotations

import pathlib
import re

QML = pathlib.Path(__file__).resolve().parent.parent / "waves" / "waves_ui" / "qml"
MAIN = (QML / "Main.qml").read_text()
SETTINGS = (QML / "SettingsPage.qml").read_text()
APP = (pathlib.Path(__file__).resolve().parent.parent / "waves" / "waves_ui" / "app.py").read_text()
BACKEND = (pathlib.Path(__file__).resolve().parent.parent / "waves" / "waves_ui" / "backend.py").read_text()


def _block(src: str, start: str, end: str) -> str:
    a = src.index(start)
    return src[a : src.index(end, a)]


def test_logout_resets_the_shelf_growing_latches_and_the_library_sort():
    logout = _block(MAIN, "root.navHistory = []\n            root.navForwardHistory = []", "function onBrowseLoaded")
    assert "root.libSort = ({})" in logout
    assert "root.browseGrowing = ({})" in MAIN[: MAIN.index("function onBrowseLoaded")]


def test_a_copy_finished_this_session_reaches_the_redownload_gate():
    assert 'canRedownload: (liveSt === "" || liveSt === "done") && owned' in MAIN


def test_an_album_without_its_track_list_asks_by_album_id():
    btn = _block(MAIN, "collectionIds: ab.trackList.length > 0", "onTap:")
    assert btn.rstrip().endswith("collectionCheck: true") or "collectionCheck: true" in btn
    assert ": null" in btn.splitlines()[0]


def test_back_into_the_open_category_still_revalidates():
    lib = _block(MAIN, 'else if (s.v === "library") {', 'else if (s.v === "artist")')
    assert "loadLib(s.cat)" in lib
    assert "libraryCategory !== s.cat" not in lib


def test_live_diagnostics_toggles_never_mark_the_page_dirty():
    assert "function setLive(key, v)" in SETTINGS
    for key in ("verbose_diagnostics", "diagnostics_redact_content"):
        assert f'page.setLive("{key}", v)' in SETTINGS
        assert f'page.setv("{key}", v)' not in SETTINGS


def test_the_card_registry_counts_its_holders():
    reg = _block(MAIN, "function ownCardRegister(cid, keys)", "function ownCardsBatch")
    assert "e.n++" in reg
    assert "if (--e.n <= 0) delete _ownCards[cid]" in reg
    assert "function ownCardRekey" in reg
    assert 'if (_ownHeld !== "") root.ownCardForget(_ownHeld)' in MAIN
    assert MAIN.count('Component.onDestruction: if (_ownHeld !== "") root.ownCardForget(_ownHeld)') == 2


def test_the_down_icon_holds_its_glyph_while_the_store_is_answering():
    di = _block(MAIN, "component DownIcon:", "component PreviewArt:")
    assert "property bool ownPending: false" in di
    assert "ownPending = o.pending === true" in di
    assert '&& !(di.ownPending && di.st === "")' in di
    assert "collectionCheck" not in di  # the never-true collection path is gone


def test_the_browse_card_carries_the_ownership_rollup():
    bc = _block(MAIN, "component BrowseCard:", "component ArtCard:")
    assert "function applyOwn(co)" in bc
    assert "function refreshOwned(live)" in bc
    assert 'readonly property bool dlDone: bc.dlSt === "done" || (bc.dlSt === "" && bc.owned)' in bc
    assert "if (bc.dlDone) { root.openRedownloadGate(bc.card); return }" in bc
    assert 'text: bc.dlDone ? "DONE"' in bc


def test_a_collapsed_completed_row_builds_no_body():
    i = re.search(r"\n\s*id: qrow\n", MAIN).start()
    body = MAIN[i : i + 12000]
    assert "id: qBody" in body
    assert "active: !qrow.collapsed || qrow.height > 0" in body, "alive until the collapse has animated shut"
    assert "property real bodyH: qBody.item ? qBody.item.height : 0" in body


def test_hover_driven_sizes_ease_and_never_spring():
    """G2: the quality pill's extraW, the menu row scale and the Browse tile
    tilt were OutBack, which overshoots under a resting pointer."""
    assert (
        "Behavior on extraW { NumberAnimation { duration: root.hoverMotion ? 220 : 0; easing.type: Easing.OutCubic } }"
        in MAIN
    )
    assert (
        "Behavior on scale { NumberAnimation { duration: root.hoverMotion ? 140 : 0; easing.type: Easing.OutCubic } }"
        in MAIN
    )
    for prop in ("fxRx", "fxRy", "fxLift"):
        assert not re.search(r"Behavior on %s\s*\{[^}]*OutBack" % prop, MAIN), prop


def test_the_video_quality_pick_leaves_the_gui_thread():
    """G34: the save and the downloader rebuild run on a worker."""
    fn = _block(BACKEND, "def setVideoQuality(", "def _video_album_fallback(")
    assert "self.threadpool.start(Worker(work))" in fn
    assert "self._save_settings()" in fn and "self._init_download()" in fn
    before_worker = re.sub(r"#[^\n]*", "", fn.split("def work()")[0])
    assert "_save_settings" not in before_worker and "_init_download" not in before_worker


def test_the_hidden_artist_grid_is_not_built_on_a_type_chip():
    """Gated on the section condition, never on artistFlow.visible: that is
    EFFECTIVE visibility, false while an artist, My Tidal, Browse or Settings
    page covers the results, and it tore the grid down on every navigation
    and rebuilt every card synchronously on Back."""
    assert ('active: !root.searchArtistsStripMode && root.sectionVisible("artists", artistsModel.count)') in MAIN
    assert not re.search(
        r"^[^/\n]*artistFlow\.visible", MAIN, re.M
    ), "no code line may read the Flow's effective visibility"


def test_the_roll_arm_walk_waits_for_the_height_to_settle():
    assert "Timer { id: bttArmSettle; interval: 60; onTriggered: btt.armRoll() }" in MAIN
    assert "function onContentHeightChanged() { btt.rollTick++; bttArmSettle.restart() }" in MAIN


def test_metric_rows_hold_no_vector_shapes():
    for row in ("dbMetric", "dbMetricDone", "dbMetricLib", "dbMetricLibTrack"):
        block = _block(MAIN, f"id: {row}\n", "        }\n")
        assert "Ico {" not in block, row


def test_the_update_toast_and_the_ffmpeg_gate_ride_the_launch_dial():
    assert 'opacity: phase !== "" ? root.bootContentShown : 0' in MAIN
    gate = _block(MAIN, "id: ffmpegGate", "property bool sessionSnoozed")
    assert "visible: wanted && root.bootContentShown > 0" in gate
    assert "opacity: root.bootContentShown" in gate


def test_a_dead_glance_plays_the_upgrade():
    assert "function _videoCut(forcePlay)" in MAIN
    assert "var wasPaused = !forcePlay && peekPlayer.playbackState" in MAIN
    lost = _block(MAIN, "function _videoPromotionLost()", "videoUpgradeRetry.stop(); videoUpgrading = false")
    assert "_videoCut(true)" in lost


def test_the_browse_veil_counts_only_the_cards_it_joined():
    assert "function _browseCardTick(counted) { if (counted) _browseBuildTick() }" in MAIN
    assert MAIN.count("Component.onCompleted: counted = root._browseCardStart(asynchronous)") == 2
    assert MAIN.count("onLoaded: root._browseCardTick(counted)") == 2


def test_small_polish_pins():
    # a disabled sort control shows no pointing hand
    assert "cursorShape: enabled ? Qt.PointingHandCursor : Qt.ArrowCursor" in MAIN
    # only a FIRST terms acceptance marks the update prompt fresh
    assert "if (!legalSettings.termsAccepted) setupSettings.updatePromptFresh = true" in MAIN
    # the folder list disarms a pending restore on a drag like every other list
    lst = _block(MAIN, "id: libFolderList", "LibList {")
    assert "onMovementStarted: pendingY = -1" in lst
    # a tile off the pane's viewport does not rotate its covers
    assert "bt.visible && bt.inView && bt.arts.length" in MAIN
    # the idle LED grid's leading cell is not bound to the 20 Hz clock
    assert "pulsing: diGrid.visible && fillIndex === diGrid.lit" in MAIN


def test_dead_helpers_are_gone():
    for name in (
        "asciiBarDim",
        "popLit",
        "navForwardLabel",
        "navIdleBorderHi",
        "navIdleTextHi",
        "showNum",
        "StyledCombo",
    ):
        assert not re.search(r"\b%s\b" % name, MAIN), name
    assert "scrollViewport" not in SETTINGS


def test_the_icon_debug_log_never_touches_the_home_directory():
    fn = _block(APP, "def _icon_debug(", "def _app_icon(")
    assert "Path.home()" not in fn
    assert "diagnostics.scrub(msg)" in fn
    assert "diagnostics.log_path()" in fn


def test_icon_debug_lines_before_diagnostics_reach_the_file(tmp_path, monkeypatch):
    """The icon and AUMID lines run before the bridge installs diagnostics;
    they are held and written with the first line after it is installed."""
    from waves.waves_ui import app as app_mod

    # Through the app module's own reference: other tests pop the diagnostics
    # module from sys.modules, and app.py keeps the object it imported.
    diagnostics = app_mod.diagnostics
    monkeypatch.setattr(app_mod, "_icon_debug_pending", [])
    monkeypatch.setattr(diagnostics, "log_path", lambda: None)
    app_mod._icon_debug("WAVES icon: early line")
    assert not list(tmp_path.iterdir())
    monkeypatch.setattr(diagnostics, "log_path", lambda: tmp_path / "waves.log")
    app_mod._icon_debug("WAVES window: late line")
    text = (tmp_path / "waves-icon-debug.log").read_text(encoding="utf-8")
    assert text == "WAVES icon: early line\nWAVES window: late line\n"
    assert app_mod._icon_debug_pending == []


def test_a_failed_qml_load_shuts_the_bridge_down_and_the_exit_flushes_the_log():
    fail = _block(APP, "if not root_objects:", "return 1")
    assert "bridge.shutdown()" in fail
    assert "diagnostics.flush_disk_log()" in fail
    tail = _block(APP, "rc = app.exec()", "return rc")
    assert tail.index("diagnostics.flush_disk_log()") < tail.index("os._exit(rc)")
