"""Pins for the v0.1.31 audit, updater and backend-ui groups.

#1: a first failed Windows swap keeps its reason on the restart prompt.
#2: a failed FFmpeg promote on Windows moves the working binary back.
#3: brew's "Upgrading N outdated package" header does not read as Installing.
#4: a Homebrew failure shows fixed wording; the raw tail goes to the log.
#5: FFmpeg failures name the FFmpeg server and folder, and a Remove says so.
#13: a drop batch walks the queue model once to release holders.
#14: concurrent probe callers share the in-flight probe's answer.
#15: a discography held by the folder gate is not credited as failed.
#16: the max-age timers catch up once on a re-show after a long absence.
#17: an eviction clears only the side state of the cache it evicts from.
"""

from __future__ import annotations

import json
import logging
import os
import pathlib
import threading
import time
from threading import Event, Lock
from types import SimpleNamespace

import pytest
import requests

from tests.test_favorite_albums_artists_download_all import (
    _ArtistScanStub,
    _fav_artists_group,
    _Favorites,
    _Stub,
)
from waves.waves_ui import ffmpeg_manager as fm
from waves.waves_ui import updater as u
from waves.waves_ui.backend import _FAV_ARTISTS_GROUP_ID, WavesBridge
from waves.waves_ui.updater import AppUpdater, Release, UpdaterError, user_facing_error

QML_DIR = pathlib.Path(__file__).resolve().parents[1] / "waves" / "waves_ui" / "qml"
MAIN = (QML_DIR / "Main.qml").read_text(encoding="utf-8")
SETTINGS = (QML_DIR / "SettingsPage.qml").read_text(encoding="utf-8")
FFMGR = (QML_DIR / "FfmpegManager.qml").read_text(encoding="utf-8")


class _Signal:
    def __init__(self):
        self.calls: list = []

    def emit(self, *a):
        self.calls.append(a)


class _InlinePool:
    @staticmethod
    def start(worker):
        worker.fn()


def _bind(obj, *names):
    for name in names:
        setattr(obj, name, getattr(WavesBridge, name).__get__(obj, type(obj)))


# ---- #1: the first failed swap keeps its reason on screen ----------------------
def _resume_stub(pending):
    emitted: list = []
    stub = SimpleNamespace(
        _updater=SimpleNamespace(resume_pending_apply=lambda: pending),
        threadpool=_InlinePool(),
        _emit_from_worker=lambda name, *a: emitted.append((name, *a)),
    )
    _bind(stub, "resumePendingUpdate")
    return stub, emitted


def test_a_rearmed_failed_swap_shows_the_restart_prompt_without_a_failure_it_would_overwrite():
    stub, emitted = _resume_stub(
        {"version": "v2.0.0", "swap_failed": True, "rearmed": True, "message": "The update could not be applied."}
    )
    stub.resumePendingUpdate()
    assert emitted == [("appUpdatePending", "v2.0.0"), ("appUpdateStatusChanged",)]


def test_a_second_failed_swap_reports_the_failure_and_no_restart_prompt():
    stub, emitted = _resume_stub({"version": "v2.0.0", "swap_failed": True, "rearmed": False, "message": "Nope."})
    stub.resumePendingUpdate()
    assert emitted == [("appUpdateStateChanged", "failed", "Nope."), ("appUpdateStatusChanged",)]


def test_the_restart_prompt_reads_the_swap_failure():
    card = SETTINGS[SETTINGS.index('text: "Update failed: " + page.auMsg') :][:900]
    assert 'visible: page.auDone && (page.appUp.swap_failure || "") !== ""' in card
    assert '(page.appUp.swap_failure || "") + " Restart to try once more."' in card
    assert "textFormat: Text.PlainText" in card
    toast = MAIN[MAIN.index("function onAppUpdatePending(v) {\n                var st") :][:400]
    assert 'updateToast.swapFailure = st ? "" + (st.swap_failure || "") : ""' in toast
    assert 'updateToast.swapFailure + " Restart to try once more."' in MAIN


