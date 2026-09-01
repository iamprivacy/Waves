"""One album, one cover fetch: the per-job art cache in the download engine.

THE COST THIS FENCES OFF
------------------------
Tagging fetches the album cover per TRACK (metadata_write -> cover_data), so
a 20-track album downloaded the identical JPEG 20 times over HTTPS. The
engine now routes cover asks through ``cover_data_cached``, a per-Download
bounded LRU keyed by URL. Cover URLs are content-addressed (the requested
size is part of the path) and the cache dies with the job (one Download
instance = one queued item), so the always-on freshness rule holds: a cover
changed on TIDAL is picked up by the next job.

Pinned here: repeat asks hit the network once; distinct URLs each fetch; a
failed fetch is not cached (the next track retries); the cache is bounded
and evicts oldest-first; and __init__ really creates the cache fields (via a
real constructor, so a refactor cannot leave the method orphaned).
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from unittest.mock import MagicMock

from waves.download import Download


def _engine(monkeypatch, responses=None):
    """A Download whose network layer is a counting stub."""
    calls: list[str] = []

    def fake_cover_data(url=None, path_file=None):
        calls.append(url)
        if responses is not None:
            return responses.get(url, b"")
        return b"jpeg-bytes-for " + url.encode()

    d = Download.__new__(Download)
    d._cover_cache = OrderedDict()
    d._cover_cache_lock = threading.Lock()
    d._cover_cache_hits = 0
    d._cover_cache_fetches = 0
    monkeypatch.setattr(d, "cover_data", fake_cover_data)
    return d, calls


def test_twenty_tracks_one_fetch(monkeypatch):
    d, calls = _engine(monkeypatch)
    url = "https://resources.example/images/abc/1280x1280.jpg"
    results = [d.cover_data_cached(url) for _ in range(20)]
    assert len(calls) == 1
    assert all(r == results[0] for r in results)
    assert d._cover_cache_hits == 19
    assert d._cover_cache_fetches == 1


def test_distinct_urls_each_fetch(monkeypatch):
    d, calls = _engine(monkeypatch)
    d.cover_data_cached("https://resources.example/a/640x640.jpg")
    d.cover_data_cached("https://resources.example/a/1280x1280.jpg")
    assert len(calls) == 2


def test_failed_fetch_is_not_cached_and_retries(monkeypatch):
    url = "https://resources.example/a/1280x1280.jpg"
    d, calls = _engine(monkeypatch, responses={url: b""})
    assert d.cover_data_cached(url) == b""
    assert d.cover_data_cached(url) == b""
    assert len(calls) == 2  # no empty entry pinned; the next track retried
    assert url not in d._cover_cache


def test_empty_url_never_touches_the_network(monkeypatch):
    d, calls = _engine(monkeypatch)
    assert d.cover_data_cached(None) == ""
    assert d.cover_data_cached("") == ""
    assert calls == []


def test_cache_is_bounded_and_evicts_oldest(monkeypatch):
    d, calls = _engine(monkeypatch)
    urls = [f"https://resources.example/{i}/1280x1280.jpg" for i in range(Download._COVER_CACHE_MAX + 4)]
    for u in urls:
        d.cover_data_cached(u)
    assert len(d._cover_cache) == Download._COVER_CACHE_MAX
    # The oldest four were evicted; asking again re-fetches.
    n = len(calls)
    d.cover_data_cached(urls[0])
    assert len(calls) == n + 1
    # The newest is still held; asking again does not.
    d.cover_data_cached(urls[-1])
    assert len(calls) == n + 1


def test_init_creates_the_cache_fields():
    d = Download(
        tidal_obj=MagicMock(),
        skip_existing=False,
        path_base="./tmp",
        fn_logger=MagicMock(),
        progress=MagicMock(),
    )
    assert d._cover_cache == {}
    assert d._cover_cache_hits == 0
    assert d._cover_cache_fetches == 0
