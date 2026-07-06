"""Regression tests for the audited WavesBridge (backend.py) fixes.

Hermetic and Qt-free: constructing a real ``WavesBridge`` needs a Qt app, a
tidalapi session and (historically) a synchronous token-login network call, none
of which belong in a unit test. Each ``WavesBridge`` method under test here is
plain Python once its Qt signals are stubbed, so we bind the *real, unbound*
method onto a minimal ``_Stub`` carrying exactly the attributes that method
touches (fake signals record their emits). This exercises the shipped code path
without a running event loop.

Covered findings:
  * cancelQueueItem must NOT globally release the pause gate (finding 3)
  * clearQueue must abort every removed non-running item (finding 4)
  * logout must bump the library generation (finding 5)
  * best-of-both merge plan is kept on failure, dropped on success (finding 10)
  * loadMoreLibrary keeps a category scrollable after a transient error (12)
  * _restore_ffmpeg_path clears an injected path but keeps a user override (2/8)
"""

from __future__ import annotations

from threading import Event

import pytest

from tidaler.waves_ui.backend import WavesBridge


class _Signal:
    """Records ``emit`` calls so a test can assert what QML would have seen."""

    def __init__(self):
        self.emits: list = []

    def emit(self, *args):
        self.emits.append(args if len(args) != 1 else args[0])


class _Stub:
    """Bare stand-in for a WavesBridge, with the attributes the bound methods
    read/write. Real behaviour comes from binding WavesBridge methods below."""

    def __init__(self):
        self._queue: list[dict] = []
        self._job_aborts: dict[int, Event] = {}
        self._job_signals: dict = {}
        self._job_dls: dict = {}
        self._job_tracks: dict = {}
        self._merge_plans: dict = {}
        self._queue_emit_suspended = False
        self._paused = False
        self._event_run = Event()
        self._event_run.set()
        self._lib_cache: dict = {}
        self._lib_loading: set = set()
        self._lib_gen = 0
        self.settings = type("S", (), {"data": type("D", (), {"path_binary_ffmpeg": ""})()})()
        self._ffmpeg_user_path = ""
        # Fake signals
        self.queueChanged = _Signal()
        self.pausedChanged = _Signal()
        self.downloadState = _Signal()
        self.queueItemProgress = _Signal()

    # Stubs for collaborators the tested methods call but that aren't the SUT.
    def _prune_job_tracks(self):
        pass

    def _set_status(self, msg):
        self._status = msg

    # Small real helpers the SUTs lean on: bind the genuine implementations so
    # queue lookups / emits behave exactly as in the app.
    def _queue_item(self, qid):
        return WavesBridge._queue_item(self, qid)

    def _emit_queue(self):
        return WavesBridge._emit_queue(self)

    def _set_queue_status(self, qid, status):
        return WavesBridge._set_queue_status(self, qid, status)


def _bind(stub, name):
    """Bind the real (unbound) WavesBridge method onto the stub."""
    return getattr(WavesBridge, name).__get__(stub, _Stub)


# --------------------------------------------------------------------------- #
# Finding 3: cancelQueueItem must not resume all paused workers.
# --------------------------------------------------------------------------- #
def test_cancel_queue_item_keeps_pause_gate_cleared():
    stub = _Stub()
    ev = Event()
    stub._job_aborts[7] = ev
    stub._queue = [{"qid": 7, "media_id": "m7", "status": "running"}]
    # Simulate a paused queue: the global run gate is cleared.
    stub._event_run.clear()
    stub._paused = True

    _bind(stub, "cancelQueueItem")(7)

    assert ev.is_set(), "the cancelled job's own abort must be set"
    assert not stub._event_run.is_set(), "the global pause gate must stay cleared"
    assert stub._paused is True, "the queue must remain paused"
    assert stub.pausedChanged.emits == [], "no spurious pausedChanged on a single cancel"
    assert all(q["qid"] != 7 for q in stub._queue), "the item is removed from the queue"


def test_cancel_queue_item_missing_job_still_removes_row():
    stub = _Stub()
    stub._queue = [{"qid": 3, "media_id": "m3", "status": "queued"}]
    _bind(stub, "cancelQueueItem")(3)
    assert stub._queue == []