# ---- #2: a failed promote puts the working ffmpeg.exe back ----------------------
def test_a_failed_promote_moves_the_running_binary_back(tmp_path, monkeypatch):
    mgr = fm.FfmpegManager(tmp_path)
    mgr.os_key = "windows"
    mgr.install_dir.mkdir(parents=True)
    live = mgr.binary_path
    live.write_bytes(b"RUNNING")
    staged = mgr.install_dir / "ffmpeg.exe.abc.new"
    staged.write_bytes(b"NEW")
    real_replace = os.replace

    def replace(src, dst, *a, **k):
        if pathlib.Path(src) == staged:
            raise PermissionError(5, "Access is denied")
        return real_replace(src, dst, *a, **k)

    monkeypatch.setattr(fm.os, "replace", replace)
    with pytest.raises(PermissionError):
        mgr._swap_in(staged)
    assert live.read_bytes() == b"RUNNING"
    assert list(mgr.install_dir.glob("ffmpeg.exe.old-*")) == [], "nothing left for the sweep to delete"


# ---- #3 and #4: Homebrew phases and failure wording -----------------------------
class _FakeProc:
    def __init__(self, lines, code):
        self.stdout = iter(line + "\n" for line in lines)
        self._code = code

    def wait(self):
        return self._code

    def poll(self):
        return self._code

    def terminate(self):
        pass


def _brew(monkeypatch, lines, code=0):
    monkeypatch.setattr(u, "is_frozen", lambda: True)
    monkeypatch.setattr(u, "managed_channel", lambda: "homebrew-cask")
    monkeypatch.setattr(u, "_find_brew", lambda: "/opt/homebrew/bin/brew")
    monkeypatch.setattr(u.subprocess, "Popen", lambda *a, **k: _FakeProc(lines, code))
    up = AppUpdater("/tmp/x", "1.0.0", repo="owner/Waves")
    up.latest = lambda *a, **k: Release(version="v1.1.0", asset="a", url="u")
    return up


# The order a real `brew upgrade --cask` prints in: the "Upgrading" header
# comes first, before anything is downloaded.
_REAL_BREW = [
    "==> Upgrading 1 outdated package:",
    "iamprivacy/waves/waves 1.0.0 -> 1.1.0",
    "==> Upgrading waves",
    "==> Downloading https://github.com/iamprivacy/Waves/releases/download/v1.1.0/waves_macos.zip",
    "==> Downloading from https://objects.githubusercontent.com/github-production-release-asset/x",
    "######################################################################## 100.0%",
    "==> Backing App 'Waves.app' up to '/opt/homebrew/Caskroom/waves/1.0.0/Waves.app'",
    "==> Removing App '/Applications/Waves.app'",
    "==> Moving App 'Waves.app' to '/Applications/Waves.app'",
    "==> Purging files for version 1.0.0 of Cask waves",
    "🍺  waves was successfully upgraded!",
]


def test_a_real_brew_transcript_reads_downloading_before_installing(monkeypatch):
    up = _brew(monkeypatch, _REAL_BREW)
    logs, pcts = [], []
    assert up.install(progress_cb=pcts.append, log_cb=logs.append)["ok"] is True
    phases = [m for m in logs if m in ("Downloading", "Installing", "Finishing")]
    assert phases == ["Downloading", "Installing", "Finishing"]
    assert pcts == sorted(pcts)


def test_a_brew_failure_shows_fixed_wording_and_logs_the_tail(monkeypatch, caplog):
    lines = [
        "==> Downloading https://github.com/iamprivacy/Waves/releases/download/v1.1.0/waves_macos.zip",
        "curl: (6) Could not resolve host: github.com",
        "Error: Download failed on Cask 'waves' with message: Download failed",
    ]
    up = _brew(monkeypatch, lines, code=1)
    with caplog.at_level(logging.WARNING, logger=u.logger.name), pytest.raises(UpdaterError) as info:
        up.install()
    shown = str(info.value)
    assert shown.startswith("Homebrew reported an error.")
    assert u.channel_hint("homebrew-cask") in shown
    for leak in ("https://", "curl", "github.com", "==>", "\n"):
        assert leak not in shown, shown
    assert "Could not resolve host" in caplog.text


