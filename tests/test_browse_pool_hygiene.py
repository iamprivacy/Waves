"""Thread hygiene in the browse region: pools that report, caches that lock.

The gap round of the 2026-08-29 audit filed five findings here, all of the
same family: work that crosses threads without the discipline its siblings
already keep.

* The per-search popularity fan-out and the merged-album track fan-out were
  invisible to the verbose perf sampler (the project's first diagnostics
  contract: every new pool registers).
* The popularity cache was inserted into, and trimmed, with no lock while an
  older search's pool could still be inserting.
* The sign-out and paste-a-link paths cleared the shared object buckets
  lock-free while search took the lock for the identical clear.
* Expanding a hover-prefetched album ran an ownership commit on the GUI thread.
* Two tile-art crawls could pass the same check-then-set and the last one to
  finish dropped the other's samples from the disk cache.
"""

from __future__ import annotations

import re
from pathlib import Path
from threading import Lock
from types import SimpleNamespace

from waves.waves_ui import backend
from waves.waves_ui.backend import WavesBridge

BACKEND_SRC = Path(backend.__file__).read_text(encoding="utf-8")


class _Signal:
    def __init__(self):
        self.emits: list = []

    def emit(self, *args):
        self.emits.append(args)


class _InlinePool:
    """Runs the worker where it was started (the tests want no threads)."""

    def __init__(self):
        self.started: list = []

    def start(self, worker, priority: int = 0):
        self.started.append(worker)
        worker.fn()


class _HoldingPool:
    """Collects workers without running them, so a test can see what was
    deferred rather than what happened to finish."""

    def __init__(self):
        self.started: list = []

    def start(self, worker, priority: int = 0):
        self.started.append(worker)


class _WatchedLock:
    """A real lock that also answers "is it held right now?"."""

    def __init__(self):
        self._lock = Lock()
        self.held = False
        self.uses = 0

    def __enter__(self):
        self._lock.acquire()
        self.held = True
        self.uses += 1
        return self

    def __exit__(self, *exc):
        self.held = False
        self._lock.release()
        return False


class _WatchedDict(dict):
    """A dict that counts writes made while a given lock is NOT held."""

    def __init__(self, lock):
        super().__init__()
        self._lock = lock
        self.unlocked_writes = 0
        self.unlocked_clears = 0

    def __setitem__(self, key, value):
        if not self._lock.held:
            self.unlocked_writes += 1
        super().__setitem__(key, value)

    def __delitem__(self, key):
        if not self._lock.held:
            self.unlocked_writes += 1
        super().__delitem__(key)

    def clear(self):
        if not self._lock.held:
            self.unlocked_clears += 1
        super().clear()


# --------------------------------------------------------------------------- #
# G-15 / G-16: the search's popularity fan-out
# --------------------------------------------------------------------------- #
class _SearchStub:
    search = WavesBridge.search
    _search_total = staticmethod(WavesBridge._search_total)
    _pop_cached = WavesBridge._pop_cached
    _remember_capped = WavesBridge._remember_capped
    _remember_search = WavesBridge._remember_search
    _SEARCH_TTL = WavesBridge._SEARCH_TTL
    _SEARCH_CACHE_MAX = WavesBridge._SEARCH_CACHE_MAX
    _ARTIST_POP_TTL = WavesBridge._ARTIST_POP_TTL
    _ARTIST_POP_MAX = 3  # small enough that one search has to evict

    def __init__(self):
        self.threadpool = _InlinePool()
        self.statuses: list[str] = []
        self.busy: list[bool] = []
        self._logged_in = True
        self._search_gen = 0
        self._search_cache: dict = {}
        self._evict_lock = _WatchedLock()
        self._artist_pop_cache = _WatchedDict(self._evict_lock)
        self._objs_lock = Lock()
        self._objs: dict = {"artist": {}, "album": {}, "track": {}, "video": {}, "playlist": {}, "mix": {}}
        self.tidal = SimpleNamespace(session=object())
        self.searchResults = _Signal()
        self.artistMetaLoaded = _Signal()

    def _set_status(self, text):
        self.statuses.append(text)

    def _set_busy(self, on):
        self.busy.append(bool(on))

    def _remember(self, kind, key, obj):
        self._objs[kind][key] = obj

    def _dedup_albums(self, albums):
        return list(albums)

    def _dedup_tracks(self, tracks):
        return list(tracks)

    def _dedup_videos(self, videos):
        return list(videos)

    def _album_dict(self, a):
        return {"id": getattr(a, "id", "")}

    def _track_dict(self, t):
        return {"id": getattr(t, "id", "")}

    def _video_dict(self, v):
        return {"id": getattr(v, "id", "")}

    def _playlist_dict(self, p):
        return {"id": getattr(p, "id", "")}

    def _mix_dict(self, m):
        return {"id": getattr(m, "id", "")}

    def _top_hit_dict(self, hit):
        return None


