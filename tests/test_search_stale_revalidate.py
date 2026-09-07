"""A search the app has answered before paints at once, then corrects itself.

The recent searches outlive the process in the page cache. One restored (or
one this session past its 90 s window) is emitted straight away, the wire is
read as always, and the fresh answer swaps in flagged ``refresh`` only when
something moved. The meters the stale page shows are carried over, and the
enrichment writes each meter back into the cached payload so the next serve
paints it with the page.
"""

from __future__ import annotations

import json
from threading import Lock
from types import SimpleNamespace
from unittest.mock import MagicMock

from conftest import _InlinePool, _Signal

from waves.waves_ui import backend
from waves.waves_ui.backend import _SEARCH_DISK_MAX, _STALE_STAMP, WavesBridge, _search_same


class _Stub:
    search = WavesBridge.search
    _search_total = staticmethod(WavesBridge._search_total)
    _pop_cached = WavesBridge._pop_cached
    _remember_capped = WavesBridge._remember_capped
    _remember_search = WavesBridge._remember_search
    _SEARCH_TTL = WavesBridge._SEARCH_TTL
    _SEARCH_CACHE_MAX = WavesBridge._SEARCH_CACHE_MAX
    _ARTIST_POP_TTL = WavesBridge._ARTIST_POP_TTL
    _ARTIST_POP_MAX = WavesBridge._ARTIST_POP_MAX

    def __init__(self):
        self.threadpool = _InlinePool()
        self.statuses: list[str] = []
        self.busy: list[bool] = []
        self.saves = 0
        self._logged_in = True
        self._search_gen = 0
        self._search_cache: dict = {}
        self._evict_lock = Lock()
        self._artist_pop_cache: dict = {}
        self._objs_lock = Lock()
        self._objs: dict = {"artist": {}, "album": {}, "track": {}, "video": {}, "playlist": {}, "mix": {}}
        self.tidal = SimpleNamespace(session=object())
        self.searchResults = _Signal()
        self.artistMetaLoaded = _Signal()

    def _set_status(self, text):
        self.statuses.append(text)

    def _set_busy(self, on):
        self.busy.append(bool(on))

    def _save_page_cache(self):
        self.saves += 1

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


def _payloads(stub):
    """The emitted payload dicts, whatever shape the signal stub keeps."""
    return [e[0] if isinstance(e, tuple) else e for e in stub.searchResults.emits]


def _payload(album_ids=("al1",), pop=-1):
    return {
        "artists": [{"id": "a1", "name": "Artist 1", "art": "", "roles": "", "popularity": pop}],
        "albums": [{"id": i} for i in album_ids],
        "tracks": [],
        "videos": [],
        "playlists": [],
        "mixes": [],
        "top": None,
    }


def _wire(monkeypatch, album_ids=("al1",), pop=50):
    artist = SimpleNamespace(id="a1", name="Artist 1")
    monkeypatch.setattr(backend, "_image", lambda obj, dimension=320: "")
    monkeypatch.setattr(backend, "_artist_roles", lambda a: "")
    monkeypatch.setattr(backend, "_artist_popularity", lambda a: pop)
    monkeypatch.setattr(
        backend,
        "search_results_all",
        lambda session, needle, **kw: {"artists": [artist], "albums": [SimpleNamespace(id=i) for i in album_ids]},
    )


def test_a_restored_search_paints_first_and_the_wire_corrects_it_in_place(monkeypatch):
    # The wire knows no meter this time (-1), so the carried-over one is
    # what the refreshed page must still show.
    _wire(monkeypatch, album_ids=("al1", "al2"), pop=-1)
    stub = _Stub()
    stub._search_cache["needle"] = (_STALE_STAMP, _payload(("al1",), pop=40))
    stub.search("needle")

    first, second = _payloads(stub)
    assert [a["id"] for a in first["albums"]] == ["al1"] and "refresh" not in first, "the old page, at once"
    assert second["refresh"] is True and [a["id"] for a in second["albums"]] == ["al1", "al2"]
    assert second["artists"][0]["popularity"] == 40, "the meter the stale page shows is carried over"
    assert True not in stub.busy, "rows are on screen the whole time: no spinner"
    assert stub.statuses[0].startswith("Searching") and stub.statuses[-1] == "3 results"
    stamp, kept = stub._search_cache["needle"]
    assert stamp > _STALE_STAMP and "refresh" not in kept and len(kept["albums"]) == 2
    assert stub.saves >= 1, "the corrected page reaches the snapshot"


