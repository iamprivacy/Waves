"""A failed re-fetch must not strand a discography group at "running".

_refetch_for_download is the fallback when a download is requested for an id
whose live object was evicted from _objs (a new search clears every bucket; a
discography video scan can even fill a whole bucket by itself, evicting its
own earliest members). When that re-fetch fails, the item never enters the
queue, yet its id stays in the artist group's key set. Without a "failed"
bump on the failure exits, len(done) can never reach len(keys): the artist
button spins forever and the group dict leaks for the session.

These tests bind the REAL _refetch_for_download and the real group bump
helpers over an inline pool, and require both failure exits (fetch failed,
and account switched mid-fetch) to settle the group.
"""

from __future__ import annotations

from threading import Lock
from types import SimpleNamespace

from waves.waves_ui.backend import WavesBridge


class _Stub:
    """Bare object the real methods get bound onto."""


def _bind(stub, name):
    return getattr(WavesBridge, name).__get__(stub, type(stub))


class _Sig:
    def __init__(self):
        self.emits: list = []

    def emit(self, *args):
        self.emits.append(args)


class _InlinePool:
    def start(self, worker):
        worker.fn()


def _stub(session_video):
    stub = _Stub()
    stub._refetch_inflight = set()
    stub._logged_in = True
    stub._browse_gen = 1
    stub._browse_lock = Lock()
    stub.tidal = SimpleNamespace(session=SimpleNamespace(video=session_video))
    stub.threadpool = _InlinePool()
    stub.downloadState = _Sig()
    stub.downloadProgress = _Sig()
    stub.statuses: list = []
    stub._set_status = stub.statuses.append
    # A one-member discography group: the strand is visible immediately.
    stub._artist_groups = {"art1": {"keys": {"9"}, "done": set(), "failed": set(), "prog": {}}}
    stub._artist_lock = Lock()
    stub._folder_groups = {}
    stub._folder_lock = Lock()
    stub._scan_gen = 0
    stub._bump_download_groups = _bind(stub, "_bump_download_groups")
    stub._bump_artist_group = _bind(stub, "_bump_artist_group")
    stub._bump_folder_group = _bind(stub, "_bump_folder_group")
    stub._refetch_for_download = _bind(stub, "_refetch_for_download")
    return stub


def test_a_failed_refetch_settles_its_group_as_failed():
    def _raise(_vid):
        raise RuntimeError("429")

    stub = _stub(_raise)
    stub._refetch_for_download("video", "9")
    assert stub._artist_groups == {}, "the finished group must be deleted, not leak"
    assert ("art1", "failed") in stub.downloadState.emits, "the artist button must leave 'running'"
    assert ("9", "failed") in stub.downloadState.emits
    assert stub._refetch_inflight == set()


def test_an_account_switch_mid_fetch_settles_the_group_too():
    stub = _stub(lambda _vid: SimpleNamespace(id=9))

    # The fetch succeeds, but the account generation moved on underneath it:
    # the download must not start, and the group must still settle.
    real_video = stub.tidal.session.video

    def _switch(vid):
        stub._browse_gen = 2
        return real_video(vid)

    stub.tidal.session.video = _switch
    stub._refetch_for_download("video", "9")
    assert stub._artist_groups == {}, "the finished group must be deleted, not leak"
    assert ("art1", "failed") in stub.downloadState.emits
    assert stub._refetch_inflight == set()
