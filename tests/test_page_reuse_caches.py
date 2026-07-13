"""Unit coverage for the session/page caches that stop redundant refetching.

These pin the reuse rules added by the phase-1 caching pass:
- the playlists/mixes sweep (user_media_lists) is fetched once and paged
  locally, refreshed only by first-page loads and rate-limited by a TTL,
- album track lists are cached per session with a bounded FIFO,
- the My Tidal Home landing persists in page_cache.json for instant paint.
"""

from __future__ import annotations

import json
from threading import Lock
from unittest.mock import MagicMock

from tidaler.waves_ui import backend
from tidaler.waves_ui.backend import WavesBridge

# ----- playlists/mixes sweep cache ------------------------------------------


def _sweep_bridge(monkeypatch, calls):
    b = WavesBridge.__new__(WavesBridge)
    b._media_lists_cache = None
    b._media_lists_lock = Lock()
    b.tidal = MagicMock()

    def fake_sweep(session):
        calls.append(session)
        return {"playlists": [], "mixes": ["m1", "m2"]}

    monkeypatch.setattr(backend, "user_media_lists", fake_sweep)
    return b


def test_media_lists_scroll_pages_reuse_the_sweep(monkeypatch):
    calls: list = []
    b = _sweep_bridge(monkeypatch, calls)
    first = b._media_lists(refresh=True)  # tab open: fetches
    again = b._media_lists(refresh=False)  # scroll page: reuses
    assert first is again and len(calls) == 1


def test_media_lists_refresh_is_ttl_limited(monkeypatch):
    calls: list = []
    b = _sweep_bridge(monkeypatch, calls)
    b._media_lists(refresh=True)
    b._media_lists(refresh=True)  # e.g. an immediate re-sort: still cached
    assert len(calls) == 1
    # Age the cache past the TTL: the next first-page load re-sweeps.
    ts, data = b._media_lists_cache
    b._media_lists_cache = (ts - WavesBridge._MEDIA_LISTS_TTL - 1, data)
    b._media_lists(refresh=True)
    assert len(calls) == 2


def test_media_lists_no_cache_fetches_even_without_refresh(monkeypatch):
    calls: list = []
    b = _sweep_bridge(monkeypatch, calls)
    assert b._media_lists(refresh=False)["mixes"] == ["m1", "m2"]
    assert len(calls) == 1


def test_library_page_playlists_pages_locally_from_one_sweep(monkeypatch):
    calls: list = []
    b = _sweep_bridge(monkeypatch, calls)
    b._lib_sort = {}
    b._sort_local_library = lambda full, spec: full
    b._mix_dict = lambda m: {"id": m}
    rows0, more0 = WavesBridge._library_page(b, "mixes", 0, 1)
    rows1, more1 = WavesBridge._library_page(b, "mixes", 1, 1)
    assert rows0 == [{"id": "m1"}] and more0 is True
    assert rows1 == [{"id": "m2"}] and more1 is False
    assert len(calls) == 1  # the scroll page never re-swept the account


# ----- album track-list cache -----------------------------------------------


def test_remember_album_tracks_evicts_oldest_beyond_cap():
    b = WavesBridge.__new__(WavesBridge)
    b._album_tracks_cache = {}
    cap = WavesBridge._ALBUM_TRACKS_CACHE_MAX
    for i in range(cap + 5):
        b._remember_album_tracks(str(i), [{"id": i}])
    assert len(b._album_tracks_cache) == cap
    assert "0" not in b._album_tracks_cache  # oldest inserts evicted first
    assert str(cap + 4) in b._album_tracks_cache


# ----- search cache + popularity memo -----------------------------------------


def test_remember_search_evicts_oldest_beyond_cap():
    b = WavesBridge.__new__(WavesBridge)
    b._search_cache = {}
    cap = WavesBridge._SEARCH_CACHE_MAX
    for i in range(cap + 3):
        b._remember_search(f"needle{i}", {"artists": []})
    assert len(b._search_cache) == cap
    assert "needle0" not in b._search_cache