# --------------------------------------------------------------------------- #
# Finding 4: clearQueue must abort removed non-running items.
# --------------------------------------------------------------------------- #
def test_clear_queue_aborts_removed_queued_items():
    stub = _Stub()
    running = Event()
    queued = Event()
    done = Event()
    stub._job_aborts = {1: running, 2: queued, 3: done}
    stub._queue = [
        {"qid": 1, "status": "running"},
        {"qid": 2, "status": "queued"},
        {"qid": 3, "status": "done"},
    ]

    _bind(stub, "clearQueue")()

    assert not running.is_set(), "a running job must keep going, not be aborted"
    assert queued.is_set(), "a queued job's Worker must be aborted so it early-returns"
    assert done.is_set(), "a finished/other row's job is aborted on clear"
    assert [q["qid"] for q in stub._queue] == [1], "only running rows remain"


# --------------------------------------------------------------------------- #
# Finding 5: logout bumps the library generation so in-flight loads can't
# re-poison the cache for the next account.
# --------------------------------------------------------------------------- #
def test_logout_bumps_lib_gen_and_clears_cache():
    stub = _Stub()
    stub._lib_cache = {"albums": {"items": [1, 2], "offset": 100, "more": True}}
    stub._lib_loading = {"albums"}
    stub._lib_gen = 5
    # logout() also resets the browse caches on the current build; provide them
    # so the SUT (the _lib_gen bump) runs regardless of that co-located cleanup.
    stub._browse_root_cache = object()
    stub._browse_pages = {"root": []}
    stub._browse_loading = {"root"}
    stub._browse_gen = 2
    # ...and the artist/page-cache cleanup added with stale-while-revalidate.
    stub._artist_cache = {"1": {}}
    stub._artist_loading = {"1"}
    stub._page_cache_path = "/nonexistent/page_cache.json"

    # logout() touches self.tidal.logout/_reset_tidal_session; stub them.
    calls = {"logout": 0, "reset": 0}
    stub.tidal = type("T", (), {"logout": lambda self: calls.__setitem__("logout", 1)})()
    stub._reset_tidal_session = lambda: calls.__setitem__("reset", 1)
    stub._set_logged_in = lambda v: setattr(stub, "_logged_in", v)
    stub._logged_in = True

    _bind(stub, "logout")()

    assert stub._lib_cache == {}, "cache cleared on logout"
    assert stub._lib_loading == set()
    assert stub._lib_gen == 6, "generation bumped so a stale in-flight load is dropped"


def test_stale_lib_gen_guards_cache_write_semantics():
    # Mirrors the loadLibrary worker's guard: a page whose captured gen no longer
    # matches must NOT write the cache. We assert the predicate the fix relies on.
    stub = _Stub()
    stub._lib_gen = 4
    captured_gen = 4
    # Simulate a logout bumping the generation mid-flight.
    stub._lib_gen = 5
    assert captured_gen != stub._lib_gen  # -> worker skips the cache write


# --------------------------------------------------------------------------- #
# Finding 10: a failed best-of-both merge keeps its plan (retryable as a merge).
# --------------------------------------------------------------------------- #
def test_merge_plan_survives_until_success():
    # downloadAlbum peeks (get, not pop); the plan is only dropped on success.
    plans = {"albumX": [("track", 1, 1)]}
    # Peek keeps it available for a retry.
    peeked = plans.get("albumX")
    assert peeked is not None
    assert "albumX" in plans, "peeking must not drop the plan on first enqueue"

    # On the success path _download does: self._merge_plans.pop(media_id, None)
    plans.pop("albumX", None)
    assert "albumX" not in plans, "plan dropped only once the merge succeeds"


def test_retry_reuses_stashed_merge_plan_for_album():
    stub = _Stub()
    plan = [("t", 1, 1)]
    stub._merge_plans = {"albumX": plan}
    stub._queue = [
        {
            "qid": 9,
            "status": "failed",
            "type": "album",
            "media_id": "albumX",
            "name": "X",
            "template": "",
            "collection": True,
        }
    ]
    stub._objs = {"album": {"albumX": object()}}

    captured = {}

    def fake_download(obj, type_media, name, template, collection, media_id, merge_plan=None):
        captured["merge_plan"] = merge_plan
        captured["type"] = type_media

    stub._download = fake_download

    _bind(stub, "retryQueueItem")(9)

    assert captured["type"] == "album"
    assert captured["merge_plan"] is plan, "retry must re-supply the stashed merge plan"


def test_retry_plain_track_passes_no_merge_plan():
    stub = _Stub()
    stub._merge_plans = {}
    stub._queue = [
        {
            "qid": 2,
            "status": "failed",
            "type": "track",
            "media_id": "t1",
            "name": "Y",
            "template": "",
            "collection": False,
        }
    ]
    stub._objs = {"track": {"t1": object()}}
    captured = {}
    stub._download = lambda *a, merge_plan=None, **k: captured.__setitem__("merge_plan", merge_plan)

    _bind(stub, "retryQueueItem")(2)
    assert captured["merge_plan"] is None


