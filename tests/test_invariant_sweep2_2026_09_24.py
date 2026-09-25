"""Pins for the second rule sweep (2026-09-24): the QML to Python surface,
worker lifecycle, settings defaults, STOP/quit/logout, visible failures and
what lands on disk. Each test names the failure it fences off; where no stub
can exercise the mechanism, the pin is on the source.
"""

from __future__ import annotations

import inspect
import json
import pathlib
import shutil
import sys
from threading import Lock
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import waves.config as config_mod
import waves.download as download_mod
import waves.waves_ui.backend as backend_mod
from waves.model.cfg import Settings as ModelSettings
from waves.ownership import path_under
from waves.waves_ui.backend import _FACTORY_WIPE_FILES, _FACTORY_WIPE_LOG_PATTERNS, WavesBridge

QML = pathlib.Path(__file__).resolve().parent.parent / "waves" / "waves_ui" / "qml"


# ---- worker lifecycle: no cache write across a sign-out -------------------
class _ListsStub:
    _media_lists = WavesBridge._media_lists
    _MEDIA_LISTS_TTL = WavesBridge._MEDIA_LISTS_TTL

    def __init__(self, on_walk):
        self._media_lists_lock = Lock()
        self._media_lists_cache = None
        self._folder_tree = None
        self._browse_gen = 1
        self.tidal = SimpleNamespace(session=object())
        self._on_walk = on_walk


def test_a_media_lists_sweep_that_outlives_a_sign_out_stores_nothing():
    """The folder walk is one request per folder; a sign-out during it used
    to be followed by the old account's playlists and tree being written
    into the caches the next account was served from within the TTL."""
    tree = SimpleNamespace(nodes={"f": 1}, playlist_paths={}, partial=False)
    stub = _ListsStub(on_walk=None)

    def walk(session, root_folders):
        stub._browse_gen += 1  # the sign-out lands mid-walk
        return tree

    with (
        patch("waves.waves_ui.backend.user_media_lists", return_value={"playlists": []}),
        patch("waves.waves_ui.backend.walk_playlist_tree", walk),
    ):
        fresh, got = stub._media_lists(refresh=True)
    assert got is tree, "the caller still gets its answer (its own gen check drops it)"
    assert stub._media_lists_cache is None and stub._folder_tree is None


def test_a_media_lists_sweep_on_the_same_account_still_caches():
    tree = SimpleNamespace(nodes={"f": 1}, playlist_paths={}, partial=False)
    stub = _ListsStub(on_walk=None)
    with (
        patch("waves.waves_ui.backend.user_media_lists", return_value={"playlists": []}),
        patch("waves.waves_ui.backend.walk_playlist_tree", return_value=tree),
    ):
        stub._media_lists(refresh=True)
    assert stub._folder_tree is tree and stub._media_lists_cache[1] == {"playlists": []}


class _FavStub:
    _favorite_ids = WavesBridge._favorite_ids
    _FAV_IDS_TTL = WavesBridge._FAV_IDS_TTL

    def __init__(self):
        self._fav_ids = {}
        self._browse_gen = 1

    def _all_favorites(self, kind):
        self._browse_gen += 1  # signed out while paging
        return [SimpleNamespace(id="a1")]


def test_favourite_ids_fetched_across_a_sign_out_are_not_cached():
    stub = _FavStub()
    assert stub._favorite_ids("albums") == {"a1"}
    assert stub._fav_ids == {}, "the previous account's favourites would filter the next account's pages for 10 minutes"


def test_logout_drops_the_queues_kept_objects_and_the_merge_plans():
    """RETRY on the next account re-fetches by id through the new session,
    instead of downloading through the signed-out account's token."""
    src = inspect.getsource(WavesBridge.logout)
    for name in ("_job_objs", "_merge_scanned"):
        assert f'getattr(self, "{name}"' in src and ".clear()" in src, name
    # Plans keep their catalog ids so RETRY rebuilds the merge (final audit
    # C12); the Track objects are dropped (tests/test_final_audit_2026_09_24.py).
    assert "WavesBridge._unbind_merge_plans(self)" in src