# ---- #5: FFmpeg failures name FFmpeg's own server and folder ---------------------
def test_user_facing_error_takes_the_callers_server_and_folder():
    kw = {"server": "the FFmpeg download server", "folder": "its tools folder"}
    assert user_facing_error(requests.exceptions.ConnectTimeout("x"), "Install failed", **kw) == (
        "Could not reach the FFmpeg download server"
    )
    resp = requests.Response()
    resp.status_code = 503
    err = requests.exceptions.HTTPError("503", response=resp)
    assert user_facing_error(err, "Install failed", **kw) == "The FFmpeg download server returned an error"
    assert user_facing_error(PermissionError(13, "x"), "Install failed", **kw) == (
        "Waves could not write to its tools folder"
    )
    # The app updater's wording is unchanged.
    assert user_facing_error(requests.exceptions.ConnectTimeout("x"), "Update failed") == (
        "Could not reach the update server"
    )


def test_the_ffmpeg_installer_names_its_own_server():
    stub = SimpleNamespace(
        _ffmpeg_install_inflight=False,
        _ffmpeg_abort=Event(),
        _ffmpeg=SimpleNamespace(install=lambda **k: (_ for _ in ()).throw(requests.exceptions.ConnectionError("x"))),
        ffmpegStateChanged=_Signal(),
        ffmpegProgress=_Signal(),
        ffmpegStatusChanged=_Signal(),
        threadpool=_InlinePool(),
    )
    _bind(stub, "installFfmpeg")
    stub.installFfmpeg()
    assert stub.ffmpegStateChanged.calls[-1] == ("failed", "Could not reach the FFmpeg download server")


def test_a_failed_remove_has_its_own_state_and_lead_in():
    stub = SimpleNamespace(
        _ffmpeg=SimpleNamespace(remove=lambda: {"state": "managed", "remove_error": "FFmpeg is in use right now."}),
        ffmpegStateChanged=_Signal(),
        ffmpegStatusChanged=_Signal(),
        _restore_ffmpeg_path=lambda: None,
        _logged_in=False,
    )
    _bind(stub, "removeFfmpeg")
    stub.removeFfmpeg()
    assert stub.ffmpegStateChanged.calls == [("remove_failed", "FFmpeg is in use right now.")]
    assert 'mgr.failPrefix = state === "remove_failed" ? "Remove failed: " : "Install failed: "' in FFMGR
    assert 'if (state === "remove_failed") state = "failed"' in FFMGR
    assert '"Install failed: " + page.ff.message' not in SETTINGS
    assert SETTINGS.count("text: page.ff.failPrefix + page.ff.message") == 2
    assert "text: appFfmpeg.failPrefix + appFfmpeg.message;" in MAIN


# ---- #13: one model walk per drop batch -----------------------------------------
def test_a_drop_batch_releases_its_holders_in_one_walk():
    rel = MAIN[MAIN.index("function dlHoldersRelease(ids) {") :]
    rel = rel[: rel.index("\n    }\n") + 6]
    assert rel.count("m.count") == 1, "one walk of the model for the whole batch"
    assert "dlHoldersGen += 1" in rel and rel.count("dlHoldersGen") == 1
    assert "function dlHolderRelease(id)" not in MAIN
    drop = MAIN[MAIN.index("function queueRowsDrop(qids) {") :][:2600]
    assert "if (mid) { if (!mids) mids = {}; mids[mid] = true }" in drop
    assert drop.index("if (mids) root.dlHoldersRelease(mids)") > drop.index("m.remove(at)")