def test_pop_cached_honours_ttl():
    import time

    b = WavesBridge.__new__(WavesBridge)
    b._artist_pop_cache = {"a1": (time.monotonic(), 73)}
    assert b._pop_cached("a1") == 73
    assert b._pop_cached("nope") == -1
    b._artist_pop_cache["a1"] = (time.monotonic() - WavesBridge._ARTIST_POP_TTL - 1, 73)
    assert b._pop_cached("a1") == -1  # expired entries read as absent


# ----- favourite-id sets TTL ---------------------------------------------------


def _fav_bridge(ids_per_call):
    b = WavesBridge.__new__(WavesBridge)
    b._fav_ids = {}
    favorites = MagicMock()
    calls = []

    def albums(limit=None, offset=0):
        calls.append(offset)
        return [] if offset else list(ids_per_call)

    favorites.albums.side_effect = albums
    b.tidal = MagicMock()
    b.tidal.session.user.favorites = favorites
    return b, calls


def test_favorite_ids_cached_within_ttl_and_refetched_after():
    import time

    stub = type("O", (), {"id": "x1"})()
    b, calls = _fav_bridge([stub])
    assert b._favorite_ids("albums") == {"x1"}
    assert b._favorite_ids("albums") == {"x1"}  # served from cache
    assert len(calls) == 1
    ts, ids = b._fav_ids["albums"]
    b._fav_ids["albums"] = (time.monotonic() - WavesBridge._FAV_IDS_TTL - 1, ids)
    assert b._favorite_ids("albums") == {"x1"}  # expired: refetched
    assert len(calls) == 2


def test_favorite_ids_failed_refresh_serves_stale():
    import time

    b = WavesBridge.__new__(WavesBridge)
    b._fav_ids = {"albums": (time.monotonic() - WavesBridge._FAV_IDS_TTL - 1, {"old"})}
    b.tidal = MagicMock()
    b.tidal.session.user.favorites.albums.side_effect = RuntimeError("blip")
    assert b._favorite_ids("albums") == {"old"}


# ----- preview clip reuse ------------------------------------------------------


def test_remember_preview_clip_evicts_and_deletes(tmp_path):
    b = WavesBridge.__new__(WavesBridge)
    b._preview_clips = {}
    cap = WavesBridge._PREVIEW_CLIPS_MAX
    paths = []
    for i in range(cap + 2):
        p = tmp_path / f"clip{i}.m4a"
        p.write_bytes(b"x")
        paths.append(p)
        b._remember_preview_clip((str(i), True), str(p))
    assert len(b._preview_clips) == cap
    assert not paths[0].exists() and not paths[1].exists()  # evicted files deleted
    assert paths[-1].exists()


# ----- Home landing persistence ----------------------------------------------


def _cache_bridge(tmp_path):
    b = WavesBridge.__new__(WavesBridge)
    b._logged_in = True
    b._lib_cache = {}
    b._lib_sort = {}
    b._browse_root_cache = None
    b._browse_pages = {}
    b._artist_cache = {}
    b._home_cache = None
    b._page_cache_path = str(tmp_path / "page_cache.json")
    b._page_cache_lock = Lock()
    b.tidal = MagicMock()
    b.tidal.session.user.id = "42"
    return b


def test_home_landing_round_trips_through_page_cache(tmp_path):
    shelves = [{"rowKind": "cards", "title": "Recent albums", "target": "albums", "items": [{"id": "a1"}]}]
    saver = _cache_bridge(tmp_path)
    saver._home_cache = shelves
    saver._save_page_cache()
    with open(saver._page_cache_path) as fh:
        assert json.load(fh)["home"] == shelves

    loader = _cache_bridge(tmp_path)
    loader._load_page_cache()
    assert loader._home_cache == shelves


def test_home_landing_snapshot_ignored_for_other_account(tmp_path):
    saver = _cache_bridge(tmp_path)
    saver._home_cache = [{"rowKind": "cards", "title": "Recent albums", "items": []}]
    saver._save_page_cache()

    loader = _cache_bridge(tmp_path)
    loader.tidal.session.user.id = "43"
    loader._load_page_cache()
    assert loader._home_cache is None