# ---- STOP while a click is parked ------------------------------------------
class _WarmStub:
    _warm_folder_tree = WavesBridge._warm_folder_tree
    _on_folder_tree_warmed = WavesBridge._on_folder_tree_warmed

    def __init__(self):
        self._logged_in = True
        self._tree_warm_waiting = []
        self._tree_warm_inflight = False
        self._scan_gen = 0
        self._folder_tree = object()
        self.busy = []
        self.states = []
        self.statuses = []
        self.threadpool = SimpleNamespace(start=lambda w: None)  # the sweep never lands on its own here

    def _set_busy(self, on):
        self.busy.append(on)

    def _current_folder_tree(self):
        return self._folder_tree

    def _set_status(self, text):
        self.statuses.append(text)

    downloadState = property(lambda self: SimpleNamespace(emit=lambda mid, st: self.states.append((mid, st))))


def test_a_click_parked_behind_the_folder_warm_honours_a_stop_pressed_meanwhile():
    stub = _WarmStub()
    ran = []
    stub._warm_folder_tree(lambda: ran.append("cat"), "cat:pages/mood")
    stub._scan_gen += 1  # STOP
    stub._on_folder_tree_warmed()
    assert ran == [], "the whole category started downloading behind the STOP press"
    assert stub.states == [("cat:pages/mood", "")], "the parked button is handed back"


def test_a_click_parked_behind_the_folder_warm_replays_when_nothing_was_stopped():
    stub = _WarmStub()
    ran = []
    stub._warm_folder_tree(lambda: ran.append("cat"), "cat:pages/mood")
    stub._on_folder_tree_warmed()
    assert ran == ["cat"]


def test_a_download_refetch_checks_the_stop_generation():
    src = inspect.getsource(WavesBridge._refetch_for_download)
    assert 'scan_gen = getattr(self, "_scan_gen", 0)' in src
    assert 'scan_gen != getattr(self, "_scan_gen", 0)' in src


# ---- settings defaults -----------------------------------------------------
def test_every_artist_section_key_the_qml_writes_is_whitelisted():
    """Main.qml builds the key as "artist_sec_" + which + "_collapsed" (and
    "_expanded"); setWavesPref drops a key the whitelist lacks without a
    word, so the Videos fold and every SHOW ALL came back reset on launch."""
    main = (QML / "Main.qml").read_text()
    assert 'setWavesPref("artist_sec_" + which + "_collapsed", v)' in main
    assert 'setWavesPref("artist_sec_" + which + "_expanded", v)' in main
    defaults = WavesBridge._default_waves_prefs(SimpleNamespace())
    keys = [f"artist_sec_{w}_{st}" for w in ("tracks", "albums", "eps", "videos") for st in ("collapsed", "expanded")]
    missing = [k for k in keys if k not in defaults]
    assert missing == [], f"setWavesPref drops a key the whitelist lacks: {missing}"


def test_factory_reset_takes_the_search_cache():
    assert "search_cache.json" in _FACTORY_WIPE_FILES and "search_cache.json.tmp" in _FACTORY_WIPE_FILES
    assert any(p.search("search_cache.json.AbC1.tmp") for p in _FACTORY_WIPE_LOG_PATTERNS)


def test_a_null_setting_falls_back_to_its_default_instead_of_crashing_the_launch():
    stock = ModelSettings()
    raw = json.loads(stock.to_json())
    raw["quality_audio"] = None
    raw["api_rate_limit_delay_sec"] = None
    healed = config_mod._drop_unusable_fields(json.dumps(raw), ModelSettings)
    data = ModelSettings.from_json(healed)
    assert data.quality_audio == stock.quality_audio
    assert data.api_rate_limit_delay_sec == stock.api_rate_limit_delay_sec