def _run_search(monkeypatch, n_artists: int):
    stub = _SearchStub()
    artists = [SimpleNamespace(id=f"a{i}", name=f"Artist {i}") for i in range(n_artists)]
    monkeypatch.setattr(backend, "search_results_all", lambda session, needle, **kw: {"artists": artists})
    monkeypatch.setattr(backend, "_artist_popularity", lambda artist: 50)
    stub.search("needle")
    return stub


def test_the_popularity_fan_out_reports_its_saturation(monkeypatch):
    """Contract 1: a new pool registers. This one is built and thrown away per
    search, so what is registered is the in-flight counter, and the counter has
    to actually move or the registration reports nothing."""
    before_peak = backend.POP_GAUGE.peak
    _run_search(monkeypatch, 6)

    assert backend.POP_GAUGE.peak > before_peak or backend.POP_GAUGE.peak >= 1
    assert backend.POP_GAUGE.activeThreadCount() == 0  # no leaked count
    assert backend.POP_GAUGE.maxThreadCount() == backend._POP_WORKERS


def test_both_bridge_fan_outs_are_handed_to_diagnostics():
    for name in ("pop", "merge"):
        assert f'diagnostics.register_pool("{name}"' in BACKEND_SRC, f"the {name} pool is not registered"


def test_the_popularity_cache_is_only_ever_written_under_the_lock(monkeypatch):
    """An older search's enrich pool keeps inserting after the gen bump (the
    gen check gates the emit, not the write), so insert and eviction have to
    share the eviction lock the other capped caches use."""
    stub = _run_search(monkeypatch, 6)

    assert stub._artist_pop_cache.unlocked_writes == 0
    assert stub._evict_lock.uses == 6  # one guarded write per artist
    assert len(stub._artist_pop_cache) == stub._ARTIST_POP_MAX  # cap held
    # The pool runs six threads, so the emits arrive in no fixed order.
    assert sorted(stub.artistMetaLoaded.emits) == sorted((f"a{i}", 50) for i in range(6))


def test_the_pop_cache_trim_no_longer_walks_the_dict_unlocked():
    """The finding's exact shape: an unlocked next(iter(...)) eviction beside a
    concurrent insert. Only _remember_capped may evict this cache."""
    assert "del self._artist_pop_cache[next(iter(" not in BACKEND_SRC


# --------------------------------------------------------------------------- #
# G-17: every clear of the shared object buckets takes the object lock
# --------------------------------------------------------------------------- #
def test_no_bucket_clear_is_left_outside_the_object_lock():
    lines = BACKEND_SRC.splitlines()
    sites = [i for i, ln in enumerate(lines) if ln.strip() == "for bucket in self._objs.values():"]
    assert len(sites) == 3, f"expected the sign-out, paste-link and search clears, found {len(sites)}"
    for i in sites:
        guard = next(ln for ln in reversed(lines[:i]) if ln.strip() and not ln.strip().startswith("#"))
        assert guard.strip().startswith("with self._objs_lock:"), f"unlocked bucket clear at line {i + 1}"