def test_a_confirmed_stale_page_is_left_alone(monkeypatch):
    _wire(monkeypatch, album_ids=("al1",))
    stub = _Stub()
    stub._search_cache["needle"] = (_STALE_STAMP, _payload(("al1",), pop=40))
    stub.search("needle")
    assert len(stub.searchResults.emits) == 1, "nothing moved, so nothing is rebuilt"
    assert stub.statuses[-1] == "2 results"
    assert stub._search_cache["needle"][0] > _STALE_STAMP, "but the window is fresh again"


def test_a_stale_page_in_this_session_takes_the_same_path(monkeypatch):
    _wire(monkeypatch, album_ids=("al1", "al2"))
    monkeypatch.setattr(backend.time, "monotonic", lambda: 1000.0)
    stub = _Stub()
    stub._search_cache["needle"] = (1000.0 - WavesBridge._SEARCH_TTL - 1, _payload(("al1",)))
    stub.search("needle")
    assert [("refresh" in p) for p in _payloads(stub)] == [False, True]


def test_a_fresh_hit_is_still_served_without_the_wire(monkeypatch):
    calls = []
    monkeypatch.setattr(backend, "search_results_all", lambda *a, **k: calls.append(1) or {})
    monkeypatch.setattr(backend.time, "monotonic", lambda: 1000.0)
    stub = _Stub()
    stub._search_cache["needle"] = (999.0, _payload())
    stub.search("needle")
    assert calls == [] and len(stub.searchResults.emits) == 1


def test_a_failed_wire_never_replaces_the_page_that_had_rows(monkeypatch):
    monkeypatch.setattr(backend, "search_results_all", lambda *a, **k: {})
    stub = _Stub()
    stale = _payload(("al1",))
    stub._search_cache["needle"] = (_STALE_STAMP, stale)
    stub.search("needle")
    assert len(stub.searchResults.emits) == 1, "an empty answer is more likely a failure than a change"
    assert stub._search_cache["needle"] == (_STALE_STAMP, stale)


def test_the_enrichment_writes_the_meter_into_the_cached_page(monkeypatch):
    _wire(monkeypatch, pop=73)
    stub = _Stub()
    stub.search("needle")
    assert stub._search_cache["needle"][1]["artists"][0]["popularity"] == 73
    assert stub.saves == 2, "once with the rows, once more with the meters"


def test_same_page_ignores_the_meters_and_the_flag():
    a = _payload(pop=-1)
    b = {**_payload(pop=88), "refresh": True}
    assert _search_same(a, b)
    assert not _search_same(a, _payload(("al1", "al2")))


def _cache_bridge(tmp_path):
    b = WavesBridge.__new__(WavesBridge)
    b._logged_in = True
    b._lib_cache = {}
    b._lib_sort = {}
    b._browse_root_cache = None
    b._browse_pages = {}
    b._artist_cache = {}
    b._search_cache = {}
    b._home_cache = None
    b._page_cache_path = str(tmp_path / "page_cache.json")
    b._page_cache_lock = Lock()
    b.tidal = MagicMock()
    b.tidal.session.user.id = "42"
    return b


def test_the_newest_searches_outlive_the_process_and_come_back_stale(tmp_path):
    saver = _cache_bridge(tmp_path)
    for i in range(_SEARCH_DISK_MAX + 3):
        saver._search_cache[f"needle {i}"] = (float(i), _payload((f"al{i}",)))
    saver._save_page_cache()
    with open(saver._page_cache_path) as fh:
        on_disk = json.load(fh)["searches"]
    assert len(on_disk) == _SEARCH_DISK_MAX and "needle 0" not in on_disk and "needle 14" in on_disk
    assert on_disk["needle 14"] == _payload(("al14",)), "the payload alone, no stamp"

    loader = _cache_bridge(tmp_path)
    loader._load_page_cache()
    assert len(loader._search_cache) == _SEARCH_DISK_MAX
    stamp, page = loader._search_cache["needle 14"]
    assert stamp == _STALE_STAMP and page == _payload(("al14",))


def test_a_live_search_is_never_clobbered_by_the_snapshot(tmp_path):
    saver = _cache_bridge(tmp_path)
    saver._search_cache["needle"] = (1.0, _payload(("old",)))
    saver._save_page_cache()
    loader = _cache_bridge(tmp_path)
    loader._search_cache["needle"] = (5.0, _payload(("live",)))
    loader._load_page_cache()
    assert loader._search_cache["needle"] == (5.0, _payload(("live",)))