def test_one_unknown_enum_value_does_not_throw_the_whole_file_away():
    stock = ModelSettings()
    raw = json.loads(stock.to_json())
    raw["quality_audio"] = "SUPER_HI_RES"
    raw["download_base_path"] = "/somewhere/kept"
    healed = config_mod._drop_unusable_fields(json.dumps(raw), ModelSettings)
    data = ModelSettings.from_json(healed)
    assert data.quality_audio == stock.quality_audio
    assert data.download_base_path == "/somewhere/kept", "every other setting survives"


def test_the_reader_leaves_a_sound_file_untouched():
    text = ModelSettings().to_json()
    assert config_mod._drop_unusable_fields(text, ModelSettings) == text
    assert config_mod._drop_unusable_fields("[]", ModelSettings) == "[]"


def test_settings_apply_falls_back_when_the_stored_quality_is_not_one():
    import tidalapi

    assert config_mod._quality_or_default(None) == tidalapi.Quality(ModelSettings().quality_audio)
    assert config_mod._quality_or_default("HI_RES_LOSSLESS") == tidalapi.Quality.hi_res_lossless


# ---- failures visible ------------------------------------------------------
def test_the_track_panels_say_when_a_fetch_failed_and_retry_on_expand():
    main = (QML / "Main.qml").read_text()
    assert "c[id] = tracks.length ? tracks : null" in main, "an empty array is truthy and hid the loading text"
    # One copy for a failed fetch and a truly empty list (a video-only album,
    # an empty playlist): the status line says which (final audit C46).
    assert main.count("No tracks to show, collapse and expand to try again") == 2
    # the album toggle asks again when the cached value is the failure marker
    assert "if (!root.trackCache[albumId]) waves.loadAlbumTracks(albumId)" in main


def test_a_failed_album_track_fetch_sets_a_status_only_for_an_open_panel():
    from tests.test_audit_batch5 import _AlbumTracksStub

    class _Rec(_AlbumTracksStub):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            self.statuses = []

        def _set_status(self, text):
            self.statuses.append(text)

    def gone(_id):
        raise OSError("network down")

    # A watched expand whose re-fetch fails says so.
    open_panel = _Rec(session_album=gone)
    open_panel._album_tracks_inflight["7"] = True
    open_panel._start_album_tracks_fetch("7")
    assert open_panel.statuses == [backend_mod._TRACKS_FETCH_FAILED]
    # A hover prefetch (unwatched) fails silently: no panel is open (final audit C44).
    hover = _Rec(session_album=gone)
    hover._album_tracks_inflight["7"] = False
    hover._start_album_tracks_fetch("7")
    assert hover.statuses == []
    assert "7" not in hover._album_tracks_inflight, "the in-flight mark is released"


def test_a_failed_home_load_and_scroll_page_say_so():
    assert "Could not load your recent favourites" in inspect.getsource(WavesBridge.loadHome)
    assert "Could not load more, scroll again to retry" in inspect.getsource(WavesBridge.loadMoreLibrary)


def test_retry_all_names_the_rows_it_could_not_restart():
    src = inspect.getsource(WavesBridge._retry_all_with_status)
    assert "could not be restarted" in src


def test_a_rescan_that_raises_leaves_an_error_status_the_settings_page_explains():
    from waves.waves_ui import bridge_library

    assert 'status = "error"' in inspect.getsource(bridge_library)
    settings = (QML / "SettingsPage.qml").read_text()
    assert 'page.libraryScanStatus === "error"' in settings
    assert "The last scan did not finish" in settings