def test_the_batch_release_frees_only_settled_holders_no_row_names():
    """The extracted function run in a JS engine against a fake model: the
    same holders go as the per-row release freed, with one generation bump."""
    qjs = pytest.importorskip("PySide6.QtQml")
    from PySide6.QtGui import QGuiApplication

    _ = QGuiApplication.instance() or QGuiApplication([])
    rel = MAIN[MAIN.index("function dlHoldersRelease(ids) {") :]
    rel = rel[: rel.index("\n    }\n") + 6]
    eng = qjs.QJSEngine()
    script = (
        "var destroyed = [];"
        "function holder(id, st) { return { st: st, destroy: function () { destroyed.push(id) } } }"
        "var dlHolders = { a: holder('a', 'done'), b: holder('b', ''), c: holder('c', 'running'),"
        " d: holder('d', 'done') };"
        "var rows = [{ media_id: 'd' }];"
        "var queueModel = { count: rows.length, get: function (i) { return rows[i] } };"
        "var dlHoldersGen = 0;" + rel + "dlHoldersRelease({ a: true, b: true, c: true, d: true, z: true });"
        "JSON.stringify([destroyed.sort(), Object.keys(dlHolders).sort(), dlHoldersGen])"
    )
    out = eng.evaluate(script)
    assert not out.isError(), out.toString()
    assert out.toString() == '[["a","b"],["c","d"],1]'


# ---- #14: concurrent probe callers share one probe ------------------------------
def _probe_stub(verdict_fn):
    stub = SimpleNamespace(
        settings=SimpleNamespace(data=SimpleNamespace(download_base_path="/Volumes/Share/Music")),
        _remount_download_share=lambda path: False,
        _probe_folder_verdict=verdict_fn,
    )
    _bind(stub, "_probe_download_base")
    return stub


def test_a_caller_arriving_mid_probe_shares_its_answer():
    started = []

    def slow_ok(path, volumes_root="/Volumes"):
        started.append(1)
        time.sleep(0.3)
        return ("ok", path)

    stub = _probe_stub(slow_ok)
    answers: list = []
    first = threading.Thread(target=lambda: answers.append(stub._probe_download_base(timeout_s=3.0)))
    first.start()
    time.sleep(0.05)
    answers.append(stub._probe_download_base(timeout_s=3.0))
    first.join(5)
    assert [a[0] for a in answers] == ["ok", "ok"], "a healthy probe is not a cold folder"
    assert len(started) == 1, "still one probe thread"


def test_a_joiner_waits_only_up_to_its_own_timeout():
    gate = Event()

    def hang(path, volumes_root="/Volumes"):
        gate.wait(5)
        return ("ok", path)

    stub = _probe_stub(hang)
    try:
        first = threading.Thread(target=lambda: stub._probe_download_base(timeout_s=3.0), daemon=True)
        first.start()
        time.sleep(0.05)
        t0 = time.monotonic()
        assert stub._probe_download_base(timeout_s=0.1)[0] == "timeout"
        assert time.monotonic() - t0 < 1.0
    finally:
        gate.set()


# ---- #15: a held discography is not a failed one ---------------------------------
def test_a_discography_the_gate_held_is_not_credited_as_failed():
    stub = _ArtistScanStub(SimpleNamespace(id="r0"))
    stub._gate_reachability = lambda retry, media_id: False
    _fav_artists_group(stub, ["r0", "r1"])
    stub.downloadArtist("r0")
    grp = stub._folder_groups[_FAV_ARTISTS_GROUP_ID]
    assert grp["failed"] == set() and grp["done"] == set(), "held work waits for its replay"
    assert stub.downloadState.emits[-1] == ("r0", "")


def test_the_reaper_keeps_the_rollup_while_a_discography_is_held():
    stub = _Stub(_Favorites())
    _fav_artists_group(stub, ["r0"])
    stub._pending_downloads = [("r0", lambda: None)]
    stub._reap_stranded_groups()
    stub._reap_stranded_groups()
    assert _FAV_ARTISTS_GROUP_ID in stub._folder_groups
    stub._pending_downloads = []
    stub._reap_stranded_groups()
    stub._reap_stranded_groups()
    assert _FAV_ARTISTS_GROUP_ID not in stub._folder_groups