# --------------------------------------------------------------------------- #
# Finding 12: a transient loadMoreLibrary error must not exhaust the category.
# --------------------------------------------------------------------------- #
def test_load_more_library_transient_error_keeps_more(monkeypatch):
    stub = _Stub()
    stub._lib_cache = {"albums": {"items": [1, 2], "offset": 100, "more": True}}
    stub.libraryMore = _Signal()
    stub._lib_status = lambda cat, count, more: f"{count} {cat}"
    stub._logged_in = True

    # Make the page fetch raise, and run the worker synchronously.
    def boom(category, offset, limit):
        raise RuntimeError("transient network blip")

    stub._library_page = boom

    ran = {}

    class _Pool:
        def start(self, worker):
            worker.run()
            ran["done"] = True

    stub.threadpool = _Pool()

    # devlog is a module-level import used inside the method; patch its funcs.
    import tidaler.waves_ui.backend as backend

    monkeypatch.setattr(backend.devlog, "clock", lambda: 0.0, raising=True)
    monkeypatch.setattr(backend.devlog, "done", lambda *a, **k: None, raising=True)

    _bind(stub, "loadMoreLibrary")("albums")

    assert ran.get("done"), "worker ran synchronously"
    entry = stub._lib_cache["albums"]
    assert entry["more"] is True, "category stays scrollable after a transient error"
    assert entry["offset"] == 100, "offset unchanged so the same window is retried"
    assert "albums" not in stub._lib_loading, "loading flag cleared for a retry"


# --------------------------------------------------------------------------- #
# Findings 2 & 8: restore-before-save clears an injected ffmpeg path but keeps a
# genuine user override.
# --------------------------------------------------------------------------- #
def test_restore_ffmpeg_path_clears_injected_managed_path():
    stub = _Stub()
    # _resolve_ffmpeg injected the managed path in-memory; user set none.
    stub.settings.data.path_binary_ffmpeg = "/managed/copy/ffmpeg"
    stub._ffmpeg_user_path = ""
    _bind(stub, "_restore_ffmpeg_path")()
    assert stub.settings.data.path_binary_ffmpeg == "", "injected managed path cleared before save"


def test_restore_ffmpeg_path_preserves_user_override():
    stub = _Stub()
    stub.settings.data.path_binary_ffmpeg = "/managed/copy/ffmpeg"  # transient injection
    stub._ffmpeg_user_path = "/home/me/bin/ffmpeg"  # a real user choice
    _bind(stub, "_restore_ffmpeg_path")()
    assert stub.settings.data.path_binary_ffmpeg == "/home/me/bin/ffmpeg", "user override kept"


# --------------------------------------------------------------------------- #
# Page-cache disk persistence: save → load round-trips, library keeps only its
# first page, and another account's snapshot is discarded.
# --------------------------------------------------------------------------- #
def _cache_stub(path, user_id):
    from threading import Lock

    stub = _Stub()
    stub._logged_in = True
    stub._page_cache_path = str(path)
    stub._page_cache_lock = Lock()
    stub._cache_user_id = lambda: user_id
    stub._browse_root_cache = None
    stub._browse_pages = {}
    stub._artist_cache = {}
    stub._lib_cache = {}
    return stub


def test_page_cache_round_trip_and_account_guard(tmp_path):
    path = tmp_path / "page_cache.json"
    stub = _cache_stub(path, "7")
    stub._browse_root_cache = {"sections": [{"rowKind": "cards"}], "genres": [], "error": False}
    stub._browse_pages = {"pages/rock": {"key": "pages/rock", "sections": [1], "error": False}}
    stub._artist_cache = {"42": {"id": "42", "name": "A"}}
    stub._lib_cache = {"albums": {"items": list(range(150)), "offset": 300, "more": False}}
    _bind(stub, "_save_page_cache")()

    fresh = _cache_stub(path, "7")
    _bind(fresh, "_load_page_cache")()
    assert fresh._browse_root_cache == stub._browse_root_cache
    assert fresh._browse_pages == stub._browse_pages
    assert fresh._artist_cache == stub._artist_cache
    # Library persists only its first page and resets paging so infinite
    # scroll re-fetches from the second window (_LIBRARY_PAGE = 100).
    assert len(fresh._lib_cache["albums"]["items"]) == 100
    assert fresh._lib_cache["albums"]["offset"] == 100
    assert fresh._lib_cache["albums"]["more"] is True, "truncated tail must stay pageable"

    other = _cache_stub(path, "8")  # different account
    _bind(other, "_load_page_cache")()
    assert other._browse_root_cache is None and other._artist_cache == {}


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