def test_the_cdn_session_retries_a_throttle_but_never_sleeps_long():
    from unittest.mock import Mock

    session = download_mod.Download._shared_http()
    retry = session.get_adapter("https://cdn.example/x").max_retries
    # A 429 or 503 carrying Retry-After is retried (final audit C15: with the
    # header ignored and no forcelist, urllib3 did not retry it at all) ...
    assert retry.is_retry("GET", 429, has_retry_after=True)
    assert retry.is_retry("GET", 503, has_retry_after=True)
    # ... but a huge Retry-After is capped so STOP never waits hours.
    response = Mock()
    response.headers = {"Retry-After": "21600"}
    assert retry.get_retry_after(response) <= download_mod.Download._RETRY_AFTER_CAP <= 10


def test_a_worker_that_outlived_the_bridge_is_not_a_crash(caplog):
    import logging

    from waves.worker import Worker

    def boom():
        raise RuntimeError("Signal source has been deleted")

    with caplog.at_level(logging.DEBUG, logger="waves"):
        Worker(boom).run()
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]

    def real_crash():
        raise RuntimeError("something else")

    with caplog.at_level(logging.DEBUG, logger="waves"):
        Worker(real_crash).run()
    assert [r for r in caplog.records if r.levelno >= logging.ERROR]


# ---- files on disk ---------------------------------------------------------
@pytest.mark.skipif(sys.platform != "darwin", reason="normalisation folding is a macOS volume rule")
def test_path_under_folds_composition_on_macos(tmp_path):
    nfc = str(tmp_path / "Música")
    nfd = str(tmp_path / "Música")
    assert path_under(nfc + "/A/01.flac", nfd)
    assert path_under(nfd + "/A/01.flac", nfc)


def test_the_staging_budget_measures_the_parent_on_the_platforms_ruler():
    src = inspect.getsource(download_mod._staging_path)
    assert "_text_length(str(path_destination.parent))" in src
    assert "os.fsencode(str(path_destination.parent))" not in src


def test_an_untaggable_file_is_not_filed_as_done(tmp_path):
    dl = download_mod.Download.__new__(download_mod.Download)
    dl.metadata_write = lambda *a, **k: (False, None, ".lrc", None)
    with pytest.raises(download_mod.UntaggableFile):
        dl._handle_metadata_and_extras(
            SimpleNamespace(name="x", artists=[SimpleNamespace(name="a")], version=""),
            tmp_path / "t.flac",
            tmp_path / "o.flac",
            False,
            object(),
        )
    assert issubclass(
        download_mod.UntaggableFile, OSError
    ), "the collection loop already counts an OSError as the item's failure"


class _SymlinkDl:
    _symlink_after_move = download_mod.Download._symlink_after_move

    def __init__(self):
        self.filled = []
        self.logs = []
        self.fn_logger = SimpleNamespace(
            debug=lambda m: None,
            info=lambda m: self.logs.append(("info", m)),
            error=lambda m: self.logs.append(("error", m)),
        )

    def _move_file(self, src, dst, overwrite=False):
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))
        return True

    def _ensure_directory(self, p):
        p.mkdir(parents=True, exist_ok=True)

    def _unlink_with_retry(self, p):
        return True

    def _note_dir_filled(self, p):
        self.filled.append(p)


def test_a_refused_symlink_leaves_a_real_copy_in_the_playlist_folder(tmp_path):
    """Windows without the privilege, or a share that refuses links: the
    move had already emptied the playlist folder, so the m3u was written
    empty and ownership recorded on a path with no file behind it."""
    src = tmp_path / "Playlists" / "P" / "01.flac"
    src.parent.mkdir(parents=True)
    src.write_bytes(b"audio")
    dst = tmp_path / "Artist" / "Album" / "01.flac"
    dl = _SymlinkDl()

    def refuse(self, target):
        raise OSError(1314, "privilege not held")

    with patch.object(pathlib.Path, "symlink_to", refuse):
        dl._symlink_after_move(src, dst, skip_file=False, skip_symlink=False, overwrite=False)
    assert dst.read_bytes() == b"audio", "the audio is in the track folder"
    assert src.is_file() and not src.is_symlink() and src.read_bytes() == b"audio", "and a real copy stayed behind"
    assert src in dl.filled