# ---- #16: the max-age timers catch up on re-show -----------------------------------
def test_the_max_age_timers_catch_up_after_a_long_absence():
    hook = MAIN[MAIN.index("property double freshDownAt: 0") :][:1000]
    assert "if (!root.windowUp) { root.freshDownAt = Date.now(); return }" in hook
    assert "if (away < browseLandingFreshTimer.interval) return" in hook
    assert "Qt.callLater(" in hook
    assert "browseLandingFreshTimer, browseItemFreshTimer, artistFreshTimer, libraryFreshTimer" in hook
    assert "if (timers[i].running) timers[i].triggered()" in hook
    # The timers still run on windowUp, not onScreen.
    assert MAIN.count("running: root.signedIn && root.windowUp && ") == 4


@pytest.mark.parametrize("away_min,fired", [(10, ["land", "lib"]), (2, [])])
def test_a_re_show_fires_each_running_timer_once_after_a_long_absence(away_min, fired):
    qjs = pytest.importorskip("PySide6.QtQml")
    from PySide6.QtGui import QGuiApplication

    _ = QGuiApplication.instance() or QGuiApplication([])
    hook = MAIN[MAIN.index("function onWindowUpChanged() {") :]
    hook = hook[: hook.index("\n        }\n") + 10]
    script = (
        "var fired = [];"
        "function timer(name, running) { return { interval: 300000, running: running,"
        " triggered: function () { fired.push(name) } } }"
        "var browseLandingFreshTimer = timer('land', true), browseItemFreshTimer = timer('item', false),"
        " artistFreshTimer = timer('artist', false), libraryFreshTimer = timer('lib', true);"
        "var Qt = { callLater: function (f) { f() } };"
        f"var root = {{ windowUp: true, freshDownAt: Date.now() - {away_min} * 60000 }};"
        + hook
        + "onWindowUpChanged();"
        "root.windowUp = false; onWindowUpChanged(); var down = root.freshDownAt > 0;"
        "JSON.stringify([fired, down])"
    )
    eng = qjs.QJSEngine()  # held: a value outlives no engine
    out = eng.evaluate(script)
    assert not out.isError(), out.toString()
    assert json.loads(out.toString()) == [fired, True]


# ---- #17: an eviction clears only its own cache's side state ------------------------
def _cap_stub():
    stub = SimpleNamespace(
        _evict_lock=Lock(),
        _browse_pages={},
        _artist_cache={},
        _album_tracks_cache={},
        _edition_tracks_cache={},
        _item_fetch_ts={"x": 1.0},
        _prefetch_unrecorded={"x"},
        _artist_reval_ts={"x": 1.0},
        _album_tracks_unrecorded={"x"},
    )
    _bind(stub, "_remember_capped")
    return stub


def test_an_edition_eviction_keeps_the_album_caches_unrecorded_mark():
    stub = _cap_stub()
    stub._edition_tracks_cache["x"] = 1
    stub._remember_capped(stub._edition_tracks_cache, "y", 2, 1)
    assert stub._edition_tracks_cache == {"y": 2}
    assert stub._album_tracks_unrecorded == {"x"} and stub._artist_reval_ts == {"x": 1.0}
    assert stub._item_fetch_ts == {"x": 1.0} and stub._prefetch_unrecorded == {"x"}


@pytest.mark.parametrize(
    "cache,cleared",
    [
        ("_album_tracks_cache", ("_album_tracks_unrecorded",)),
        ("_artist_cache", ("_artist_reval_ts",)),
        ("_browse_pages", ("_item_fetch_ts", "_prefetch_unrecorded")),
    ],
)
def test_each_cache_evicts_its_own_side_state(cache, cleared):
    stub = _cap_stub()
    d = getattr(stub, cache)
    d["x"] = 1
    stub._remember_capped(d, "y", 2, 1)
    for name in ("_item_fetch_ts", "_prefetch_unrecorded", "_artist_reval_ts", "_album_tracks_unrecorded"):
        assert ("x" in getattr(stub, name)) is (name not in cleared), name
