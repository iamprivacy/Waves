"""Pins for the final audit of 2026-09-24 (report in notes/, private)."""

from __future__ import annotations

import pathlib
from types import SimpleNamespace

from waves.waves_ui.backend import WavesBridge, _PlanEntry


class _PlanStub:
    _unbind_merge_plans = WavesBridge._unbind_merge_plans
    _needs_plan_rebind = WavesBridge._needs_plan_rebind
    _rebind_merge_plan = WavesBridge._rebind_merge_plan

    def __init__(self):
        self._merge_plans = {}
        self._merge_plans_unbound = {}


def test_a_sign_out_keeps_a_best_of_both_plan_as_ids_and_retry_rebuilds_it():
    """Final audit C12: logout cleared the plan, so RETRY on the next session
    downloaded the identity edition as a plain album without saying so."""
    stub = _PlanStub()
    old_track = SimpleNamespace(id=555)
    stub._merge_plans["10"] = [_PlanEntry(old_track, 3, 1, "30")]
    stub._unbind_merge_plans()
    assert stub._merge_plans == {}, "no Track bound to the dead session survives"
    assert stub._merge_plans_unbound == {"10": [("555", 3, 1, "30")]}
    assert stub._needs_plan_rebind({"type": "album", "media_id": "10"})
    assert not stub._needs_plan_rebind({"type": "track", "media_id": "10"})

    fetched = []

    def track(tid):
        fetched.append(tid)
        return SimpleNamespace(id=tid, session="new")

    plan = stub._rebind_merge_plan("album", "10", SimpleNamespace(track=track))
    assert fetched == [555]
    assert plan == [_PlanEntry(SimpleNamespace(id=555, session="new"), 3, 1, "30")]
    assert stub._rebind_merge_plan("album", "99", SimpleNamespace(track=track)) is None


def test_a_rebind_that_cannot_fetch_a_borrowed_track_raises():
    stub = _PlanStub()
    stub._merge_plans_unbound["10"] = [("555", 3, 1, "30")]

    def track(_tid):
        raise OSError("down")

    try:
        stub._rebind_merge_plan("album", "10", SimpleNamespace(track=track))
    except OSError:
        pass
    else:
        raise AssertionError("a partial plan must not pass for a whole one")
    assert "10" in stub._merge_plans_unbound, "the ids stay for the next RETRY"


def test_the_retry_refetch_rebinds_before_it_retries():
    import inspect

    src = inspect.getsource(WavesBridge._retry_queue_refetch)
    assert "_rebind_merge_plan" in src and "self._merge_plans[media_id] = plan" in src
    assert "_needs_plan_rebind" in inspect.getsource(WavesBridge.retryQueueItem)


def test_the_config_folder_is_made_owner_only_on_posix(tmp_path):
    """Final audit C02: files inside are created with the umask (0644 on most
    Linux boxes); the README promises other accounts cannot read them."""
    import os
    import stat

    import pytest

    from waves.waves_ui import app as app_mod

    if os.name != "posix":
        pytest.skip("POSIX only")
    folder = tmp_path / "Waves"
    folder.mkdir(mode=0o755)
    folder.chmod(0o755)
    app_mod._make_private(folder)
    assert stat.S_IMODE(folder.stat().st_mode) == 0o700
    app_mod._make_private(folder)  # idempotent
    assert stat.S_IMODE(folder.stat().st_mode) == 0o700


def test_the_issue_templates_never_ask_for_raw_settings_or_crash_log():
    """Final audit C00/C01: both templates asked users to paste raw files that
    carry folder paths and share addresses into a public issue."""
    import pathlib

    root = pathlib.Path(__file__).resolve().parent.parent / ".github" / "ISSUE_TEMPLATE"
    for name in ("bug.yml", "help.yml"):
        text = (root / name).read_text(encoding="utf-8")
        assert "copy and paste your settings" not in text, name
        assert "no personal data" not in text, name
    assert "Export report" in (root / "bug.yml").read_text(encoding="utf-8")


def test_lyrics_are_embedded_only_when_embed_is_on(tmp_path):
    """Final audit C25: with only the sidecar switch on, every audio file got
    the lyrics in its tags as well."""
    from unittest.mock import patch

    from tests.test_tag_truths import _album, _download, _RecMeta, _track

    stream = SimpleNamespace(
        album_replay_gain=None, album_peak_amplitude=None, track_replay_gain=None, track_peak_amplitude=None
    )
    for embed in (False, True):
        dl = _download()
        dl.settings.data.lyrics_file = True
        dl.settings.data.lyrics_embed = embed
        dl._retrieve_lyrics = lambda _t: ("synced", "synced", "plain")
        with patch("waves.download.Metadata", _RecMeta):
            dl.metadata_write(_track(_album(12)), tmp_path / "t.flac", True, stream)
        kw = _RecMeta.last.kw
        assert (kw["lyrics"], kw["lyrics_unsynced"]) == (("synced", "plain") if embed else ("", ""))


def test_a_text_payload_to_a_binary_temp_file_never_raises(tmp_path):
    """Final audit C24: a failed cover.jpg fetch returns '' and the binary
    write raised ValueError, which failed the whole track."""
    from waves.download import Download

    dl = SimpleNamespace(fn_logger=lambda *a, **k: None)
    path = Download.write_to_tmp_file(dl, tmp_path, "xb", "abc")
    assert pathlib.Path(path).read_bytes() == b"abc"