class _OpenUrlStub:
    _open_url = WavesBridge._open_url

    def __init__(self):
        self.threadpool = _HoldingPool()  # the clear happens before the worker
        self._search_gen = 0
        self._objs_lock = _WatchedLock()
        self._objs = {"artist": _WatchedDict(self._objs_lock)}
        self.searchResults = _Signal()
        self.statuses: list[str] = []
        self.busy: list[bool] = []

    def _set_status(self, text):
        self.statuses.append(text)

    def _set_busy(self, on):
        self.busy.append(bool(on))


def test_the_pasted_link_clears_the_buckets_under_the_lock():
    stub = _OpenUrlStub()
    stub._objs["artist"]["a1"] = object()

    stub._open_url("https://tidal.com/album/42")

    assert stub._objs["artist"] == {}
    assert stub._objs["artist"].unlocked_clears == 0
    assert stub._objs_lock.uses == 1


# --------------------------------------------------------------------------- #
# G-18: the membership commit leaves the GUI thread
# --------------------------------------------------------------------------- #
class _AlbumExpandStub:
    loadAlbumTracks = WavesBridge.loadAlbumTracks

    def __init__(self):
        self.threadpool = _HoldingPool()
        self._album_tracks_cache = {"al1": [{"id": "t1"}]}
        self._prefetch_lock = Lock()
        self._album_tracks_unrecorded = {"al1"}
        self._album_tracks_inflight: dict = {}
        self.albumTracksLoaded = _Signal()
        self.recorded: list = []

    def _record_album_members(self, album_id, rows):
        self.recorded.append((album_id, len(rows)))


def test_expanding_a_hovered_album_defers_the_ownership_commit():
    stub = _AlbumExpandStub()

    stub.loadAlbumTracks("al1")

    # The slot runs on the GUI thread: the rows go out at once, the commit
    # (a DELETE plus insert behind the store's lock) is handed to a worker.
    assert stub.albumTracksLoaded.emits == [("al1", [{"id": "t1"}])]
    assert stub.recorded == []
    assert len(stub.threadpool.started) == 1
    assert stub._album_tracks_unrecorded == set()

    stub.threadpool.started[0].fn()  # and the worker does do the recording
    assert stub.recorded == [("al1", 1)]


# --------------------------------------------------------------------------- #
# G-19: one tile-art crawl at a time
# --------------------------------------------------------------------------- #
class _TileArtStub:
    _sample_links_art = WavesBridge._sample_links_art
    _TILE_ART_TTL = WavesBridge._TILE_ART_TTL
    _TILE_ART_V = WavesBridge._TILE_ART_V

    def __init__(self):
        self.threadpool = _HoldingPool()
        self._tile_art_mem: dict = {}
        self._tile_art_lock = _WatchedLock()
        self._running = False
        self.set_while_unlocked = 0
        self._browse_gen = 0
        self._logged_in = True
        self.browseTileArt = _Signal()

    # The flag itself is the thing under test, so watch how it is written.
    @property
    def _tile_art_running(self):
        return self._running

    @_tile_art_running.setter
    def _tile_art_running(self, value):
        if not self._tile_art_lock.held:
            self.set_while_unlocked += 1
        self._running = value

    def _tile_art_disk(self):
        return {}


def test_a_second_tile_art_crawl_never_starts_beside_the_first():
    stub = _TileArtStub()
    links = [("Rock", "/pages/rock"), ("Jazz", "/pages/jazz")]

    stub._sample_links_art(links, 0)
    assert len(stub.threadpool.started) == 1  # the first crawl claimed the run

    # A browse page opening (or the post-login prefetch) lands while it runs.
    stub._sample_links_art(links, 0)
    assert len(stub.threadpool.started) == 1, "two crawls would race their disk snapshots"

    # Claim and release both happen under the lock, so the check-then-set the
    # finding named cannot interleave.
    assert stub.set_while_unlocked == 0
    stub.threadpool.started[0].fn()
    assert stub._tile_art_running is False
    assert stub.set_while_unlocked == 0

    stub._sample_links_art(links, 0)  # the flag really was released
    assert len(stub.threadpool.started) == 2


def test_the_crawl_flag_is_not_read_and_set_in_one_unguarded_step():
    assert not re.search(r"if not missing or self\._tile_art_running", BACKEND_SRC)
