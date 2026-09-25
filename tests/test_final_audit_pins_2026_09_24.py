"""Behavioural pins for the final audit of 2026-09-24 (C06, C08, C09, C10,
C43). Each test drives the real method through a harness and fails when
the fix it fences off is reverted; the earlier source-text or helper-only
pins stayed green through such a revert.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import tidalapi
from tidalapi.media import Quality

import waves.config as config_mod
from waves.config import BaseConfig
from waves.model.cfg import Settings as ModelSettings
from waves.waves_ui import backend as backend_mod
from waves.waves_ui.backend import WavesBridge

_EXIT_OK = 0
_EXIT_REGRESSED = 1
_EXIT_NO_QT = 77


# ---- C06: the settings READER heals a bad field in place -------------------
def _reader(tmp_path, path):
    cfg = BaseConfig()
    cfg.cls_model = ModelSettings
    cfg.file_path = str(path)
    cfg.path_base = str(tmp_path)
    return cfg


def test_the_reader_keeps_a_file_with_one_null_and_one_unknown_enum_field(tmp_path):
    """One bad field used to throw the WHOLE file away: settings.json went to
    .bak and the download folder, templates and toggles reset to stock."""
    stock = ModelSettings()
    raw = json.loads(stock.to_json())
    raw["quality_audio"] = "SUPER_HI_RES"
    raw["api_rate_limit_delay_sec"] = None
    kept = str(tmp_path / "somewhere" / "kept")
    raw["download_base_path"] = kept
    path = tmp_path / "settings.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    cfg = _reader(tmp_path, path)

    assert cfg.read(str(path)) is True, "a file with one unusable field is still a readable file"
    assert cfg.data.download_base_path == kept, "every sound setting survives"
    assert cfg.data.quality_audio == stock.quality_audio
    assert cfg.data.api_rate_limit_delay_sec == stock.api_rate_limit_delay_sec
    assert not (tmp_path / "settings.json.bak").exists(), "nothing was set aside"
    assert not [p for p in tmp_path.iterdir() if p.name.endswith(".bak")]


def test_settings_apply_writes_the_default_tier_over_a_null_setting():
    t = config_mod.Tidal.__new__(config_mod.Tidal)
    t.session = SimpleNamespace(audio_quality=None, video_quality=None)
    t.settings = SimpleNamespace(data=SimpleNamespace(quality_audio=None))
    t.is_atmos_session = False
    t.stream_lock = None
    assert t.settings_apply() is True
    assert t.session.audio_quality == tidalapi.Quality(ModelSettings().quality_audio)


# ---- C08: a discography merge is capped at the edition's own choice --------
def _merge_bridge(setting, overrides):
    from tests.test_edition_merge import _recs_of

    bridge = WavesBridge.__new__(WavesBridge)
    bridge.settings = SimpleNamespace(data=SimpleNamespace(quality_audio=setting))
    bridge._waves_prefs = {"explicit_mode": "explicit"}
    bridge._merge_recs_factory = lambda: _recs_of
    if overrides is not None:
        bridge._quality_overrides = overrides
    return bridge


def _standard_and_deluxe():
    from tests.test_edition_merge import _Album, _Track

    standard = _Album("std", [_Track("s-a", "A", 200), _Track("s-b", "B", 200)], rank=4)
    deluxe = _Album("dlx", [_Track("d-a", "A", 200), _Track("d-b", "B", 200), _Track("d-c", "C", 200)], rank=3)
    return standard, deluxe


def _plan_editions(bridge, albums):
    with patch("waves.waves_ui.backend._edition_base_key", lambda album: "one release"):
        return bridge._merge_editions(list(albums))


def test_a_discography_merge_is_planned_at_the_editions_own_quality_choice():
    """The deluxe carries a LOSSLESS choice under a HI-RES setting: its job
    asks at LOSSLESS, so the standard's HI-RES recordings are no upgrade and
    the plan must decline. Measured at the setting it assembled an album
    from tiers the job never fetches."""
    standard, deluxe = _standard_and_deluxe()
    plain, plans = _plan_editions(_merge_bridge(Quality.hi_res_lossless, {"dlx": "LOSSLESS"}), [standard, deluxe])
    assert plans == [], "the merge was planned at the setting, not at the edition's choice"
    assert plain == [deluxe], "the group collapses to its most complete edition"


def test_without_a_choice_the_same_group_merges_at_the_setting():
    standard, deluxe = _standard_and_deluxe()
    plain, plans = _plan_editions(_merge_bridge(Quality.hi_res_lossless, None), [standard, deluxe])
    assert [identity for identity, _plan in plans] == [deluxe]
    assert plain == []


# ---- C09: a Mixes-tab sweep that outlives a sign-out stores nothing --------
def test_a_walkless_media_lists_sweep_that_outlives_a_sign_out_stores_nothing():
    from tests.test_invariant_sweep2_2026_09_24 import _ListsStub

    stub = _ListsStub(on_walk=None)
    listing = {"playlists": [], "mixes": [SimpleNamespace(id="m1")]}

    def lists(session):
        stub._browse_gen += 1  # the sign-out lands while the listing is on the wire
        return listing

    with patch("waves.waves_ui.backend.user_media_lists", lists):
        fresh, tree = stub._media_lists(refresh=True, walk=False)
    assert fresh is listing and tree is None, "the caller still gets its answer (its own gen check drops it)"
    assert stub._media_lists_cache is None, "the old account's listing would serve the next account for the TTL"


def test_a_walkless_media_lists_sweep_on_the_same_account_is_cached():
    from tests.test_invariant_sweep2_2026_09_24 import _ListsStub

    stub = _ListsStub(on_walk=None)
    listing = {"playlists": [], "mixes": []}
    with patch("waves.waves_ui.backend.user_media_lists", lambda session: listing):
        stub._media_lists(refresh=True, walk=False)
    assert stub._media_lists_cache is not None and stub._media_lists_cache[1] is listing


# ---- C10: a raise inside the row build still releases the panel -----------
class _RaisingTrack:
    """A partial tidalapi object: the first attribute the row builder reads
    raises something other than AttributeError, so getattr's default does
    not cover it."""

    @property
    def id(self):
        raise RuntimeError("partial object")


def _album_stub_with_statuses(album):
    from tests.test_audit_batch5 import _AlbumTracksStub

    class _Rec(_AlbumTracksStub):
        def __init__(self):
            super().__init__()
            self.statuses = []

        def _set_status(self, text):
            self.statuses.append(text)

    stub = _Rec()
    stub._objs["album"]["al1"] = album
    stub._album_tracks_inflight["al1"] = True  # an open panel is watching
    return stub


def test_an_album_row_build_that_raises_releases_the_panel_and_says_so():
    stub = _album_stub_with_statuses(SimpleNamespace(tracks=lambda: [_RaisingTrack()]))
    stub._start_album_tracks_fetch("al1")
    assert stub._album_tracks_inflight == {}, "the in-flight mark stuck: the row sits on 'Loading tracks…' for good"
    assert stub.albumTracksLoaded.emits == [("al1", [])], "the panel must leave its loading state"
    assert stub.statuses == [backend_mod._TRACKS_FETCH_FAILED]
    assert "al1" not in stub._album_tracks_cache


def test_an_album_track_fetch_that_raises_releases_the_panel_and_says_so():
    def boom():
        raise OSError("network down")

    stub = _album_stub_with_statuses(SimpleNamespace(tracks=boom))
    stub._start_album_tracks_fetch("al1")
    assert stub._album_tracks_inflight == {}
    assert stub.albumTracksLoaded.emits == [("al1", [])]
    assert stub.statuses == [backend_mod._TRACKS_FETCH_FAILED]


def _playlist_stub():
    from tests.test_worker_latch_and_logout import _PlaylistTracksStub

    return _PlaylistTracksStub()


def test_a_playlist_whose_items_raise_says_so_and_still_answers_the_panel(monkeypatch):
    stub = _playlist_stub()

    def boom(obj):
        raise OSError("network down")

    monkeypatch.setattr(backend_mod, "_all_playlist_items", boom)
    stub.loadPlaylistTracks("p1")
    assert stub.statuses == [backend_mod._TRACKS_FETCH_FAILED]
    assert stub.playlistTracksLoaded.emits == [("p1", [])]


def test_a_playlist_row_build_that_raises_says_so_and_still_answers_the_panel(monkeypatch):
    stub = _playlist_stub()
    monkeypatch.setattr(backend_mod, "_all_playlist_items", lambda obj: ([_RaisingTrack()], True))
    stub.loadPlaylistTracks("p1")
    assert stub.statuses == [backend_mod._TRACKS_FETCH_FAILED]
    assert stub.playlistTracksLoaded.emits == [("p1", [])]


def test_a_failed_playlist_refetch_says_so_and_still_answers_the_panel():
    stub = _playlist_stub()
    stub._objs = {"playlist": {}}

    def boom(pid):
        raise OSError("offline")

    stub.tidal = SimpleNamespace(session=SimpleNamespace(playlist=boom))
    stub.loadPlaylistTracks("p1")
    assert stub.statuses == [backend_mod._TRACKS_FETCH_FAILED]
    assert stub.playlistTracksLoaded.emits == [("p1", [])]


def test_a_playlist_failure_after_a_sign_out_stays_silent(monkeypatch):
    stub = _playlist_stub()

    def boom(obj):
        stub._browse_gen += 1
        raise OSError("network down")

    monkeypatch.setattr(backend_mod, "_all_playlist_items", boom)
    stub.loadPlaylistTracks("p1")
    assert stub.statuses == [], "'Signed out' must stand"
    assert stub.playlistTracksLoaded.emits == []


# ---- C43: the ownership max-age timer is wired and running ----------------
def _own_max_age_verdict(bridge, calls: list) -> list[str]:
    """What is wrong with the bridge's ownership max-age wiring, if anything.
    A list so the subprocess can print every miss at once."""
    misses = []
    timer = getattr(bridge, "_own_max_age", None)
    if timer is None:
        return ["no _own_max_age timer on the bridge"]
    if not timer.isActive():
        misses.append("the timer is not running")
    if timer.interval() != WavesBridge._OWN_MAX_AGE_MS:
        misses.append(f"interval {timer.interval()} != _OWN_MAX_AGE_MS {WavesBridge._OWN_MAX_AGE_MS}")
    calls.clear()
    timer.timeout.emit()
    if calls != [bridge]:
        misses.append(f"a tick did not reach _age_ownership_answers once (calls={len(calls)})")
    return misses


def _run_own_max_age_scenario() -> int:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    try:
        from PySide6.QtGui import QGuiApplication
    except Exception as exc:
        print(f"Qt unavailable: {exc}", file=sys.stderr)
        return _EXIT_NO_QT
    from tests._qml_offline import patch_offline

    patch_offline()
    calls: list = []
    # Replaced on the class BEFORE construction: the timer connects the bound
    # method at __init__, so an instance patch afterwards would miss the tick.
    WavesBridge._age_ownership_answers = lambda self: calls.append(self)
    app = QGuiApplication.instance() or QGuiApplication([])
    bridge = WavesBridge(tidal=None)
    misses = _own_max_age_verdict(bridge, calls)
    for miss in misses:
        print(miss, file=sys.stderr)
    app.processEvents()
    return _EXIT_OK if not misses else _EXIT_REGRESSED


def test_the_ownership_max_age_timer_is_running_and_ticks_the_ageing():
    """The four lines that make a parked page re-ask (a file deleted in
    Finder stops reading as DOWNLOADED) have no other check: the ageing
    method is tested by hand, the timer that calls it never was."""
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="waves-own-max-age-test-")
    proc = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), "--run-own-max-age"],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    tail = "\n".join((proc.stdout + proc.stderr).strip().splitlines()[-8:])
    if proc.returncode == _EXIT_NO_QT:
        pytest.skip("PySide6 / offscreen Qt unavailable")
    assert (
        proc.returncode == _EXIT_OK
    ), f"the ownership max-age timer is unwired. Scenario exit={proc.returncode}:\n{tail}"


if __name__ == "__main__":
    if "--run-own-max-age" in sys.argv:
        raise SystemExit(_run_own_max_age_scenario())