def test_an_unreadable_settings_file_starts_on_defaults_and_is_left_alone(tmp_path):
    """Final audit C28: a settings.json that exists but cannot be opened
    raised out of the bridge constructor on every launch."""
    from waves.config import BaseConfig
    from waves.model.cfg import Settings as ModelSettings

    target = tmp_path / "settings.json"
    target.mkdir()  # a folder under that name: open() raises IsADirectoryError
    cfg = BaseConfig.__new__(BaseConfig)
    cfg.cls_model = ModelSettings
    cfg.file_path = str(target)
    cfg.path_base = str(tmp_path)
    assert cfg.read(str(target)) is False
    assert cfg.data == ModelSettings()
    assert target.is_dir(), "never written over"


def test_a_wrong_shaped_field_sets_the_file_aside_before_defaults_are_written(tmp_path):
    """Final audit C27: a list where a number belongs raised TypeError, which
    skipped the set-aside, so the defaults replaced the only copy."""
    import json

    from waves.config import BaseConfig
    from waves.model.cfg import Settings as ModelSettings

    target = tmp_path / "settings.json"
    body = json.dumps({"download_base_path": "/music/mine", "window_x": [1, 2]})
    target.write_text(body, encoding="utf-8")
    (tmp_path / "settings.json.bak").write_text("older backup", encoding="utf-8")
    cfg = BaseConfig.__new__(BaseConfig)
    cfg.cls_model = ModelSettings
    cfg.file_path = str(target)
    cfg.path_base = str(tmp_path)
    cfg.read(str(target))
    kept = [p for p in tmp_path.iterdir() if p.name.endswith(".bak")]
    assert (tmp_path / "settings.json.bak").read_text(
        encoding="utf-8"
    ) == "older backup", "an older backup is never deleted"
    assert any(p.read_text(encoding="utf-8") == body for p in kept), "the user's file is kept"


def test_a_folder_drill_in_parked_behind_the_tree_warm_replays_after_stop():
    """Final audit C45: STOP is for downloads; a parked navigation (no media
    id) was dropped and the drill-in view stayed blank."""
    from tests.test_invariant_sweep2_2026_09_24 import _WarmStub

    stub = _WarmStub()
    ran = []
    stub._warm_folder_tree(lambda: ran.append("open"))
    stub._scan_gen += 1  # STOP
    stub._on_folder_tree_warmed()
    assert ran == ["open"]


def test_a_root_resolve_is_single_flight_and_serves_the_last_answer():
    """Final audit C13: every ownership thread and download worker ran its own
    realpath on a share that stopped answering, and all of them stalled."""
    import threading
    from unittest.mock import patch

    class _Roots:
        _resolved_root = WavesBridge._resolved_root
        _ROOT_RESOLVE_TTL = WavesBridge._ROOT_RESOLVE_TTL

    stub = _Roots()
    entered, release = threading.Event(), threading.Event()
    calls = []

    def slow_realpath(p):
        calls.append(p)
        entered.set()
        release.wait(5)
        return "/real" + p

    with patch("waves.waves_ui.backend.os.path.realpath", slow_realpath):
        first = []
        t = threading.Thread(target=lambda: first.append(stub._resolved_root("/share")))
        t.start()
        assert entered.wait(5)
        # A second caller while the first is stuck answers at once, unresolved.
        assert stub._resolved_root("/share") == "/share"
        release.set()
        t.join(5)
    assert first == ["/real/share"]
    assert calls == ["/share"], "only one resolve ran"
    assert stub._resolved_root("/share") == "/real/share", "the memo answers inside the window"


def test_a_merge_job_takes_its_ask_from_the_click_not_from_the_other_editions_choice():
    import inspect

    src = inspect.getsource(WavesBridge.downloadAlbum)
    assert 'keep_ask=getattr(self, "_merge_asks", {}).pop(album_id, None)' in src
    assert "setQualityOverride(key" not in inspect.getsource(WavesBridge.downloadAlbumBestOfBoth)


def test_the_ntfs_recycle_bin_is_never_walked():
    """Final audit C32: the skip set had only the FAT spelling, compared
    exactly, so a deleted album in $Recycle.Bin indexed as owned."""
    from waves.library_index import _is_skipped_dir_name

    for name in ("$Recycle.Bin", "$RECYCLE.BIN", "$recycle.bin"):
        assert _is_skipped_dir_name(name), name
    assert not _is_skipped_dir_name("Recycle")


def test_the_max_age_timers_stop_while_the_window_is_minimized():
    """Final audit C35: four 5-minute revalidation timers kept hitting TIDAL
    from a minimized window."""
    main = (pathlib.Path(__file__).resolve().parent.parent / "waves" / "waves_ui" / "qml" / "Main.qml").read_text(
        encoding="utf-8"
    )
    # windowUp, not onScreen: onScreen also drops on a presentation stall,
    # which every app switch trips, and would restart the countdown each time.
    assert main.count("running: root.signedIn && root.windowUp &&") == 4
    assert "readonly property bool windowUp: visibility !== Window.Hidden && visibility !== Window.Minimized" in main


def test_the_bundle_trim_keeps_the_fallback_dialogs_model():
    """Final audit C29: Qt's non-native Folder/File dialog imports
    Qt.labs.folderlistmodel; trimming it left Browse dead on Linux."""
    trim = (pathlib.Path(__file__).resolve().parent.parent / "tools" / "trim_qt_bundle.sh").read_text(encoding="utf-8")
    body = trim.split("QT_LABS_DIRS=(", 1)[1].split(")", 1)[0]
    assert "folderlistmodel" not in body
    assert "LabsFolderListModel" not in trim
