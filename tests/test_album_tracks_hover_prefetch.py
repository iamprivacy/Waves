"""An album row's tracks are fetched on hover, so the expand opens on them.

THE BUG WE ARE FENCING OFF
--------------------------
Clicking an album row in search results expanded the panel first and
fetched its tracks second, so the rows popped in visibly after the panel
had already opened. The hover prefetch that warms playlist, mix and album
PAGES did not cover the inline expand: that path has its own cache and its
own fetch (loadAlbumTracks).

HOW THIS STAYS FIXED
--------------------
``prefetchAlbumTracks`` runs the same worker as ``loadAlbumTracks`` into
the same session cache, silently: no emit, no membership record. An expand
served from a hover-filled cache emits at once and records the membership
then (the hover never does), on a worker: the record is a database commit,
which is not the GUI thread's work. An expand that lands while a hover fetch
is still running claims it instead of fetching the same album twice.
"""

from __future__ import annotations

from threading import Lock
from types import SimpleNamespace

from waves.waves_ui import backend


class _DeferredPool:
    """Holds workers so a test can interleave calls before a fetch lands."""

    def __init__(self):
        self.pending = []

    def start(self, worker, priority: int = 0):
        self.pending.append(worker)

    def run_all(self):
        while self.pending:
            self.pending.pop(0).fn()


class _Signal:
    def __init__(self):
        self.calls = []

    def emit(self, *args):
        self.calls.append(args)


def _track(tid: str):
    return SimpleNamespace(id=tid, name=f"Track {tid}", duration=200, popularity=10, explicit=False)


def _album(tracks):
    return SimpleNamespace(id=7, tracks=lambda: list(tracks))


def _bridge(album=None):
    b = backend.WavesBridge.__new__(backend.WavesBridge)
    b._logged_in = True
    b._objs = {"album": {}, "track": {}}
    b._objs_lock = Lock()
    b._prefetch_lock = Lock()
    b._album_tracks_cache = {}
    b._album_tracks_inflight = {}
    b._album_tracks_unrecorded = set()
    b.threadpool = _DeferredPool()
    b.albumTracksLoaded = _Signal()
    b.collectionMembershipChanged = _Signal()
    b._ownership = SimpleNamespace(record_members_replace=_Signal().emit)
    b._recorded = []
    b._ownership.record_members_replace = lambda aid, ids: b._recorded.append((aid, list(ids)))
    b._remember = lambda kind, key, obj: b._objs[kind].__setitem__(key, obj)
    b._remember_album_tracks = lambda aid, rows: b._album_tracks_cache.__setitem__(aid, rows)
    if album is not None:
        b._objs["album"]["7"] = album
    return b


def test_a_hover_fills_the_cache_silently_and_the_expand_serves_it_at_once():
    b = _bridge(_album([_track("1"), _track("2")]))
    b.prefetchAlbumTracks("7")
    b.threadpool.run_all()
    assert [r["id"] for r in b._album_tracks_cache["7"]] == ["1", "2"]
    assert b.albumTracksLoaded.calls == []  # nobody was watching
    assert b._recorded == []  # membership is the expand's job
    assert "7" not in b._album_tracks_inflight

    b.loadAlbumTracks("7")
    # The rows go out at once (no second fetch, the cache had them); the only
    # deferred work is the membership commit, off the GUI thread.
    assert len(b.albumTracksLoaded.calls) == 1 and b.albumTracksLoaded.calls[0][0] == "7"
    assert b._recorded == []
    assert len(b.threadpool.pending) == 1
    b.threadpool.run_all()
    assert b._recorded == [("7", ["1", "2"])]
    assert b.collectionMembershipChanged.calls == [("7",)]
    # Recorded once: the next expand is a plain cache hit.
    b.loadAlbumTracks("7")
    b.threadpool.run_all()
    assert len(b._recorded) == 1 and len(b.albumTracksLoaded.calls) == 2


def test_an_expand_mid_hover_fetch_claims_it_instead_of_fetching_twice():
    b = _bridge(_album([_track("1")]))
    b.prefetchAlbumTracks("7")
    assert len(b.threadpool.pending) == 1
    b.loadAlbumTracks("7")  # the user clicked while the hover fetch runs
    assert len(b.threadpool.pending) == 1  # rode on it
    b.threadpool.run_all()
    assert len(b.albumTracksLoaded.calls) == 1  # the worker emitted for the expand
    assert b._recorded == [("7", ["1"])]  # and recorded, as an expand would
    assert "7" not in b._album_tracks_unrecorded


def test_a_hover_is_a_no_op_when_cached_in_flight_or_busy():
    b = _bridge(_album([_track("1")]))
    b._album_tracks_cache["7"] = [{"id": "1"}]
    b.prefetchAlbumTracks("7")
    assert b.threadpool.pending == []  # cached

    b2 = _bridge(_album([_track("1")]))
    b2._objs["album"]["8"] = _album([_track("9")])
    b2.prefetchAlbumTracks("7")
    b2.prefetchAlbumTracks("7")  # same album again
    b2.prefetchAlbumTracks("8")  # one unwatched fetch at a time
    assert len(b2.threadpool.pending) == 1

    b3 = _bridge(_album([_track("1")]))
    b3._logged_in = False
    b3.prefetchAlbumTracks("7")
    assert b3.threadpool.pending == []


def test_an_empty_hover_fetch_is_never_cached_so_the_expand_retries():
    b = _bridge(_album([]))
    b.prefetchAlbumTracks("7")
    b.threadpool.run_all()
    assert "7" not in b._album_tracks_cache
    assert "7" not in b._album_tracks_inflight
    assert b.albumTracksLoaded.calls == []
    b.loadAlbumTracks("7")
    assert len(b.threadpool.pending) == 1  # a real fetch for the expand
    b.threadpool.run_all()
    assert b.albumTracksLoaded.calls == [("7", [])]  # the row hears the answer, empty or not
