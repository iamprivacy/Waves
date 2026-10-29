"""Tests for the Releases bridge family (waves/waves_ui/bridge_releases.py).

The store is covered by test_follow_store.py; here the Qt layer over it: a
step fetches one artist's newest releases with sibling ids and tells a rate
limit from a failed section, the pacer picks the stalest follow and backs
off on a 429, a step with new releases arms the notification and the
auto-download, and the gates (signed in AND revealed) open and close it.

Qt-free and hermetic, in the style of test_ownership_ask.py: the real,
unbound bridge methods are bound onto a minimal _Stub carrying only what
they touch.
"""

from __future__ import annotations

import collections
import itertools
import logging
import time
from types import SimpleNamespace

import pytest
from conftest import _InlinePool, _Signal

from waves.follows import DL_QUEUED, DL_WAITING, FollowStore
from waves.release_delta import collapse_atmos_twins
from waves.waves_ui import bridge_releases as rel
from waves.waves_ui import diagnostics
from waves.waves_ui.backend import WavesBridge


class _FakeNotifier:
    def __init__(self, ok=True):
        self.sent = []
        self.ok = ok

    def send(self, title, message):
        self.sent.append((title, message))
        return self.ok


class _Timer:
    def __init__(self):
        self._active = False
        self.starts: list = []
        self.stops = 0

    def isActive(self):
        return self._active

    def start(self, ms=None):
        self._active = True
        self.starts.append(ms)

    def stop(self):
        self._active = False
        self.stops += 1


class _Album(SimpleNamespace):
    pass


class _TooManyRequests(Exception):
    pass


class _Stub:
    pass


def _bind(stub, name):
    return getattr(WavesBridge, name).__get__(stub, _Stub)


for _m in (
    "_releases_maybe_start",
    "_releases_on_gui_thread",
    "_releases_stop",
    "_releases_rearm",
    "_releases_queue_busy",
    "_releases_step_ms",
    "_releases_tick",
    "_releases_check_one",
    "_artist_recent_releases",
    "_releases_deep_read",
    "_releases_deep_pages",
    "_releases_deep_due",
    "_releases_past_head",
    "_releases_deep_ask",
    "_on_rel_deep_done",
    "_releases_mb_due",
    "_releases_extra_turn",
    "_releases_mb_check",
    "_releases_mb_client",
    "_on_rel_mb_done",
    "_releases_musicbrainz_switched",
    "_releases_artist_obj",
    "_releases_section_rows",
    "_releases_keep_from",
    "_releases_window_changed",
    "_on_rel_step_done",
    "_releases_announce",
    "_on_rel_mirror_done",
    "_on_rel_notify",
    "_releases_notify",
    "_notify_send",
    "_releases_forget_waiting",
    "_releases_today",
    "_releases_auto_download",
    "_releases_auto_download_joined",
    "_releases_drain_auto_downloads",
    "_releases_queue_release",
    "_releases_queue_twin",
    "_releases_drain_atmos_twins",
    "_releases_atmos_on",
    "_releases_on_download_state",
    "loadReleases",
    "releasesUnseenCount",
    "acknowledgeAllReleases",
    "followedCount",
    "isFollowed",
    "followInfo",
    "followArtist",
    "unfollowArtist",
    "loadFollowed",
    "_releases_followed_fresh",
    "_releases_followed_forget",
    "searchArtists",
    "_releases_mirror_walk",
    "_releases_mirror_store",
    "_releases_walk_failed",
    "_releases_walk_retry_due",
    "_releases_walk_pages",
    "_releases_note_favorites",
    "_releases_mirror_to_tidal",
    "_releases_tidal_enqueue",
    "_releases_tidal_check_gen",
    "_releases_tidal_arm",
    "_releases_tidal_drain",
    "_releases_tidal_write",
    "_on_rel_tidal_done",
    "followOnTidal",
    "tidalFavouritePending",
    "followMirrorsTidal",
    "_releases_mirror_switched",
    "_releases_signed_out",
    "_releases_back_off",
    "_releases_library_cache",
    "startArtistImport",
    "cancelArtistImport",
    "_releases_import_alive",
    "_releases_import_scan",
    "_releases_import_run",
    "_releases_import_tags",
    "_releases_import_searcher",
    "_releases_import_pacer",
    "_releases_import_catalog",
    "_releases_import_library",
    "_releases_min_tracks",
    "_releases_enough_tracks",
    "_releases_import_floor_changed",
    "_releases_thin_follows",
    "followsUnderFloor",
    "unfollowUnderFloor",
    "loadExcluded",
    "_releases_held",
    "_releases_excluded_run",
    "_releases_excluded_rows",
    "_releases_folder_cover",
    "startArtistImportNames",
    "confirmArtistPicks",
    "_releases_confirm_picks",
    "_releases_import_payload",
    "_on_rel_import_done",
    "confirmArtistImport",
    "artistImportTray",
    "artistImportTrayCount",
    "dismissArtistImportTray",
    "_releases_import_begin",
    "startArtistImportMore",
    "_releases_seen_norms",
    "_releases_seen_add",
    "_releases_set_notice",
    "_releases_library_moved",
    "_releases_more_count",
    "_on_rel_more_counted",
    "_releases_import_auto_settle",
    "_releases_auto_import_switched",
    "artistImportNoticeNow",
    "dismissArtistImportNotice",
    "_waves_pref_bool",
    "_release_type",
    "_releases_check_videos",
    "_artist_recent_videos",
    "_releases_video_rows",
    "_releases_videos_on",
    "_releases_videos_switched",
    "_releases_featured_on",
    "_releases_featured_switched",
    "_releases_drop_featured",
    "releasesCheckNow",
    "releasesCheckNowReady",
    "_releases_extra_rearm",
    "_releases_extra_pending",
    "releasesUnchecked",
    "_releases_due_from",
    "_releases_video_download_on",
    "_releases_queue_video",
    "searchReleases",
    "_releases_auto_kinds",
    "_releases_kind_wanted",
    "_releases_guest_on",
    "_releases_guest_switched",
    "_releases_auto_download_guests",
    "_releases_queue_guest",
    "_releases_check_guests",
    "_artist_recent_guests",
):
    if hasattr(WavesBridge, _m):
        setattr(_Stub, _m, getattr(WavesBridge, _m))


def _stub(tmp_path, *, prefs=None):
    s = _Stub()
    s._releases = FollowStore(str(tmp_path / "releases.sqlite3"))
    s._waves_prefs = {
        # Armed: the notify tests here are about an armed Waves's banners.
        "notify_enabled": True,
        "notify_in_front": True,
        "releases_notify": True,
        "releases_auto_import": False,
        "releases_auto_download": False,
        "follow_mirror_tidal": False,
        "releases_window_months": "12",
        "releases_musicbrainz": False,
        **(prefs or {}),
    }
    s._logged_in = True
    s._rel_revealed = True
    s._rel_gen = 0
    s._rel_step_running = False
    s._rel_import_running = False
    s._rel_backoff_until = 0.0
    s._rel_backoff_level = 0
    s._rel_mirror_due = False
    s._rel_notify_pending = 0
    s._rel_notify_items = []
    s._notifier = _FakeNotifier()
    s._notify_pool = _InlinePool()
    s._notify_waves_in_front = lambda: True
    s._rel_round_done = 0
    s._rel_deep_queue = collections.deque()
    s._rel_deep_turn = False
    s._rel_extra_spent = False
    s._rel_step_at = 0.0
    s._rel_mb_client = None
    s._rel_timer = _Timer()
    s._rel_notify_timer = _Timer()
    s._rel_pool = _InlinePool()
    s.threadpool = _InlinePool()
    s._job_aborts = {}
    s._objs = {"artist": {}}
    s.releasesChanged = _Signal()
    s.releasesLoaded = _Signal()
    s.releasesSearched = _Signal()
    s.releasesProgress = _Signal()
    s.followStateChanged = _Signal()
    s.followedLoaded = _Signal()
    s.excludedLoaded = _Signal()
    s.artistSearchResults = _Signal()
    s._rel_search_gen = 0
    s._relStepDone = _Signal()
    s._relMirrorDone = _Signal()
    s._relTidalDone = _Signal()
    s.followTidalChanged = _Signal()
    s.followFloorChanged = _Signal()
    s._rel_tidal_out = {}
    s._rel_tidal_inflight = set()
    s._rel_tidal_gen = 0
    s._rel_tidal_last = 0.0
    s._rel_tidal_sent = collections.deque()
    s._rel_tidal_backoff_until = 0.0
    s._rel_tidal_timer = _Timer()
    s._rel_tidal_hand = set()
    s._rel_tidal_base = {}
    s._rel_tidal_pool = _InlinePool()
    s._rel_fav_known_gen = 0  # this account's favorites already walked
    s._rel_import_gen = 0
    s._rel_auto_watch = set()
    s._rel_auto_tries = {}
    s._rel_auto_videos = set()
    s.artistImportProgress = _Signal()
    s.artistImportReady = _Signal()
    s.artistImportError = _Signal()
    s.artistImportDone = _Signal()
    s._relImportDone = _Signal()
    s._rel_import_auto = False
    s._rel_lib_stamp_seen = -1
    s._rel_auto_last = 0.0
    s._rel_notice = {"kind": "", "n": 0}
    s._rel_more_norms = []
    s.artistImportNotice = _Signal()
    s._relMoreCounted = _Signal()
    s.queued: list = []
    s.downloadAlbum = lambda rid: s.queued.append(rid)
    s.queued_videos: list = []
    s.downloadVideo = lambda vid: s.queued_videos.append(vid)
    s._scan_gen = 0
    s.queued_tracks: list = []
    s._tracksQueued = SimpleNamespace(emit=lambda gen, keys: s.queued_tracks.extend(keys))
    s._featured_only = lambda raw: [a for a in raw if not getattr(a, "comp", False)]
    s._guest_track_keys = lambda rid, aid: [f"{rid}:{aid}"]
    return s


def _fake_artist(albums=(), eps=(), raise_on=None):
    def albums_fn(limit=None):
        if raise_on == "albums":
            raise raise_on_exc
        return list(albums)

    def eps_fn(limit=None):
        if raise_on == "eps":
            raise raise_on_exc
        return list(eps)

    return SimpleNamespace(get_albums=albums_fn, get_ep_singles=eps_fn)


raise_on_exc: Exception = RuntimeError("boom")


def _releases_stub(s, artist):
    s._objs["artist"]["A"] = artist
    s._dedup_albums = lambda xs: [a for a in xs if not getattr(a, "dup", False)]
    s._album_key = lambda a: a.title
    s._album_dict = lambda a: {
        "id": a.id,
        "title": a.title,
        "artist": "Artist",
        "art": "u",
        "year": "2026",
        "date": getattr(a, "date", "2026-10-01"),
        "rtype": getattr(a, "rtype", "album"),
        "tracks": getattr(a, "tracks", 10),
        "quality": getattr(a, "quality", "LOSSLESS"),
    }
    s._dedup_videos = lambda xs: list(xs)
    s._video_dict = lambda v: {
        "id": str(v.id),
        "title": v.title,
        "artist": "Artist",
        "artists": [],
        "art": "small",
        "art_big": "big",
        "date": getattr(v, "date", "2026-10-01"),
        "explicit": False,
        "quality": "1080p",
    }
    return s


# --- _artist_recent_releases -------------------------------------------------


def test_recent_releases_carry_sibling_ids_and_use_the_fetch_limit(tmp_path):
    seen_limits = []

    def albums(limit=None):
        seen_limits.append(limit)
        return [_Album(id="1", title="LP"), _Album(id="1b", title="LP", dup=True)]

    artist = SimpleNamespace(get_albums=albums, get_ep_singles=lambda limit=None: [_Album(id="2", title="S")])
    s = _releases_stub(_stub(tmp_path), artist)
    rows, failures, limited = _bind(s, "_artist_recent_releases")("A")
    assert [r["id"] for r in rows] == ["1", "2"]
    assert rows[0]["sibling_ids"] == ["1", "1b"] and rows[1]["sibling_ids"] == ["2"]
    assert failures == [] and limited is False
    assert seen_limits == [rel._REL_FETCH_LIMIT]


def test_recent_releases_fold_an_atmos_twin_into_its_stereo_edition(tmp_path):
    albums = [_Album(id="1", title="LP"), _Album(id="1x", title="LP", quality="ATMOS")]
    artist = SimpleNamespace(get_albums=lambda limit=None: albums, get_ep_singles=lambda limit=None: [])
    s = _releases_stub(_stub(tmp_path), artist)
    rows, failures, limited = _bind(s, "_artist_recent_releases")("A")
    assert [r["id"] for r in rows] == ["1"] and failures == [] and limited is False
    assert rows[0]["sibling_ids"] == ["1", "1x"]


def test_recent_releases_reports_a_failed_section(tmp_path):
    global raise_on_exc
    raise_on_exc = RuntimeError("boom")
    artist = _fake_artist(albums=[_Album(id="1", title="LP")], raise_on="eps")
    s = _releases_stub(_stub(tmp_path), artist)
    rows, failures, limited = _bind(s, "_artist_recent_releases")("A")
    assert [r["id"] for r in rows] == ["1"] and failures == ["get_ep_singles"] and limited is False


def test_recent_releases_tells_a_rate_limit_apart(tmp_path):
    global raise_on_exc
    raise_on_exc = type("TooManyRequests", (Exception,), {})()
    artist = _fake_artist(albums=[_Album(id="1", title="LP")], raise_on="eps")
    s = _releases_stub(_stub(tmp_path), artist)
    _rows, failures, limited = _bind(s, "_artist_recent_releases")("A")
    assert limited is True and "get_ep_singles" in failures
    raise_on_exc = RuntimeError("boom")


def test_recent_releases_asks_by_id_without_fetching_the_artist(tmp_path, monkeypatch):
    s = _stub(tmp_path)
    asked = []

    class _Handle:
        def __init__(self, session, artist_id):
            assert artist_id is None  # an id-only handle sends nothing
            self.id = None

        def get_albums(self, limit=None):
            asked.append(("albums", self.id))
            return [_Album(id="1", title="LP")]

        def get_ep_singles(self, limit=None):
            asked.append(("eps", self.id))
            return []

    monkeypatch.setattr(rel, "Artist", _Handle)
    s.tidal = SimpleNamespace(session=SimpleNamespace(artist=lambda i: pytest.fail("fetched the artist")))
    s._remember = lambda kind, key, obj: pytest.fail("cached a bare handle")
    _releases_stub(s, None)
    s._objs["artist"].clear()
    rows, failures, limited = _bind(s, "_artist_recent_releases")("7")
    assert [r["id"] for r in rows] == ["1"] and failures == [] and limited is False
    assert asked == [("albums", 7), ("eps", 7)]
    assert "7" not in s._objs["artist"]


def test_recent_releases_bad_id_is_a_failure(tmp_path):
    s = _stub(tmp_path)
    s.tidal = SimpleNamespace(session=SimpleNamespace(request=None))
    assert _bind(s, "_artist_recent_releases")("not-a-number") == ([], ["artist"], False)


# --- the pacer -------------------------------------------------------------


def test_tick_picks_the_stalest_follow_and_records_it(tmp_path):
    s = _releases_stub(_stub(tmp_path), _fake_artist(albums=[_Album(id="1", title="LP")]))
    s._releases.follow("A", "Artist", "")
    s._releases.follow("B", "Other", "")
    s._releases.record_check("B", [{"id": "x", "date": "2026-01-01"}])
    _bind(s, "_releases_tick")()
    # The step ran inline on the pool and posted its outcome.
    assert len(s._relStepDone.emits) == 1
    out = s._relStepDone.emits[0]
    assert out["artist_id"] == "A" and out["fresh"] == [] and not out["failed"]
    assert s._releases.follow_info("A")["seeded"]
    assert [r["id"] for r in s._releases.feed()] == ["1", "x"]


def test_tick_is_a_noop_while_gated_or_running(tmp_path):
    s = _releases_stub(_stub(tmp_path), _fake_artist())
    s._releases.follow("A", "Artist", "")
    s._logged_in = False
    _bind(s, "_releases_tick")()
    s._logged_in = True
    s._rel_revealed = False
    _bind(s, "_releases_tick")()
    s._rel_revealed = True
    s._rel_step_running = True
    _bind(s, "_releases_tick")()
    s._rel_step_running = False
    s._rel_import_running = True
    _bind(s, "_releases_tick")()
    assert s._relStepDone.emits == [] and s._rel_timer.starts == []


def test_tick_waits_out_a_backoff(tmp_path, monkeypatch):
    s = _releases_stub(_stub(tmp_path), _fake_artist())
    s._releases.follow("A", "Artist", "")
    monkeypatch.setattr(rel.time, "monotonic", lambda: 100.0)
    s._rel_backoff_until = 160.0
    _bind(s, "_releases_tick")()
    assert s._relStepDone.emits == []
    assert s._rel_timer.starts and 59_000 <= s._rel_timer.starts[-1] <= 60_000


def test_tick_idles_when_nothing_is_due_and_closes_the_round(tmp_path):
    s = _releases_stub(_stub(tmp_path), _fake_artist())
    s._releases.follow("A", "Artist", "")
    s._releases.record_check("A", [{"id": "x", "date": "2026-01-01"}])
    s._rel_round_done = 3
    _bind(s, "_releases_tick")()
    assert s._relStepDone.emits == []
    assert s._rel_timer.starts == [rel._REL_MAX_STEP_MS]
    assert s._rel_round_done == 0 and s._rel_mirror_due is True
    assert s._releases.meta_get("last_round_end") != ""


def test_step_ms_burst_steady_and_busy(tmp_path):
    s = _stub(tmp_path)
    for i in range(3):
        s._releases.follow(str(i), "A", "")
    assert _bind(s, "_releases_step_ms")() == rel._REL_BURST_MS
    s._job_aborts = {1: object()}
    assert _bind(s, "_releases_step_ms")() == rel._REL_BURST_BUSY_MS
    s._job_aborts = {}
    for i in range(3):
        s._releases.record_check(str(i), [{"id": f"r{i}", "date": "2026-01-01"}])
    # Three follows over a round is clamped to the one-minute ceiling.
    assert _bind(s, "_releases_step_ms")() == rel._REL_MAX_STEP_MS
    s2 = _stub(tmp_path / "many")
    s2._releases.follow_many([{"id": str(i), "name": "x"} for i in range(1000)])
    for i in range(1000):
        s2._releases.record_check(str(i), [{"id": f"r{i}", "date": "2026-01-01"}])
    assert _bind(s2, "_releases_step_ms")() == rel._REL_ROUND_S * 1000 // 1000


def test_step_done_counts_announces_and_rearms(tmp_path):
    s = _stub(tmp_path)
    s._releases.follow("A", "Artist", "")
    _bind(s, "_on_rel_step_done")(
        {"artist_id": "A", "name": "Artist", "fresh": [{"id": "n", "date": "2026-10-01", "title": "T", "rtype": "ep"}]}
    )
    assert s._rel_step_running is False
    assert s._rel_notify_items == [{"artist": "Artist", "title": "T", "rtype": "ep"}]
    assert s._rel_round_done == 1 and s.releasesProgress.emits == [(1, 1)]
    assert s._rel_notify_pending == 1 and s._rel_notify_timer.starts == [None]
    assert s.releasesChanged.emits == [()]
    assert s._rel_timer.starts == [rel._REL_BURST_MS]
    # A second fresh step while the banner timer runs does not restart it.
    _bind(s, "_on_rel_step_done")({"artist_id": "A", "name": "Artist", "fresh": [{"id": "m", "date": "2026-10-01"}]})
    assert s._rel_notify_pending == 2 and len(s._rel_notify_timer.starts) == 1


def test_step_done_backs_off_on_a_rate_limit(tmp_path, monkeypatch):
    s = _stub(tmp_path)
    s._releases.follow("A", "Artist", "")
    monkeypatch.setattr(rel.time, "monotonic", lambda: 1000.0)
    _bind(s, "_on_rel_step_done")({"artist_id": "A", "rate_limited": True})
    assert s._rel_backoff_until == 1000.0 + rel._REL_BACKOFF_S[0]
    assert s._rel_timer.starts == [rel._REL_BACKOFF_S[0] * 1000]
    assert s.releasesChanged.emits == [] and s._rel_round_done == 0
    _bind(s, "_on_rel_step_done")({"artist_id": "A", "rate_limited": True})
    assert s._rel_backoff_until == 1000.0 + rel._REL_BACKOFF_S[1]
    # A clean step resets the ladder.
    _bind(s, "_on_rel_step_done")({"artist_id": "A", "fresh": []})
    assert s._rel_backoff_level == 0


def test_notify_fires_once_per_minute_and_honors_the_pref(tmp_path):
    s = _stub(tmp_path)
    s._rel_notify_pending = 3
    s._rel_notify_items = [{"artist": "A", "title": "x"}, {"artist": "B", "title": "y"}, {"artist": "A", "title": "z"}]
    _bind(s, "_on_rel_notify")()
    assert s._notifier.sent == [("3 new releases", "From A and B")]
    assert s._rel_notify_pending == 0 and s._rel_notify_items == []
    s._rel_notify_pending = 2
    s._rel_notify_items = [{"artist": "A", "title": "x"}]
    s._waves_prefs["releases_notify"] = False
    _bind(s, "_on_rel_notify")()
    assert len(s._notifier.sent) == 1
    # Switched off, the batch is dropped, not carried into the next banner.
    assert s._rel_notify_pending == 0 and s._rel_notify_items == []


def test_banner_wording():
    b = rel._releases_banner
    assert b(1, [{"artist": "Lorde", "title": "Virgin", "rtype": "album"}]) == ("New album from Lorde", "Virgin")
    assert b(1, [{"artist": "Lorde", "title": "Hammer", "rtype": "SINGLE"}]) == ("New single from Lorde", "Hammer")
    assert b(1, [{"artist": "Lorde", "title": "", "rtype": "ep"}]) == ("New EP from Lorde", "From an artist you follow")
    assert b(1, [{"artist": "", "title": "X", "rtype": ""}]) == ("New release", "X")
    assert b(1, []) == ("1 new release", "From artists you follow")
    assert b(2, [{"artist": "Lorde"}, {"artist": "Lorde"}]) == ("New from Lorde", "2 new releases")
    many = [{"artist": n} for n in ("A", "B", "C", "D", "B")]
    assert b(5, many) == ("5 new releases", "From A, B and 2 more")
    assert b(9, []) == ("9 new releases", "From artists you follow")


def test_notify_sends_on_the_shared_pool_and_logs_only_a_handover(tmp_path, caplog):
    s = _stub(tmp_path)
    # On the logger itself: the diagnostics tests may stop "waves" propagating.
    log = logging.getLogger("waves.notify")
    log.addHandler(caplog.handler)
    try:
        with caplog.at_level("INFO", logger="waves.notify"):
            _bind(s, "_releases_notify")("New album from Lorde", "Virgin")
        assert s._notifier.sent == [("New album from Lorde", "Virgin")]
        assert "notification handed to the OS" in caplog.text
        # No title or artist reaches the log, only that a banner went out.
        assert "Lorde" not in caplog.text and "Virgin" not in caplog.text
        caplog.clear()
        s._notifier = _FakeNotifier(ok=False)
        with caplog.at_level("INFO", logger="waves.notify"):
            _bind(s, "_releases_notify")("t", "m")
        assert "notification handed to the OS" not in caplog.text
    finally:
        log.removeHandler(caplog.handler)


def test_stop_drops_a_pending_banner(tmp_path):
    s = _stub(tmp_path)
    s._rel_notify_pending = 2
    s._rel_notify_items = [{"artist": "A"}]
    _bind(s, "_releases_stop")()
    assert s._rel_notify_pending == 0 and s._rel_notify_items == []


# --- gating ------------------------------------------------------------------


def test_maybe_start_needs_both_gates(tmp_path):
    s = _stub(tmp_path)
    s._logged_in = False
    _bind(s, "_releases_maybe_start")()
    assert s._rel_timer.starts == []
    s._logged_in = True
    s._rel_revealed = False
    _bind(s, "_releases_maybe_start")()
    assert s._rel_timer.starts == []
    s._rel_revealed = True
    _bind(s, "_releases_maybe_start")()
    assert s._rel_timer.starts == [0] and s._rel_mirror_due is True


def test_stop_bumps_the_generation_and_drops_a_late_step(tmp_path):
    s = _releases_stub(_stub(tmp_path), _fake_artist(albums=[_Album(id="1", title="LP")]))
    s._releases.follow("A", "Artist", "")
    _bind(s, "_releases_stop")()
    assert s._rel_gen == 1 and s._rel_timer.stops == 1 and s._rel_notify_timer.stops == 1
    # A step dispatched under the old generation posts nothing.
    _bind(s, "_releases_check_one")({"artist_id": "A", "name": "Artist"}, 0)
    assert s._relStepDone.emits == []


def test_bridge_hooks_call_the_family(tmp_path):
    """_set_logged_in and bootRevealed reach the family through getattr, so a
    stub without it survives and one with it starts or stops the sweep."""
    s = _stub(tmp_path)
    started, stopped = [], []
    s._releases_maybe_start = lambda: started.append(1)
    s._releases_stop = lambda: stopped.append(1)
    s._register_session_secrets = lambda: None
    s._releases_signed_out = lambda: stopped.append("out")
    s.loggedInChanged = _Signal()
    s._logged_in = False
    _bind(s, "_set_logged_in")(True)
    _bind(s, "_set_logged_in")(False)
    assert started == [1] and stopped == [1, "out"]


# --- the feed, follows -------------------------------------------------------


def test_load_releases_dresses_rows_and_drops_a_stale_build(tmp_path):
    s = _stub(tmp_path)
    s._releases.follow("A", "Artist", "")
    s._releases.record_check("A", [{"id": "x", "date": "2026-01-01", "title": "T"}])
    s._dress_card = lambda c: {**c, "lib": {"present": True}}
    _bind(s, "loadReleases")()
    assert len(s.releasesLoaded.emits) == 1
    rows, more, months = s.releasesLoaded.emits[0]
    assert (more, months) == (False, 0)
    assert rows[0]["id"] == "x" and rows[0]["kind"] == "album" and rows[0]["lib"] == {"present": True}
    s._rel_gen = 5  # a sign-out mid-build
    s._dress_card = lambda c: setattr(s, "_rel_gen", 6) or c
    _bind(s, "loadReleases")()
    assert len(s.releasesLoaded.emits) == 1


def test_search_releases_reaches_past_the_window_dressed_and_drops_a_stale_answer(tmp_path, monkeypatch):
    s = _stub(tmp_path)
    s._releases.follow("A", "Artist", "")
    s._releases.record_check(
        "A",
        [
            {"id": "new", "date": "2026-01-01", "title": "Debut", "artist": "Björk"},
            {"id": "old", "date": "1993-07-05", "title": "Debut Live", "artist": "Björk"},
            {"id": "x", "date": "2026-02-01", "title": "Other", "artist": "Someone"},
        ],
    )
    s._dress_card = lambda c: {**c, "lib": {"present": True}}
    logged: list[str] = []
    monkeypatch.setattr(rel.logger, "info", lambda msg, *a: logged.append(msg % a))
    _bind(s, "searchReleases")("  bjork   debut ")
    needle, rows, more = s.releasesSearched.emits[0]
    assert needle == "  bjork   debut " and more is False
    assert [r["id"] for r in rows] == ["new", "old"] and rows[1]["lib"] == {"present": True}
    # The words reach the log only as marked user content.
    assert logged == [f"release search: 2 hit(s) for {diagnostics.content('bjork debut')}"]
    # An empty box answers at once with nothing; a newer search drops an older answer.
    _bind(s, "searchReleases")("")
    assert s.releasesSearched.emits[-1] == ("", [], False)
    s._dress_card = lambda c: setattr(s, "_rel_find_gen", 99) or c
    _bind(s, "searchReleases")("debut")
    assert len(s.releasesSearched.emits) == 2
    # The feed's filter rides along, and the limit counts under it.
    s._dress_card = lambda c: c
    _bind(s, "searchReleases")("debut", "single")
    assert s.releasesSearched.emits[-1] == ("debut", [], False)


def test_follow_and_unfollow_emit_and_rearm(tmp_path):
    s = _stub(tmp_path)
    _bind(s, "followArtist")("A", "Artist", "img")
    assert s._releases.follow_info("A")["origin"] == "manual"
    assert s.followStateChanged.emits == [("A", True)] and s.releasesChanged.emits == [()]
    assert s._rel_timer.starts == [0]
    assert _bind(s, "isFollowed")("A") is True and _bind(s, "followedCount")() == 1
    assert _bind(s, "followInfo")("A")["name"] == "Artist" and _bind(s, "followInfo")("zz") == {}
    _bind(s, "unfollowArtist")("A")
    assert s.followStateChanged.emits[-1] == ("A", False)
    assert _bind(s, "isFollowed")("A") is False
    _bind(s, "followArtist")("", "x", "")
    assert len(s.followStateChanged.emits) == 2


def test_unfollowing_a_mirrored_follow_is_remembered(tmp_path):
    s = _stub(tmp_path)
    s._releases.sync_tidal_favorites([{"id": "t", "name": "T"}])
    s.tidal = SimpleNamespace(session=None)
    _bind(s, "unfollowArtist")("t")
    assert s._releases.sync_tidal_favorites([{"id": "t", "name": "T"}]) == (0, 0)


def test_acknowledge_all_returns_ids_and_announces(tmp_path):
    s = _stub(tmp_path)
    s._releases.follow("A", "Artist", "")
    s._releases.record_check("A", [{"id": "x", "date": "2026-01-01"}])
    s._releases.record_check("A", [{"id": "x", "date": "2026-01-01"}, {"id": "y", "date": "2026-02-01"}])
    assert _bind(s, "releasesUnseenCount")() == 1
    assert _bind(s, "acknowledgeAllReleases")() == ["y"]
    assert s.releasesChanged.emits == [()]
    assert _bind(s, "acknowledgeAllReleases")() == [] and len(s.releasesChanged.emits) == 1


def test_load_followed_dresses_with_library_presence(tmp_path):
    s = _stub(tmp_path)
    s._releases.follow("A", "Artist", "")
    s._dress_library_row = lambda cat, r: {**r, "lib": {"present": cat == "artists"}}
    _bind(s, "loadFollowed")()
    assert s.followedLoaded.emits[0][0]["lib"] == {"present": True}


def test_load_followed_carries_the_careers_only_for_the_ledger(tmp_path):
    s = _stub(tmp_path, prefs={"releases_follow_view": "roster"})
    s._releases.follow("A", "Artist", "")
    s._releases.record_check("A", [{"id": "x", "date": "2026-01-01", "title": "X"}])
    _bind(s, "loadFollowed")()
    (row,) = s.followedLoaded.emits[-1]
    assert row["career"] == [] and row["total"] == 1
    s._waves_prefs["releases_follow_view"] = "ledger"
    _bind(s, "loadFollowed")()
    (row,) = s.followedLoaded.emits[-1]
    assert [e["id"] for e in row["career"]] == ["x"]


def test_load_followed_carries_every_release_only_for_the_shelves(tmp_path):
    s = _stub(tmp_path, prefs={"releases_follow_view": "roster"})
    s._releases.follow("A", "Artist", "")
    s._releases.record_check("A", [{"id": f"r{m}", "date": f"2026-{m:02d}-01", "title": f"R{m}"} for m in range(1, 10)])
    for view in ("roster", "ledger"):
        s._waves_prefs["releases_follow_view"] = view
        _bind(s, "loadFollowed")()
        (row,) = s.followedLoaded.emits[-1]
        assert [e["id"] for e in row["releases"]] == ["r9"] and row["total"] == 9, view
    s._waves_prefs["releases_follow_view"] = "shelves"
    _bind(s, "loadFollowed")()
    (row,) = s.followedLoaded.emits[-1]
    assert [e["id"] for e in row["releases"]] == [f"r{m}" for m in range(9, 0, -1)] and row["career"] == []


def test_an_unchanged_followed_list_is_not_handed_to_the_page_again(tmp_path):
    # Every sweep step reloads the list; the page spends tens of ms taking
    # 900 rows in, so the same rows are dropped before they reach it.
    s = _stub(tmp_path, prefs={"releases_follow_view": "roster"})
    s._releases.follow("A", "Artist", "")
    s._releases.record_check("A", [{"id": "x", "date": "2026-01-01", "title": "X"}])
    load = _bind(s, "loadFollowed")
    load()
    load()
    assert len(s.followedLoaded.emits) == 1
    # A sweep step stamps when each follow was checked, which the page never
    # shows: that alone is not a change worth handing over.
    s._releases.record_check("A", [{"id": "x", "date": "2026-01-01", "title": "X"}])
    with s._releases._lock:
        s._releases._conn.execute("UPDATE followed SET last_checked = last_checked + 600, mb_checked = 7")
        s._releases._conn.commit()
    load()
    assert len(s.followedLoaded.emits) == 1
    s._releases.record_check(
        "A", [{"id": "y", "date": "2026-02-01", "title": "Y"}, {"id": "x", "date": "2026-01-01", "title": "X"}]
    )
    load()
    assert len(s.followedLoaded.emits) == 2
    # A new session hands the list over again, whatever it holds.
    s._rel_gen += 1
    load()
    assert len(s.followedLoaded.emits) == 3
    # An unfollow drops the row on the page by itself: the next list goes
    # over even when it is the same (the artist followed again meanwhile).
    _bind(s, "unfollowArtist")("A")
    _bind(s, "followArtist")("A", "Artist", "")
    load()
    assert len(s.followedLoaded.emits) == 4


def test_a_followed_list_for_a_shape_no_longer_shown_is_dropped(tmp_path):
    s = _stub(tmp_path, prefs={"releases_follow_view": "ledger"})
    s._releases.follow("A", "Artist", "")
    s._releases.record_check("A", [{"id": "x", "date": "2026-01-01", "title": "X"}])
    held = []
    s.threadpool = type("_Held", (), {"start": lambda self, w: held.append(w)})()
    _bind(s, "loadFollowed")()  # the Ledger's load, still out...
    s._waves_prefs["releases_follow_view"] = "shelves"
    _bind(s, "loadFollowed")()  # ...when the Shelves are picked
    for w in reversed(held):  # the Shelves' load lands first
        w.fn()
    (row,) = s.followedLoaded.emits[-1]
    assert len(s.followedLoaded.emits) == 1 and row["career"] == []


def test_musicbrainz_marks_are_only_worked_out_when_drawn(tmp_path):
    s = _stub(tmp_path)
    s._releases.follow("A", "Artist", "")
    s._releases.record_check("A", [{"id": "x", "date": "2020-01-01", "title": "First"}])
    s._releases.record_mb("A", "mb-a", [{"rgid": "g", "title": "Lost", "date": "2021-03-03", "rtype": "ep"}])
    assert [m["rgid"] for m in s._releases.followed_artists()[0]["missing"]] == ["g"]
    assert s._releases.followed_artists(marks=False)[0]["missing"] == []


# --- follow by search ---------------------------------------------------------


def _search_stub(tmp_path, artists, *, fail=False):
    s = _stub(tmp_path)
    calls: list = []

    def search(text, models=None, limit=None):
        calls.append((text, [m.__name__ for m in models or []], limit))
        if fail:
            raise RuntimeError("offline")
        return {"artists": artists, "top_hit": None}

    s.tidal = SimpleNamespace(session=SimpleNamespace(search=search))
    s._fav_artist_dict = lambda a: {"id": str(a.id), "name": a.name, "art": "", "roles": [], "popularity": -1}
    return s, calls


def test_search_artists_asks_for_artists_only_and_marks_follows(tmp_path):
    s, calls = _search_stub(tmp_path, [SimpleNamespace(id=1, name="One"), SimpleNamespace(id=2, name="Two")])
    s._releases.follow("2", "Two", "")
    _bind(s, "searchArtists")("  big \n thief ")
    assert calls == [("big thief", ["Artist"], rel._REL_SEARCH_LIMIT)]
    needle, rows, ok = s.artistSearchResults.emits[0]
    assert needle == "  big \n thief " and ok is True
    assert [(r["id"], r["followed"]) for r in rows] == [("1", False), ("2", True)]


def test_search_artists_blank_signed_out_and_failed(tmp_path):
    s, calls = _search_stub(tmp_path, [], fail=True)
    _bind(s, "searchArtists")("   ")
    assert s.artistSearchResults.emits == [("   ", [], True)] and calls == []
    _bind(s, "searchArtists")("x")
    assert s.artistSearchResults.emits[-1] == ("x", [], False) and len(calls) == 1
    s._logged_in = False
    _bind(s, "searchArtists")("y")
    assert s.artistSearchResults.emits[-1] == ("y", [], False) and len(calls) == 1


def test_search_artists_drops_a_superseded_answer(tmp_path):
    s, _calls = _search_stub(tmp_path, [SimpleNamespace(id=1, name="One")])
    s._fav_artist_dict = lambda a: setattr(s, "_rel_search_gen", 99) or {"id": "1", "name": "One"}
    _bind(s, "searchArtists")("one")
    assert s.artistSearchResults.emits == []


# --- the window and auto-download --------------------------------------------


def test_keep_from_is_the_first_of_the_month_n_months_back(tmp_path, monkeypatch):
    s = _stub(tmp_path, prefs={"releases_window_months": "12"})
    real_date = rel._dt.date

    class _Today:
        @staticmethod
        def today():
            return real_date(2026, 10, 14)

    monkeypatch.setattr(rel._dt, "date", _Today)
    assert _bind(s, "_releases_keep_from")() == "2025-10-01"
    s._waves_prefs["releases_window_months"] = "6"
    assert _bind(s, "_releases_keep_from")() == "2026-04-01"
    s._waves_prefs["releases_window_months"] = "junk"
    assert _bind(s, "_releases_keep_from")() == "2025-10-01"


def test_the_window_setting_applies_at_once_both_ways(tmp_path, monkeypatch):
    s = _stub(tmp_path, prefs={"releases_window_months": "6"})
    s._releases.follow("A", "Artist", "")
    s._releases.record_check(
        "A",
        [{"id": "old", "date": "2020-01-01"}, {"id": "new", "date": "2099-01-01"}],
        keep_from=_bind(s, "_releases_keep_from")(),
        prune_from=_bind(s, "_releases_keep_from")(rel._REL_WINDOW_MAX_MONTHS),
    )
    _bind(s, "loadReleases")()
    assert [r["id"] for r in s.releasesLoaded.emits[-1][0]] == ["new"]
    # Widened: the older release is back at once, no re-check needed.
    s._waves_prefs["releases_window_months"] = "120"
    _bind(s, "_releases_window_changed")()
    assert s.releasesChanged.emits == [()]
    _bind(s, "loadReleases")()
    assert [r["id"] for r in s.releasesLoaded.emits[-1][0]] == ["new", "old"]


def test_the_feed_loads_a_few_months_and_says_when_older_ones_wait(tmp_path, monkeypatch):
    class _Day(rel._dt.date):
        @classmethod
        def today(cls):
            return cls(2026, 10, 14)

    monkeypatch.setattr(rel._dt, "date", _Day)
    s = _stub(tmp_path, prefs={"releases_window_months": "12"})
    s._releases.follow("A", "Artist", "")
    s._releases.record_check(
        "A",
        [
            {"id": "now", "date": "2026-10-02"},
            {"id": "july", "date": "2026-07-01"},
            {"id": "june", "date": "2026-06-30"},
            {"id": "spring", "date": "2026-03-01"},
            {"id": "gone", "date": "2025-09-30"},
        ],
    )

    def load(months):
        _bind(s, "loadReleases")(months)
        rows, more, asked = s.releasesLoaded.emits[-1]
        return [r["id"] for r in rows], more, asked

    # Three months back from October: July 1 on, older ones waiting.
    assert load(3) == (["now", "july"], True, 3)
    assert load(6) == (["now", "july", "june"], True, 6)
    # The last page inside the window: nothing older waits (the window drops "gone").
    assert load(9) == (["now", "july", "june", "spring"], False, 9)
    # Asking past the window never reads past it.
    assert load(60) == (["now", "july", "june", "spring"], False, 60)
    assert load(0) == (["now", "july", "june", "spring"], False, 0)


def test_auto_download_is_off_by_default_and_waits_for_the_day(tmp_path):
    s = _stub(tmp_path)
    s._releases.follow("A", "Artist", "")
    s._releases.record_check("A", [{"id": "a", "date": "2026-01-01"}])
    fresh = s._releases.record_check(
        "A",
        [{"id": "a", "date": "2026-01-01"}, {"id": "now", "date": "2026-01-02"}, {"id": "soon", "date": "2099-01-01"}],
    )
    _bind(s, "_releases_auto_download")(fresh)
    assert s.queued == []
    s._waves_prefs["releases_auto_download"] = True
    _bind(s, "_releases_auto_download")(fresh)
    assert s.queued == ["now"]
    rows = {r["id"]: r for r in s._releases.feed()}
    assert rows["now"]["dl_state"] == DL_QUEUED and rows["soon"]["dl_state"] == DL_WAITING
    # The day comes: a later step drains it.
    s._releases_today = lambda: "2099-01-01"
    _bind(s, "_releases_drain_auto_downloads")()
    assert s.queued == ["now", "soon"]


def _atmos_pair(day):
    stereo = {"id": "s", "date": day, "title": "LP", "artist_name": "Artist", "tracks": 10, "quality": "HI-RES"}
    twin = {**stereo, "id": "t", "title": "LP (Dolby Atmos)", "quality": "ATMOS"}
    return collapse_atmos_twins([stereo, twin])


def test_auto_download_takes_the_atmos_twin_too_when_the_setting_wants_atmos(tmp_path):
    s = _stub(tmp_path, prefs={"releases_auto_download": True})
    s.settings = SimpleNamespace(data=SimpleNamespace(download_dolby_atmos=False))
    s._releases.follow("A", "Artist", "")
    s._releases.record_check("A", [{"id": "a", "date": "2026-01-01"}])
    fresh = s._releases.record_check("A", [{"id": "a", "date": "2026-01-01"}, *_atmos_pair("2026-01-02")])
    assert [r["id"] for r in fresh] == ["s"] and fresh[0]["atmos_ids"] == ["t"]
    _bind(s, "_releases_auto_download")(fresh)
    assert s.queued == ["s"]
    # Download Dolby Atmos on: both editions, as the artist page's bulk does.
    s.settings.data.download_dolby_atmos = True
    _bind(s, "_releases_auto_download")(fresh)
    assert s.queued == ["s", "s", "t"]
    # A twin whose fetch failed waits in the store's twin list, then retries.
    on_state = _bind(s, "_releases_on_download_state")
    on_state("t", "failed")
    _bind(s, "_releases_drain_auto_downloads")()
    assert s.queued[-1] == "t"


def test_an_atmos_twin_dated_ahead_waits_for_its_day_with_its_stereo_row(tmp_path):
    s = _stub(tmp_path, prefs={"releases_auto_download": True})
    s.settings = SimpleNamespace(data=SimpleNamespace(download_dolby_atmos=True))
    s._releases.follow("A", "Artist", "")
    s._releases.record_check("A", [{"id": "a", "date": "2026-01-01"}])
    fresh = s._releases.record_check("A", [{"id": "a", "date": "2026-01-01"}, *_atmos_pair("2099-01-01")])
    _bind(s, "_releases_auto_download")(fresh)
    assert s.queued == []
    s._releases_today = lambda: "2099-01-01"
    _bind(s, "_releases_drain_auto_downloads")()
    assert s.queued == ["s", "t"]
    _bind(s, "_releases_drain_auto_downloads")()
    assert s.queued == ["s", "t"]


def test_a_waiting_featured_release_stays_back_once_featured_are_hidden(tmp_path):
    # Dated ahead while featured releases were shown, so it waits with its
    # Atmos twin; hidden before its day, neither edition goes, as the feed
    # no longer shows it. The follow's own release still goes.
    s = _stub(tmp_path, prefs={"releases_auto_download": True})
    s.settings = SimpleNamespace(data=SimpleNamespace(download_dolby_atmos=True))
    s._releases.follow("A", "Artist", "")
    s._releases.record_check("A", [{"id": "a", "date": "2026-01-01"}])
    pair = [{**r, "artist_id": "Z"} for r in _atmos_pair("2099-01-01")]
    own = {"id": "own", "date": "2099-01-01", "title": "Own", "artist_id": "A"}
    fresh = s._releases.record_check("A", [{"id": "a", "date": "2026-01-01"}, *pair, own])
    _bind(s, "_releases_auto_download")(fresh)
    assert s.queued == []
    s._waves_prefs["releases_featured"] = "hide"
    s._releases_today = lambda: "2099-01-01"
    _bind(s, "_releases_drain_auto_downloads")()
    assert s.queued == ["own"]
    assert {r["id"]: r["dl_state"] for r in s._releases.feed()}["s"] != DL_WAITING


def test_a_waiting_atmos_twin_stays_back_when_its_kind_is_unticked(tmp_path):
    s = _stub(tmp_path, prefs={"releases_auto_download": True})
    s.settings = SimpleNamespace(data=SimpleNamespace(download_dolby_atmos=True))
    s._releases.follow("A", "Artist", "")
    s._releases.record_check("A", [{"id": "a", "date": "2026-01-01"}])
    fresh = s._releases.record_check("A", [{"id": "a", "date": "2026-01-01"}, *_atmos_pair("2099-01-01")])
    _bind(s, "_releases_auto_download")(fresh)
    # Albums unticked while the pair waits: neither edition goes.
    s._waves_prefs["releases_auto_kinds"] = "ep,single"
    s._releases_today = lambda: "2099-01-01"
    _bind(s, "_releases_drain_auto_downloads")()
    assert s.queued == []
    # A twin kept before the kind was stored (a bare day) still goes.
    s._waves_prefs["releases_auto_kinds"] = "album"
    s._releases.meta_set("atmos_waiting", '{"old": "2026-01-01"}')
    _bind(s, "_releases_drain_auto_downloads")()
    assert s.queued == ["old"]


def _editions(day, *, twin_too=True, stereo_too=True, clean=False):
    """A step's rows for one album, folded the way the sweep folds them
    (``clean``: each mix also has a clean edition the dedup folded in)."""
    stereo = {"id": "s", "date": day, "title": "LP", "artist_name": "Artist", "tracks": 10, "quality": "HI-RES"}
    twin = {**stereo, "id": "t", "title": "LP (Dolby Atmos)", "quality": "ATMOS"}
    if clean:
        stereo["sibling_ids"], twin["sibling_ids"] = ["s_c"], ["t_c"]
    rows = [{"id": "a", "date": "2026-01-01"}]
    rows += [stereo] if stereo_too else []
    rows += [twin] if twin_too else []
    return collapse_atmos_twins(rows)


def _sweep_step(s, rows, aid="A"):
    s._artist_recent_releases = lambda _aid: (rows, [], False)
    _bind(s, "_releases_check_one")({"artist_id": aid, "name": "Artist"}, s._rel_gen)
    _bind(s, "_on_rel_step_done")(s._relStepDone.emits[-1])


def _atmos_stub(tmp_path, *, auto=True, atmos=True):
    s = _releases_stub(_stub(tmp_path, prefs={"releases_auto_download": auto}), _fake_artist())
    s.settings = SimpleNamespace(data=SimpleNamespace(download_dolby_atmos=atmos))
    s._releases.follow("A", "Artist", "")
    _sweep_step(s, [{"id": "a", "date": "2026-01-01"}])
    return s


def test_an_atmos_twin_listed_days_after_its_release_follows_it(tmp_path):
    s = _atmos_stub(tmp_path)
    _sweep_step(s, _editions("2026-01-02", twin_too=False))
    assert s.queued == ["s"]
    # TIDAL adds the Dolby Atmos edition a few days later: it goes too.
    _sweep_step(s, _editions("2026-01-02"))
    assert s.queued == ["s", "t"]
    _sweep_step(s, _editions("2026-01-02"))
    assert s.queued == ["s", "t"]


def test_a_late_atmos_twin_stays_back_when_its_release_did_not_download(tmp_path):
    s = _atmos_stub(tmp_path, auto=False)
    _sweep_step(s, _editions("2026-01-02", twin_too=False))
    s._waves_prefs["releases_auto_download"] = True
    _sweep_step(s, _editions("2026-01-02"))
    assert s.queued == []
    # Download Dolby Atmos off: the stereo edition only, early or late.
    s = _atmos_stub(tmp_path / "off", atmos=False)
    _sweep_step(s, _editions("2026-01-02", twin_too=False))
    _sweep_step(s, _editions("2026-01-02"))
    assert s.queued == ["s"]


def test_a_late_atmos_twin_waits_for_the_day_its_release_waits_for(tmp_path):
    s = _atmos_stub(tmp_path)
    _sweep_step(s, _editions("2099-01-01", twin_too=False))
    _sweep_step(s, _editions("2099-01-01"))
    assert s.queued == []
    s._releases_today = lambda: "2099-01-01"
    _bind(s, "_releases_drain_auto_downloads")()
    assert sorted(s.queued) == ["s", "t"]


def test_a_stereo_edition_listed_after_its_atmos_twin_downloads_too(tmp_path):
    # Download Dolby Atmos off: the Atmos edition alone was new, so it went;
    # the stereo edition TIDAL lists later goes as well.
    s = _atmos_stub(tmp_path, atmos=False)
    _sweep_step(s, _editions("2026-01-02", stereo_too=False))
    assert s.queued == ["t"]
    _sweep_step(s, _editions("2026-01-02"))
    assert s.queued == ["t", "s"]
    rows = {r["id"]: r for r in s._releases.feed()}
    assert rows["s"]["dl_state"] == DL_QUEUED


def test_a_stereo_edition_after_its_atmos_twin_is_known_when_each_has_a_clean_one(tmp_path):
    s = _atmos_stub(tmp_path, atmos=False)
    _sweep_step(s, _editions("2026-01-02", stereo_too=False, clean=True))
    assert s.queued == ["t"]
    _sweep_step(s, _editions("2026-01-02", clean=True))
    assert s.queued == ["t", "s"]


def test_a_late_edition_of_a_collaboration_is_queued_once(tmp_path):
    # Both followed artists list the album: the twin TIDAL adds later goes
    # once, not once per artist.
    s = _atmos_stub(tmp_path)
    s._releases.follow("B", "Band", "")
    _sweep_step(s, [{"id": "a", "date": "2026-01-01"}], aid="B")
    _sweep_step(s, _editions("2026-01-02", twin_too=False))
    _sweep_step(s, _editions("2026-01-02", twin_too=False), aid="B")
    assert s.queued == ["s"]
    _sweep_step(s, _editions("2026-01-02"))
    _sweep_step(s, _editions("2026-01-02"), aid="B")
    assert s.queued == ["s", "t"]


def test_a_late_twin_survives_a_collaborator_followed_in_between(tmp_path):
    # The twin appears, then collaborator B is followed before A's next step.
    # B's first fetch seeds B's whole catalog, twin included, into ``seen``
    # silently: only a real report may silence a late edition, never that.
    s = _atmos_stub(tmp_path)
    _sweep_step(s, _editions("2026-01-02", twin_too=False))
    assert s.queued == ["s"]
    s._releases.follow("B", "Band", "")
    _sweep_step(s, _editions("2026-01-02"), aid="B")  # B's silent baseline
    _sweep_step(s, _editions("2026-01-02"))  # A's step: the twin is late for A
    assert s.queued == ["s", "t"]


def test_a_sweep_step_keeps_releases_listed_long_ago(tmp_path):
    # A back catalog TIDAL listed in 2014 stays on record: the timeline draws
    # every release, and the feed window applies only when it is read.
    s = _releases_stub(_stub(tmp_path), _fake_artist())
    s._releases.follow("A", "Artist", "")
    old = {"id": "old", "date": "2004-05-01", "listed": "2014-11-01", "title": "Old", "rtype": "album"}
    s._artist_recent_releases = lambda aid: ([old], [], False)
    _bind(s, "_releases_check_one")({"artist_id": "A", "name": "Artist"}, s._rel_gen)
    (row,) = s._releases.followed_artists("2026-10-14")
    assert [e["id"] for e in row["career"]] == ["old"] and row["since"] == "2004"


def test_auto_download_switched_off_forgets_the_waiting(tmp_path):
    s = _stub(tmp_path, prefs={"releases_auto_download": True})
    s._releases.follow("A", "Artist", "")
    s._releases.record_check("A", [{"id": "a", "date": "2026-01-01"}])
    fresh = s._releases.record_check("A", [{"id": "a", "date": "2026-01-01"}, {"id": "soon", "date": "2099-01-01"}])
    _bind(s, "_releases_auto_download")(fresh)
    assert s.queued == []
    _bind(s, "_releases_forget_waiting")()
    s._releases_today = lambda: "2099-01-01"
    _bind(s, "_releases_drain_auto_downloads")()
    # Switched back on years later: no stale backlog is fetched.
    assert s.queued == []


def test_an_auto_download_whose_fetch_failed_goes_back_in_line(tmp_path):
    s = _stub(tmp_path, prefs={"releases_auto_download": True})
    s._releases.follow("A", "Artist", "")
    s._releases.record_check("A", [{"id": "a", "date": "2026-01-01"}])
    fresh = s._releases.record_check("A", [{"id": "a", "date": "2026-01-01"}, {"id": "now", "date": "2026-01-02"}])
    on_state = _bind(s, "_releases_on_download_state")
    drain = _bind(s, "_releases_drain_auto_downloads")

    def state():
        return {r["id"]: r for r in s._releases.feed()}["now"]["dl_state"]

    _bind(s, "_releases_auto_download")(fresh)
    on_state("now", "preparing")
    # The album fetch failed (a dropped connection): the next step tries again.
    on_state("now", "failed")
    assert state() == DL_WAITING
    drain()
    assert s.queued == ["now", "now"] and state() == DL_QUEUED
    # Reaching the queue settles it; nothing tries again.
    on_state("now", "queued")
    on_state("now", "failed")  # the download itself failing later is the queue's business
    assert state() == DL_QUEUED
    drain()
    assert s.queued == ["now", "now"]


# --- the deep read -----------------------------------------------------------


def _paged_artist(albums, eps=(), *, raise_at=None):
    """An artist whose album lists page by limit and offset; every call is
    kept, and the page at ``raise_at`` (section, offset) is a rate limit."""
    calls: list = []

    def pager(name, items):
        def fn(limit=None, offset=0):
            calls.append((name, limit, offset))
            if raise_at == (name, offset):
                raise type("TooManyRequests", (Exception,), {})()
            return list(items)[offset : offset + (limit or len(items))]

        return fn

    return SimpleNamespace(get_albums=pager("albums", albums), get_ep_singles=pager("eps", eps), calls=calls)


def _lps(n, prefix="a"):
    return [
        _Album(id=f"{prefix}{i}", title=f"T{i}", date=f"{2026 - i // 12:04d}-{i % 12 + 1:02d}-01") for i in range(n)
    ]


def test_a_full_head_asks_for_a_deep_read_once_a_season(tmp_path, monkeypatch):
    s = _releases_stub(_stub(tmp_path), _paged_artist(_lps(45)))
    s._releases.follow("A", "Artist", "")
    step = _bind(s, "_releases_check_one")
    step({"artist_id": "A", "name": "Artist"}, 0)
    out = s._relStepDone.emits[-1]
    assert out["deep"] == ["get_albums"] and len(s._releases.feed()) == rel._REL_FETCH_LIMIT
    # Read to its end, the catalog is not asked for again this season.
    s._releases.record_deep("A", [], complete=True)
    step({"artist_id": "A", "name": "Artist"}, 0)
    assert "deep" not in s._relStepDone.emits[-1]
    real_time = time.time
    monkeypatch.setattr(rel.time, "time", lambda: real_time() + rel._REL_DEEP_AGAIN_S + 1)
    step({"artist_id": "A", "name": "Artist"}, 0)
    assert s._relStepDone.emits[-1]["deep"] == ["get_albums"]
    # A head that holds the whole catalog asks for nothing.
    s2 = _releases_stub(_stub(tmp_path / "small"), _paged_artist(_lps(3), _lps(2, "e")))
    s2._releases.follow("A", "Artist", "")
    _bind(s2, "_releases_check_one")({"artist_id": "A", "name": "Artist"}, 0)
    assert "deep" not in s2._relStepDone.emits[-1]


def test_the_deep_read_takes_every_other_beat_paced_and_silent(tmp_path, monkeypatch):
    artist = _paged_artist(_lps(130), _lps(10, "e"))
    s = _releases_stub(_stub(tmp_path), artist)
    s._releases.follow("A", "Artist", "")
    s._releases.follow("B", "Other", "")
    s._releases.record_check("B", [{"id": "x", "date": "2026-01-01"}])
    naps: list = []
    monkeypatch.setattr(rel.time, "sleep", lambda t: naps.append(t))
    tick, done = _bind(s, "_releases_tick"), _bind(s, "_on_rel_step_done")
    tick()  # A's first check: the albums head is full
    done(s._relStepDone.emits[-1])
    assert list(s._rel_deep_queue) == [("A", "Artist", ["get_albums"])]
    assert s._rel_round_done == 1 and s._releases.unseen_count() == 0
    # A follow never checked comes first: the burst is not slowed.
    s._releases.follow("C", "New", "")
    tick()
    assert s._relStepDone.emits[-1]["artist_id"] == "C" and len(s._rel_deep_queue) == 1
    done(s._relStepDone.emits[-1])
    # Now the deep read has the beat: pages from the top, a pause before
    # each, until a short page; the older releases go on record silently.
    artist.calls.clear()
    s.releasesChanged.emits.clear()
    tick()
    page = rel._REL_DEEP_PAGE
    assert artist.calls == [("albums", page, 0), ("albums", page, 50), ("albums", page, 100)]
    assert naps == [rel._REL_DEEP_PAGE_GAP_S] * 3
    out = s._relStepDone.emits[-1]
    assert out["deep_done"] and out["added"] == 100 and not out["rate_limited"]
    assert len([r for r in s._releases.feed() if r["via"] == "A"]) == 140
    assert s._releases.unseen_count() == 0 and s._releases.deep_read_at("A") > 0
    done(out)
    assert s._rel_step_running is False and s._rel_round_done == 2
    # It fits between steps: the next beat is the step's slot, not a whole
    # interval of its own.
    assert s.releasesChanged.emits == [()] and s._rel_timer.starts[-1] < rel._REL_MAX_STEP_MS
    assert list(s._rel_deep_queue) == []
    # The sweep's own beat follows; nothing is due, so it idles.
    tick()
    assert s._relStepDone.emits[-1] is out and s._rel_timer.starts[-1] == rel._REL_MAX_STEP_MS
    # Seen once, A's next head asks for no deep read.
    _bind(s, "_releases_check_one")({"artist_id": "A", "name": "Artist"}, 0)
    assert "deep" not in s._relStepDone.emits[-1]


def test_a_rate_limited_deep_read_backs_off_and_stays_first_in_line(tmp_path, monkeypatch):
    artist = _paged_artist(_lps(130), raise_at=("albums", 50))
    s = _releases_stub(_stub(tmp_path), artist)
    s._releases.follow("A", "Artist", "")
    s._releases.record_check("A", [{"id": "x", "date": "2026-01-01"}])
    s._rel_deep_queue.extend([("A", "Artist", ["get_albums"]), ("B", "Other", ["get_albums"])])
    monkeypatch.setattr(rel.time, "sleep", lambda t: None)
    monkeypatch.setattr(rel.time, "monotonic", lambda: 1000.0)
    _bind(s, "_releases_tick")()
    out = s._relStepDone.emits[-1]
    # The first page landed before the limit: what lies past the head's 30
    # is on record already (the head's own rows are the sweep's).
    assert out["deep_done"] and out["rate_limited"] and out["added"] == 20
    assert s._releases.deep_read_at("A") == 0
    _bind(s, "_on_rel_step_done")(out)
    assert list(s._rel_deep_queue) == [("A", "Artist", ["get_albums"]), ("B", "Other", ["get_albums"])]
    assert s._rel_backoff_until == 1000.0 + rel._REL_BACKOFF_S[0]
    assert s.releasesChanged.emits == [] and s._rel_round_done == 0
    # A sign-out drops the line, and a late landing is ignored.
    _bind(s, "_releases_stop")()
    assert list(s._rel_deep_queue) == []


def test_a_release_out_since_the_check_stays_the_sweeps_news(tmp_path, monkeypatch):
    albums = _lps(45)
    artist = _paged_artist(albums)
    s = _releases_stub(_stub(tmp_path), artist)
    s._releases.follow("A", "Artist", "")
    monkeypatch.setattr(rel.time, "sleep", lambda t: None)
    step = _bind(s, "_releases_check_one")
    step({"artist_id": "A", "name": "Artist"}, 0)
    assert s._relStepDone.emits[-1]["deep"] == ["get_albums"]
    # A new album lands at the top before the deep read gets its beat.
    albums.insert(0, _Album(id="new", title="Brand New", date="2026-10-14"))
    _bind(s, "_releases_deep_read")(("A", "Artist", ["get_albums"]), 0)
    out = s._relStepDone.emits[-1]
    # 16 past the head now, one of them held since the check pushed it out.
    assert out["deep_done"] and out["added"] == 15
    assert "new" not in {r["id"] for r in s._releases.feed()}
    # The artist's next check announces it.
    step({"artist_id": "A", "name": "Artist"}, 0)
    assert [r["id"] for r in s._relStepDone.emits[-1]["fresh"]] == ["new"]
    assert s._releases.unseen_count() == 1


def test_a_deep_read_at_the_page_cap_counts_as_read_and_a_failed_page_waits_a_day(tmp_path, monkeypatch):
    monkeypatch.setattr(rel.time, "sleep", lambda t: None)
    monkeypatch.setattr(rel, "_REL_DEEP_MAX_PAGES", 2)
    s = _releases_stub(_stub(tmp_path), _paged_artist(_lps(300)))
    s._releases.follow("A", "Artist", "")
    _bind(s, "_releases_deep_read")(("A", "Artist", ["get_albums"]), 0)
    assert s._relStepDone.emits[-1]["added"] == 70 and s._releases.deep_read_at("A") > 0
    step = _bind(s, "_releases_check_one")
    step({"artist_id": "A", "name": "Artist"}, 0)
    assert "deep" not in s._relStepDone.emits[-1]

    class _Broken(Exception):
        pass

    s2 = _releases_stub(_stub(tmp_path / "b"), _paged_artist(_lps(120)))
    s2._releases.follow("A", "Artist", "")
    real = s2._objs["artist"]["A"].get_albums

    def flaky(limit=None, offset=0):
        if offset == 50:
            raise _Broken()
        return real(limit=limit, offset=offset)

    s2._objs["artist"]["A"].get_albums = flaky
    now = time.time()
    _bind(s2, "_releases_deep_read")(("A", "Artist", ["get_albums"]), 0)
    stamp = s2._releases.deep_read_at("A")
    assert abs(stamp - (now - rel._REL_DEEP_AGAIN_S + rel._REL_DEEP_RETRY_S)) < 5
    step2 = _bind(s2, "_releases_check_one")
    step2({"artist_id": "A", "name": "Artist"}, 0)
    assert "deep" not in s2._relStepDone.emits[-1]
    real_time = time.time
    monkeypatch.setattr(rel.time, "time", lambda: real_time() + rel._REL_DEEP_RETRY_S + 1)
    step2({"artist_id": "A", "name": "Artist"}, 0)
    assert s2._relStepDone.emits[-1]["deep"] == ["get_albums"]


def test_deep_reads_and_musicbrainz_checks_fit_between_steps(tmp_path, monkeypatch):
    # 700 follows: a step a minute keeps the twelve-hour round. With a deep
    # read and a MusicBrainz check always waiting, a step must still come a
    # minute after the last one, not after every extra beat's own minute.
    clock = [1000.0]
    monkeypatch.setattr(rel.time, "monotonic", lambda: clock[0])
    s = _releases_stub(_stub(tmp_path, prefs={"releases_musicbrainz": True}), _fake_artist())
    s._releases.follow_many([{"id": f"f{i}", "name": f"F{i}"} for i in range(700)])
    for i in range(700):
        s._releases.record_check(f"f{i}", [{"id": f"r{i}", "date": "2026-01-01"}])
    step_ms = _bind(s, "_releases_step_ms")()
    assert step_ms == rel._REL_MAX_STEP_MS
    s._releases_deep_due = lambda: True
    s._rel_deep_queue.extend([("f1", "F1", ["get_albums"])] * 50)
    kinds: list = []

    # Each extra beat takes a while (paging, MusicBrainz's second a request).
    def deep(entry, gen):
        kinds.append("deep")
        clock[0] += 20.0
        s._relStepDone.emit({"deep_done": True, "gen": gen})

    def mb_check(due, gen, client):
        kinds.append("mb")
        clock[0] += 8.0
        s._relStepDone.emit({"mb_done": True, "gen": gen})

    s._releases_deep_read = deep
    s._releases_mb_check = mb_check
    s._releases_check_one = lambda row, gen: kinds.append("step") or s._relStepDone.emit(
        {"artist_id": row["artist_id"], "gen": gen}
    )
    s._releases.next_due = lambda before: {"artist_id": "f0", "name": "F0"}
    s._releases_mb_client = lambda: None
    step_times = []
    for _ in range(40):
        n = len(kinds)
        _bind(s, "_releases_tick")()
        if len(kinds) > n:
            if kinds[-1] == "step":
                step_times.append(clock[0])
            _bind(s, "_on_rel_step_done")(s._relStepDone.emits[-1])
        clock[0] += s._rel_timer.starts[-1] / 1000.0
    gaps = [b - a for a, b in itertools.pairwise(step_times)]
    assert gaps and max(gaps) <= step_ms / 1000.0 * 1.1
    assert "deep" in kinds and "mb" in kinds
    # One extra between two steps, the deep read and the check by turns:
    # never two deep reads in one slot (TIDAL's patience).
    marks = "".join("s" if k == "step" else "d" if k == "deep" else "m" for k in kinds)
    between = marks.split("s")[1:-1]
    assert between and all(len(b) <= 1 for b in between) and {"d", "m"} <= set(between)


def test_an_unfollowed_artist_leaves_the_deep_line_and_stops_its_paging(tmp_path, monkeypatch):
    monkeypatch.setattr(rel.time, "sleep", lambda t: None)
    artist = _paged_artist(_lps(130))
    s = _releases_stub(_stub(tmp_path), artist)
    s._releases.follow("B", "Other", "")
    s._releases.record_check("B", [{"id": "x", "date": "2026-01-01"}])
    s._rel_deep_queue.extend([("A", "Artist", ["get_albums"]), ("B", "Other", ["get_albums"])])
    s._objs["artist"]["B"] = artist
    _bind(s, "_releases_tick")()
    assert s._relStepDone.emits[-1]["artist_id"] == "B" and list(s._rel_deep_queue) == []
    # Unfollowed after its first page: the paging stops there.
    artist.calls.clear()
    real = artist.get_albums

    def then_unfollow(limit=None, offset=0):
        page = real(limit=limit, offset=offset)
        s._releases.unfollow("B")
        return page

    artist.get_albums = then_unfollow
    _bind(s, "_releases_deep_read")(("B", "Other", ["get_albums"]), 0)
    assert artist.calls == [("albums", rel._REL_DEEP_PAGE, 0)]
    assert s._relStepDone.emits[-1]["added"] == 0


def test_an_auto_download_gives_up_after_its_tries_or_when_refused(tmp_path):
    s = _stub(tmp_path, prefs={"releases_auto_download": True})
    s._releases.follow("A", "Artist", "")
    s._releases.record_check("A", [{"id": "a", "date": "2026-01-01"}])
    fresh = s._releases.record_check(
        "A", [{"id": "a", "date": "2026-01-01"}, {"id": "x", "date": "2026-01-02"}, {"id": "y", "date": "2026-01-02"}]
    )
    on_state = _bind(s, "_releases_on_download_state")
    _bind(s, "_releases_auto_download")(fresh)
    for _ in range(rel._REL_AUTO_DL_TRIES):
        on_state("x", "failed")
        _bind(s, "_releases_drain_auto_downloads")()
    # No download folder set: refused outright, not retried.
    on_state("y", "")
    rows = {r["id"]: r for r in s._releases.feed()}
    assert rows["x"]["dl_state"] == 0 and rows["y"]["dl_state"] == 0
    assert s.queued.count("x") == rel._REL_AUTO_DL_TRIES and s.queued.count("y") == 1


def test_auto_download_never_fires_for_a_first_check(tmp_path):
    s = _stub(tmp_path, prefs={"releases_auto_download": True})
    s._releases.follow("A", "Artist", "")
    s._relStepDone = _Signal()
    fresh = s._releases.record_check("A", [{"id": "a", "date": "2026-10-14"}])
    assert fresh == []
    _bind(s, "_on_rel_step_done")({"artist_id": "A", "name": "Artist", "fresh": fresh})
    assert s.queued == [] and s._rel_notify_pending == 0


# --- the MusicBrainz check -----------------------------------------------------


class _FakeMB:
    """A MusicBrainz client with canned answers; records what was asked."""

    def __init__(self, artists=(), groups=None, *, fail=False):
        self.artists = list(artists)
        self.groups = dict(groups or {})
        self.fail = fail
        self.calls: list = []

    def search_artist(self, name, limit=10):
        self.calls.append(("search", name))
        return None if self.fail else list(self.artists)

    def release_groups(self, mbid):
        self.calls.append(("groups", mbid))
        return None if self.fail else [dict(g) for g in self.groups.get(mbid, [])]


def _beat(s) -> None:
    """One pacer beat, with the outcome a worker posted fed to the slot the
    way the GUI thread would (the harness's signals record, never call)."""
    n = len(s._relStepDone.emits)
    _bind(s, "_releases_tick")()
    if len(s._relStepDone.emits) > n:
        _bind(s, "_on_rel_step_done")(s._relStepDone.emits[-1])


def _mb_stub(tmp_path, *, on=True, fail=False):
    """A seeded follow (so the sweep has nothing due and the check may
    run) and a fake client that knows it: two titles shared, one not."""
    s = _stub(tmp_path, prefs={"releases_musicbrainz": on, "releases_follow_view": "ledger"})
    s._releases.follow("A", "Artist", "")
    s._releases.record_check(
        "A",
        [
            {"id": "x", "date": "2020-01-01", "title": "First (Deluxe)"},
            {"id": "y", "date": "2022-06-01", "title": "Second"},
        ],
    )
    s._rel_mb_client = _FakeMB(
        [{"mbid": "mb-a", "name": "Artist", "disambiguation": "", "score": 100}],
        {
            "mb-a": [
                {"rgid": "g1", "title": "First", "date": "2020-01-01", "rtype": "album"},
                {"rgid": "g2", "title": "Second", "date": "2022-05-30", "rtype": "album"},
                {"rgid": "g3", "title": "Lost", "date": "2021-03-03", "rtype": "ep"},
            ]
        },
        fail=fail,
    )
    return s


def test_the_musicbrainz_check_waits_for_the_setting(tmp_path):
    s = _mb_stub(tmp_path, on=False)
    for _ in range(4):
        _beat(s)
    assert s._rel_mb_client.calls == [] and s._releases.follow_info("A")["mb_checked"] == 0
    assert s._releases.followed_artists()[0]["missing"] == []
    # Off, the ledger carries no marks even with rows on record.
    s._releases.record_mb("A", "mb-a", [{"rgid": "g3", "title": "Lost", "date": "2021-03-03", "rtype": "ep"}])
    _bind(s, "loadFollowed")()
    assert s.followedLoaded.emits[-1][0]["missing"] == []


def test_the_musicbrainz_check_takes_every_other_beat_and_records_what_tidal_lacks(tmp_path, caplog):
    s = _mb_stub(tmp_path)
    # The logger is hooked directly: the diagnostics stack may have cut
    # its propagation earlier in the suite.
    log = logging.getLogger("waves.releases")
    log.addHandler(caplog.handler)
    try:
        with caplog.at_level("INFO", logger="waves.releases"):
            _beat(s)
    finally:
        log.removeHandler(caplog.handler)
    fake = s._rel_mb_client
    assert fake.calls == [("search", "Artist"), ("groups", "mb-a")]
    info = s._releases.follow_info("A")
    assert info["mbid"] == "mb-a" and info["mb_checked"] > 0
    assert [(r["rgid"], r["release_id"]) for r in s._releases.mb_rows("A")] == [("g2", "y"), ("g3", ""), ("g1", "x")]
    # The marks changed: the ledger redraws, the beat is re-armed, no round counted.
    assert len(s.releasesChanged.emits) == 1 and s._rel_timer.starts and s._rel_round_done == 0
    assert not s._rel_step_running
    assert "1 release(s) not on TIDAL" in caplog.text and "mb-a" not in caplog.text
    _bind(s, "loadFollowed")()
    (row,) = s.followedLoaded.emits[-1]
    assert [m["rgid"] for m in row["missing"]] == ["g3"] and row["missing"][0]["mb"] is True
    # The check takes every other beat: the sweep's own beat between (it
    # idles, nothing is due). A second follow is checked on the next turn.
    _beat(s)
    assert len(fake.calls) == 2 and s._rel_timer.starts[-1] == rel._REL_MAX_STEP_MS
    s._releases.follow("B", "Other", "")
    s._releases.record_check("B", [{"id": "z", "date": "2020-01-01", "title": "Only"}])
    _beat(s)
    assert fake.calls[-1] == ("search", "Other")
    _beat(s)
    assert len(fake.calls) == 3
    # A thin follow with a lone namesake... none here: no sure match is
    # recorded as such, marks nothing, and is not asked again at once.
    assert s._releases.follow_info("B")["mbid"] == "" and s._releases.follow_info("B")["mb_checked"] > 0
    assert len(s.releasesChanged.emits) == 1
    # The same answer again changes nothing and redraws nothing; an artist
    # already known is paged, not searched for again.
    s._releases.mb_touch("A", 0)
    _beat(s)
    _beat(s)
    assert fake.calls[-1] == ("groups", "mb-a") and fake.calls[-2] != ("search", "Artist")
    assert len(s.releasesChanged.emits) == 1


def test_a_musicbrainz_check_that_cannot_be_made_goes_again_in_an_hour(tmp_path):
    s = _mb_stub(tmp_path, fail=True)
    _beat(s)
    stamp = s._releases.follow_info("A")["mb_checked"]
    assert abs(stamp - (time.time() - rel._REL_MB_AGAIN_S + rel._REL_MB_RETRY_S)) < 5
    assert s._releases.mb_rows("A") == [] and s.releasesChanged.emits == []
    assert not s._rel_step_running and s._rel_timer.starts
    # Not asked again before the hour; asked once it has passed.
    _beat(s)
    _beat(s)
    assert len(s._rel_mb_client.calls) == 1
    s._releases.mb_touch("A", stamp - rel._REL_MB_RETRY_S - 1)
    _beat(s)
    _beat(s)
    assert len(s._rel_mb_client.calls) == 2


def test_the_musicbrainz_check_waits_for_the_first_round_and_the_deep_read(tmp_path):
    s = _mb_stub(tmp_path)
    s._releases.follow("N", "Never", "")
    # N has never been checked: the sweep's own step takes the beat (it
    # fails here, there is no TIDAL), and MusicBrainz is not asked.
    _beat(s)
    assert s._rel_mb_client.calls == [] and s._relStepDone.emits[-1]["artist_id"] == "N"
    # A deep read in line takes the extra beat first; the check takes the
    # next one (an idle slot passes between: one extra per slot).
    s._releases.record_check("N", [{"id": "n", "date": "2020-01-01", "title": "N"}])
    s._rel_deep_queue.append(("A", "Artist", ["get_albums"]))
    s._releases_deep_read = lambda entry, gen: s._rel_deep_queue.clear()
    _beat(s)
    assert s._rel_mb_client.calls == [] and s._rel_step_running
    s._rel_step_running = False
    _beat(s)
    assert s._rel_mb_client.calls == [] and s._rel_timer.starts[-1] == rel._REL_MAX_STEP_MS
    # (That idle beat closed the round and armed the favorites walk, which is
    # not this test's.)
    s._rel_mirror_due = False
    _beat(s)
    assert s._rel_mb_client.calls[:1] == [("search", "Artist")]


def test_switching_musicbrainz_off_hides_the_marks_and_on_starts_checking(tmp_path):
    s = _mb_stub(tmp_path)
    _beat(s)
    _bind(s, "loadFollowed")()
    assert [m["rgid"] for m in s.followedLoaded.emits[-1][0]["missing"]] == ["g3"]
    s._waves_prefs["releases_musicbrainz"] = False
    _bind(s, "_releases_musicbrainz_switched")(False)
    assert len(s.releasesChanged.emits) == 2
    _bind(s, "loadFollowed")()
    assert s.followedLoaded.emits[-1][0]["missing"] == []
    # The rows stay; the roster never carries marks.
    assert len(s._releases.mb_rows("A")) == 3
    s._waves_prefs["releases_musicbrainz"] = True
    s._rel_timer.starts = []
    _bind(s, "_releases_musicbrainz_switched")(True)
    assert s._rel_timer.starts == [0]
    s._waves_prefs["releases_follow_view"] = "roster"
    _bind(s, "loadFollowed")()
    assert s.followedLoaded.emits[-1][0]["missing"] == []


def test_a_musicbrainz_check_from_a_previous_session_is_dropped(tmp_path):
    s = _mb_stub(tmp_path)
    row = s._releases.mb_next_due(1)
    _bind(s, "_releases_mb_check")(row, s._rel_gen - 1)
    assert s._relStepDone.emits == [] and s._releases.follow_info("A")["mbid"] == ""


def test_release_type_trusts_tidal_then_track_count():
    assert WavesBridge.__module__  # the helper is module-level in backend
    from waves.waves_ui.backend import _release_type

    assert _release_type(SimpleNamespace(type="EP", num_tracks=9)) == "ep"
    assert _release_type(SimpleNamespace(type="SINGLE")) == "single"
    assert _release_type(SimpleNamespace(type="ALBUM")) == "album"
    assert _release_type(SimpleNamespace(type="", num_tracks=2)) == "single"
    assert _release_type(SimpleNamespace(type=None, num_tracks=12)) == "album"
    assert _release_type(SimpleNamespace()) == "album"


# --- TIDAL favorites: the walk ------------------------------------------------


def _http_429():
    """A 429 the way tidalapi's raise_for_status raises it."""
    exc = RuntimeError("429 Client Error")
    exc.response = SimpleNamespace(status_code=429)
    return exc


class _Favs:
    """The account's favorites: ``artists`` pages, ``add_artist`` takes a
    list, ``remove_artist`` one id. Every call is recorded."""

    def __init__(self, ids=(), *, fail_page=None, fail_exc=None, write_exc=None, write_ok=True):
        self.ids = list(ids)
        self.calls: list = []
        self.fail_page = fail_page
        self.fail_exc = fail_exc or RuntimeError("down")
        self.write_exc = write_exc
        self.write_ok = write_ok

    def artists(self, limit=50, offset=0):
        self.calls.append(("page", offset))
        if self.fail_page is not None and offset // limit == self.fail_page:
            raise self.fail_exc
        return [SimpleNamespace(id=i, name=f"N{i}") for i in self.ids[offset : offset + limit]]

    def add_artist(self, ids):
        self.calls.append(("add", list(ids)))
        if self.write_exc:
            raise self.write_exc
        return self.write_ok

    def remove_artist(self, aid):
        self.calls.append(("remove", aid))
        if self.write_exc:
            raise self.write_exc
        return self.write_ok


def _mirror_stub(tmp_path, favs, monkeypatch, *, prefs=None):
    s = _stub(tmp_path, prefs=prefs)
    s.tidal = SimpleNamespace(session=SimpleNamespace(user=SimpleNamespace(favorites=favs)))
    s._fav_artist_dict = lambda a: {"id": str(a.id), "name": a.name, "art": ""}
    sleeps: list = []
    monkeypatch.setattr(rel.time, "sleep", lambda sec: sleeps.append(sec))
    return s, sleeps


def test_walk_pages_slowly_and_reconciles_when_complete(tmp_path, monkeypatch):
    favs = _Favs([str(i) for i in range(250)])
    s, sleeps = _mirror_stub(tmp_path, favs, monkeypatch)
    s._releases.sync_tidal_favorites([{"id": "gone", "name": "G"}])
    _bind(s, "_releases_mirror_walk")(0)
    # Only an empty page is the end, so the last short page is confirmed.
    assert favs.calls == [("page", 0), ("page", 100), ("page", 200), ("page", 250)]
    assert sleeps == [rel._REL_WALK_PAGE_GAP_S] * 3
    assert s._relMirrorDone.emits == [(250, 1, False)]
    assert s._releases.followed_count() == 250 and not s._releases.is_followed("gone")


def test_a_glitched_short_page_mid_list_does_not_unfollow_the_rest(tmp_path, monkeypatch):
    # TIDAL answers one page short of what it was asked without the list
    # being over: the walk reads on from where that page ended, so the
    # favorites past it are not taken for unfavorited by the full reconcile.
    favs = _Favs([str(i) for i in range(240)])
    real = favs.artists

    def glitchy(limit=50, offset=0):
        raw = real(limit=limit, offset=offset)
        return raw[:40] if offset == 100 else raw

    favs.artists = glitchy
    s, _ = _mirror_stub(tmp_path, favs, monkeypatch)
    _bind(s, "_releases_mirror_walk")(0)
    assert [off for _p, off in favs.calls] == [0, 100, 140, 240]
    assert s._relMirrorDone.emits == [(240, 0, False)]
    assert s._releases.followed_count() == 240


def test_a_walk_finishing_after_sign_out_stores_nothing(tmp_path, monkeypatch):
    # The sign-out lands between the last page and the store: the favorites
    # read belong to the account that left, so nothing is stored or stamped
    # and the next account's first walk is not skipped.
    favs = _Favs(["1"])
    s, _ = _mirror_stub(tmp_path, favs, monkeypatch)
    real = _bind(s, "_releases_walk_pages")

    def pages(gen):
        out = real(gen)
        s._rel_gen += 1  # signed out while the pages were in flight
        return out

    s._releases_walk_pages = pages
    _bind(s, "_releases_mirror_walk")(0)
    assert s._releases.followed_count() == 0
    assert (s._releases.meta_get("last_walk") or "") in ("", "0")
    assert s._relMirrorDone.emits == []


def test_walk_never_runs_twice_inside_half_an_hour(tmp_path, monkeypatch):
    favs = _Favs(["1"])
    s, _ = _mirror_stub(tmp_path, favs, monkeypatch)
    _bind(s, "_releases_mirror_walk")(0)
    _bind(s, "_releases_mirror_walk")(0)
    assert favs.calls == [("page", 0), ("page", 1)]
    assert s._relMirrorDone.emits == [(1, 0, False), (0, 0, False)]


def test_walk_cut_short_only_adds(tmp_path, monkeypatch):
    favs = _Favs([str(i) for i in range(150)], fail_page=1)
    s, _ = _mirror_stub(tmp_path, favs, monkeypatch)
    s._releases.sync_tidal_favorites([{"id": "kept", "name": "K"}])
    _bind(s, "_releases_mirror_walk")(0)
    assert s._releases.is_followed("kept") and s._releases.followed_count() == 101
    assert s._relMirrorDone.emits == [(100, 0, False)]


def test_walk_stops_at_the_page_cap_as_partial(tmp_path, monkeypatch):
    monkeypatch.setattr(rel, "_REL_WALK_MAX_PAGES", 2)
    favs = _Favs([str(i) for i in range(500)])
    s, _ = _mirror_stub(tmp_path, favs, monkeypatch)
    s._releases.sync_tidal_favorites([{"id": "kept", "name": "K"}])
    _bind(s, "_releases_mirror_walk")(0)
    assert len(favs.calls) == 2 and s._releases.is_followed("kept")


def test_walk_never_reads_an_empty_answer_as_no_favorites(tmp_path, monkeypatch):
    s, _ = _mirror_stub(tmp_path, _Favs([]), monkeypatch)
    s._releases.sync_tidal_favorites([{"id": "kept", "name": "K"}])
    _bind(s, "_releases_mirror_walk")(0)
    assert s._releases.is_followed("kept")


def test_walk_rate_limited_backs_the_sweep_off_and_walks_again_later(tmp_path, monkeypatch):
    favs = _Favs(["1"], fail_page=0, fail_exc=_http_429())
    s, _ = _mirror_stub(tmp_path, favs, monkeypatch)
    _bind(s, "_releases_mirror_walk")(0)
    assert s._relMirrorDone.emits == [(0, 0, True)]
    assert s._releases.meta_get("last_walk") == ""
    s._rel_step_running = True
    _bind(s, "_on_rel_mirror_done")(0, 0, True)
    assert s._rel_step_running is False and s._rel_backoff_until > time.monotonic()
    assert s._rel_timer.starts == [rel._REL_BACKOFF_S[0] * 1000]


def _walk_beat(s) -> None:
    """One pacer beat, with a walk's outcome fed back to its slot too."""
    n = len(s._relMirrorDone.emits)
    _beat(s)
    if len(s._relMirrorDone.emits) > n:
        _bind(s, "_on_rel_mirror_done")(*s._relMirrorDone.emits[-1])


def test_a_failed_first_walk_is_tried_again_with_no_follows(tmp_path, monkeypatch):
    """A new install whose follows all come from its TIDAL favorites: the
    first page fails (a 5xx, a timeout after a wake). Nothing is followed,
    so no round ever closes to arm the walk again; the idle sweep retries
    it instead of leaving Releases empty until a relaunch."""
    favs = _Favs(["1", "2"], fail_page=0)
    s, _ = _mirror_stub(tmp_path, favs, monkeypatch)
    s._rel_fav_known_gen = -1
    s._rel_mirror_due = True
    _walk_beat(s)
    assert favs.calls == [("page", 0)] and s._releases.meta_get("last_walk") == ""
    _walk_beat(s)
    _walk_beat(s)
    assert favs.calls == [("page", 0)]  # not before the retry pause
    s._rel_walk_at -= rel._REL_WALK_RETRY_S[0]
    _walk_beat(s)  # arms the walk
    _walk_beat(s)  # runs it, and it fails again: the next pause is longer
    assert favs.calls == [("page", 0)] * 2
    s._rel_walk_at -= rel._REL_WALK_RETRY_S[0]
    _walk_beat(s)
    _walk_beat(s)
    assert len(favs.calls) == 2
    s._rel_walk_at -= rel._REL_WALK_RETRY_S[1]
    favs.fail_page = None
    _walk_beat(s)
    _walk_beat(s)
    assert s._releases.followed_count() == 2 and s._releases.meta_get("last_walk") != ""
    assert s._rel_fav_known_gen == s._rel_gen and s._rel_walk_fails == 0


def test_a_rate_limited_first_walk_is_tried_again_after_the_pause(tmp_path, monkeypatch):
    favs = _Favs(["1"], fail_page=0, fail_exc=_http_429())
    s, _ = _mirror_stub(tmp_path, favs, monkeypatch)
    s._rel_fav_known_gen = -1
    s._rel_mirror_due = True
    _walk_beat(s)
    assert s._rel_backoff_until > time.monotonic()
    s._rel_backoff_until = 0.0
    s._rel_walk_at -= rel._REL_WALK_RETRY_S[0]
    favs.fail_page = None
    _walk_beat(s)
    _walk_beat(s)
    assert s._releases.followed_count() == 1


def test_a_walk_that_breaks_while_storing_is_tried_again(tmp_path, monkeypatch):
    s, _ = _mirror_stub(tmp_path, _Favs(["1"]), monkeypatch)
    s._rel_fav_known_gen = -1

    def broken(rows, partial):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(s._releases, "sync_tidal_favorites", broken)
    _bind(s, "_releases_mirror_walk")(0)
    assert s._rel_walk_fails == 1 and s._releases.meta_get("last_walk") == ""


def test_walk_from_a_previous_session_is_dropped(tmp_path, monkeypatch):
    s, _ = _mirror_stub(tmp_path, _Favs(["1"]), monkeypatch)
    s._rel_gen = 3
    _bind(s, "_releases_mirror_walk")(2)
    assert s._relMirrorDone.emits == [] and s._releases.followed_count() == 0


def test_tick_dispatches_the_walk_when_due(tmp_path, monkeypatch):
    s, _ = _mirror_stub(tmp_path, _Favs(["1"]), monkeypatch)
    s._rel_mirror_due = True
    _bind(s, "_releases_tick")()
    assert s._relMirrorDone.emits == [(1, 0, False)] and s._rel_mirror_due is False


def test_a_my_tidal_artists_page_adds_but_never_removes(tmp_path):
    s = _stub(tmp_path)
    s._releases.sync_tidal_favorites([{"id": "a", "name": "A"}])
    _bind(s, "_releases_note_favorites")([{"id": "b", "name": "B"}])
    assert s._releases.is_followed("a") and s._releases.is_followed("b")
    assert s.releasesChanged.emits == [()]
    _bind(s, "_releases_note_favorites")([{"id": "b", "name": "B"}])
    assert len(s.releasesChanged.emits) == 1


# --- TIDAL favorites: the rationed writes ------------------------------------


def _drain(s):
    """Fire the drain timer and land the write, as the event loop would."""
    _bind(s, "_releases_tidal_drain")()
    while s._relTidalDone.emits:
        _bind(s, "_on_rel_tidal_done")(s._relTidalDone.emits.pop(0))


def test_the_mirror_is_off_by_default_and_sends_nothing(tmp_path, monkeypatch):
    favs = _Favs()
    s, _ = _mirror_stub(tmp_path, favs, monkeypatch)
    _bind(s, "followArtist")("A", "Artist", "")
    _bind(s, "unfollowArtist")("A")
    assert s._rel_tidal_out == {} and s._rel_tidal_timer.starts == [] and favs.calls == []


def test_follows_settle_then_go_out_in_one_request(tmp_path, monkeypatch):
    favs = _Favs()
    s, _ = _mirror_stub(tmp_path, favs, monkeypatch, prefs={"follow_mirror_tidal": True})
    for a in ("A", "B", "C"):
        _bind(s, "followArtist")(a, a, "")
    assert s._rel_tidal_timer.starts == [rel._REL_TIDAL_SETTLE_MS]
    assert _bind(s, "tidalFavouritePending")("B") is True
    _drain(s)
    assert favs.calls == [("add", ["A", "B", "C"])]
    assert all(s._releases.follow_info(a)["tidal_fav"] for a in "ABC")
    assert sorted(s.followTidalChanged.emits) == [("A", True), ("B", True), ("C", True)]
    assert _bind(s, "tidalFavouritePending")("B") is False


def test_a_follow_undone_inside_the_settle_never_leaves(tmp_path, monkeypatch):
    favs = _Favs()
    s, _ = _mirror_stub(tmp_path, favs, monkeypatch, prefs={"follow_mirror_tidal": True})
    _bind(s, "followArtist")("A", "Artist", "")
    _bind(s, "unfollowArtist")("A")
    _drain(s)
    assert favs.calls == [] and s._rel_tidal_out == {}


def test_following_an_existing_favorite_sends_nothing(tmp_path, monkeypatch):
    favs = _Favs()
    s, _ = _mirror_stub(tmp_path, favs, monkeypatch, prefs={"follow_mirror_tidal": True})
    s._releases.sync_tidal_favorites([{"id": "A", "name": "A"}])
    _bind(s, "followArtist")("A", "A", "")
    _bind(s, "unfollowArtist")("Z")
    assert s._rel_tidal_out == {}


def test_unfollows_go_one_per_request_spaced_apart(tmp_path, monkeypatch):
    favs = _Favs()
    s, _ = _mirror_stub(tmp_path, favs, monkeypatch, prefs={"follow_mirror_tidal": True})
    s._releases.sync_tidal_favorites([{"id": "A", "name": "A"}, {"id": "B", "name": "B"}])
    _bind(s, "unfollowArtist")("A")
    _bind(s, "unfollowArtist")("B")
    _bind(s, "_releases_tidal_drain")()
    assert favs.calls == [("remove", "A")]
    # In flight: the drain sends nothing more until the answer lands.
    _bind(s, "_releases_tidal_drain")()
    assert len(favs.calls) == 1
    s._rel_tidal_timer._active = False
    _bind(s, "_on_rel_tidal_done")(s._relTidalDone.emits.pop(0))
    # The next request waits out the spacing after the last one.
    assert rel._REL_TIDAL_GAP_MS - 200 <= s._rel_tidal_timer.starts[-1] <= rel._REL_TIDAL_GAP_MS
    _drain(s)
    assert favs.calls == [("remove", "A"), ("remove", "B")]


def test_a_large_run_of_follows_is_batched_and_capped_per_hour(tmp_path, monkeypatch):
    favs = _Favs()
    s, _ = _mirror_stub(tmp_path, favs, monkeypatch, prefs={"follow_mirror_tidal": True})
    for i in range(120):
        _bind(s, "followArtist")(f"a{i}", "x", "")
    _drain(s)
    _drain(s)
    _drain(s)
    assert [len(c[1]) for c in favs.calls] == [50, 50, 20]
    # The hourly ceiling: with it reached, the drain waits instead of sending.
    s._rel_tidal_sent = collections.deque([time.monotonic()] * rel._REL_TIDAL_PER_HOUR)
    _bind(s, "followArtist")("late", "x", "")
    _bind(s, "_releases_tidal_drain")()
    assert len(favs.calls) == 3 and s._rel_tidal_out == {"late": True}
    assert s._rel_tidal_timer.starts[-1] > 3_000_000


def test_a_rate_limited_write_pauses_and_keeps_the_change(tmp_path, monkeypatch):
    favs = _Favs(write_exc=_http_429())
    s, _ = _mirror_stub(tmp_path, favs, monkeypatch, prefs={"follow_mirror_tidal": True})
    _bind(s, "followArtist")("A", "A", "")
    _drain(s)
    assert s._rel_tidal_out == {"A": True} and s._rel_tidal_backoff_until > time.monotonic()
    assert s.followTidalChanged.emits == [("A", False)]
    s._rel_tidal_timer._active = False
    _bind(s, "_releases_tidal_drain")()
    assert len(favs.calls) == 1
    assert s._rel_tidal_timer.starts[-1] > (rel._REL_TIDAL_BACKOFF_S - 5) * 1000


def test_a_refused_write_is_not_retried_and_the_follow_stays(tmp_path, monkeypatch):
    favs = _Favs(write_ok=False)
    s, _ = _mirror_stub(tmp_path, favs, monkeypatch, prefs={"follow_mirror_tidal": True})
    _bind(s, "followArtist")("A", "A", "")
    _drain(s)
    assert s._rel_tidal_out == {} and s._releases.is_followed("A")
    assert not s._releases.follow_info("A")["tidal_fav"]
    assert s.followTidalChanged.emits == [("A", False)]


def test_an_unfollow_while_the_follow_is_in_flight_still_goes_out(tmp_path, monkeypatch):
    favs = _Favs()
    s, _ = _mirror_stub(tmp_path, favs, monkeypatch, prefs={"follow_mirror_tidal": True})
    _bind(s, "followArtist")("A", "A", "")
    _bind(s, "_releases_tidal_drain")()
    _bind(s, "unfollowArtist")("A")
    assert s._rel_tidal_out == {"A": False}
    _bind(s, "_on_rel_tidal_done")(s._relTidalDone.emits.pop(0))
    _drain(s)
    assert favs.calls == [("add", ["A"]), ("remove", "A")]


def test_sign_out_drops_pending_writes(tmp_path, monkeypatch):
    favs = _Favs()
    s, _ = _mirror_stub(tmp_path, favs, monkeypatch, prefs={"follow_mirror_tidal": True})
    _bind(s, "followArtist")("A", "A", "")
    _bind(s, "_releases_stop")()
    s._logged_in = False
    _drain(s)
    assert favs.calls == [] and s._rel_tidal_out == {}
    s._logged_in = True
    _bind(s, "_releases_tidal_enqueue")("B", True)
    assert s._rel_tidal_out == {"B": True}


def test_a_reset_mid_write_drops_its_answer_and_unblocks(tmp_path, monkeypatch):
    favs = _Favs()
    s, _ = _mirror_stub(tmp_path, favs, monkeypatch, prefs={"follow_mirror_tidal": True})
    s._rel_tidal_inflight = {"stale"}
    s._rel_gen = 1  # a factory reset bumps the generation only
    _bind(s, "followArtist")("A", "A", "")
    _drain(s)
    assert favs.calls == [("add", ["A"])]


def test_switching_the_mirror_off_drops_its_queued_changes_but_not_hand_asks(tmp_path, monkeypatch):
    favs = _Favs()
    s, _ = _mirror_stub(tmp_path, favs, monkeypatch, prefs={"follow_mirror_tidal": True})
    s._releases.sync_tidal_favorites([{"id": f"f{i}", "name": "x"} for i in range(5)])
    for i in range(5):
        _bind(s, "unfollowArtist")(f"f{i}")
    s._releases.follow("H", "Hand", "")
    _bind(s, "followOnTidal")("H")
    _drain(s)  # one request goes out (the hand add)
    assert len(favs.calls) == 1
    s._waves_prefs["follow_mirror_tidal"] = False
    _bind(s, "_releases_mirror_switched")(False)
    assert s._rel_tidal_out == {}
    s._rel_tidal_timer._active = False
    _drain(s)
    assert len(favs.calls) == 1
    # A hand ask still waiting survives the switch.
    s._releases.follow("K", "Kept", "")
    _bind(s, "followOnTidal")("K")
    _bind(s, "_releases_mirror_switched")(False)
    assert s._rel_tidal_out == {"K": True}


def test_a_new_account_does_not_trust_the_last_accounts_favorites(tmp_path, monkeypatch):
    favs = _Favs()
    s, _ = _mirror_stub(tmp_path, favs, monkeypatch, prefs={"follow_mirror_tidal": True})
    # Account A walked: X is A's favorite, and the walk is recent.
    s._releases.sync_tidal_favorites([{"id": "X", "name": "X"}])
    s._releases.meta_set("last_walk", str(int(time.time())))
    _bind(s, "_releases_stop")()
    _bind(s, "_releases_signed_out")()
    assert s._releases.meta_get("last_walk") == "0"
    # Account B signs in. Its first walk is not skipped as "recent"...
    s._rel_fav_known_gen = -1
    _bind(s, "_releases_mirror_walk")(s._rel_gen)
    assert favs.calls == [("page", 0)] and s._rel_fav_known_gen == s._rel_gen
    # ...and before it lands, X's stale flag does not swallow B's ask.
    s._releases.mark_tidal_fav("X", True)
    s._rel_fav_known_gen = -1
    _bind(s, "followOnTidal")("X")
    _drain(s)
    assert favs.calls[-1] == ("add", ["X"])


def test_an_unfollow_and_refollow_inside_the_settle_sends_nothing(tmp_path, monkeypatch):
    favs = _Favs()
    s, _ = _mirror_stub(tmp_path, favs, monkeypatch, prefs={"follow_mirror_tidal": True})
    s._releases.sync_tidal_favorites([{"id": "X", "name": "X"}])
    _bind(s, "unfollowArtist")("X")
    _bind(s, "followArtist")("X", "X", "")
    _drain(s)
    assert favs.calls == [] and s._rel_tidal_out == {}


def test_a_write_queued_before_a_sign_out_is_never_sent(tmp_path, monkeypatch):
    favs = _Favs()
    s, _ = _mirror_stub(tmp_path, favs, monkeypatch, prefs={"follow_mirror_tidal": True})
    _bind(s, "_releases_tidal_write")(["A"], True, s._rel_gen - 1)
    assert favs.calls == [] and s._relTidalDone.emits == []


def test_the_write_spacing_runs_from_the_answer(tmp_path, monkeypatch):
    favs = _Favs()
    s, _ = _mirror_stub(tmp_path, favs, monkeypatch, prefs={"follow_mirror_tidal": True})
    clock = [1000.0]
    monkeypatch.setattr(rel.time, "monotonic", lambda: clock[0])
    _bind(s, "followArtist")("A", "A", "")
    _bind(s, "followArtist")("B", "B", "")
    s._rel_tidal_out = {"A": True}
    _bind(s, "_releases_tidal_drain")()
    s._rel_tidal_out["B"] = True
    clock[0] += 50  # the request waited for the pool, then landed
    s._rel_tidal_timer._active = False
    _bind(s, "_on_rel_tidal_done")(s._relTidalDone.emits.pop(0))
    assert s._rel_tidal_timer.starts[-1] >= rel._REL_TIDAL_GAP_MS - 1


def test_a_step_landing_after_a_sign_out_does_nothing(tmp_path):
    s = _stub(tmp_path, prefs={"releases_auto_download": True})
    old = s._rel_gen
    s._rel_gen += 1
    s._rel_step_running = True  # the new session's own step
    _bind(s, "_on_rel_step_done")(
        {"artist_id": "A", "name": "A", "fresh": [{"id": "x", "date": "2026-01-01"}], "gen": old}
    )
    assert s._rel_notify_pending == 0 and s.queued == [] and s._rel_timer.starts == []
    assert s._rel_step_running is True


def test_also_follow_on_tidal_sends_one_artist_without_the_pref(tmp_path, monkeypatch):
    favs = _Favs()
    s, _ = _mirror_stub(tmp_path, favs, monkeypatch)
    s._releases.follow("A", "A", "")
    assert _bind(s, "followMirrorsTidal")() is False
    _bind(s, "followOnTidal")("A")
    _drain(s)
    assert favs.calls == [("add", ["A"])] and s._releases.follow_info("A")["tidal_fav"]


# --- music videos -------------------------------------------------------------


class _Video(SimpleNamespace):
    def image(self, width, height):
        return f"still{width}x{height}"


def _video_artist(videos, calls, *, raise_exc=None):
    def videos_fn(limit=None):
        calls.append(limit)
        if raise_exc is not None:
            raise raise_exc
        return list(videos)

    return SimpleNamespace(
        get_albums=lambda limit=None: [_Album(id="1", title="LP")],
        get_ep_singles=lambda limit=None: [],
        get_videos=videos_fn,
    )


def _step(s, aid="A"):
    _bind(s, "_releases_check_one")({"artist_id": aid, "name": "Artist"}, 0)
    return s._relStepDone.emits[-1]


def test_a_step_reads_videos_takes_the_first_answer_in_silently_then_announces(tmp_path, monkeypatch):
    calls: list = []
    vids = [_Video(id="7", title="Clip", date="2026-09-01", duration=215, artist=SimpleNamespace(id=5))]
    s = _releases_stub(_stub(tmp_path), _video_artist(vids, calls))
    s._releases.follow("A", "Artist", "")
    out = _step(s)
    assert out["fresh"] == [] and calls == [rel._REL_FETCH_LIMIT]
    row = next(r for r in s._releases.feed() if r["kind"] == "video")
    assert row["id"] == "7" and row["rtype"] == "video" and row["art"] == "still480x320"
    assert row["duration_sec"] == 215 and row["quality"] == "1080p" and row["artist_id"] == "5"
    # Read again the same day: not due, no request.
    _step(s)
    assert len(calls) == 1
    # A day on, a new video is news and names itself in the banner.
    later = time.time() + 86400
    monkeypatch.setattr(rel.time, "time", lambda: later)
    vids.insert(0, _Video(id="8", title="New clip", date="2026-10-10", duration=200))
    out = _step(s)
    assert len(calls) == 2 and [(r["id"], r["kind"]) for r in out["fresh"]] == [("8", "video")]
    _bind(s, "_on_rel_step_done")(out)
    assert s._rel_notify_items == [{"artist": "Artist", "title": "New clip", "rtype": "video"}]
    assert rel._releases_banner(1, s._rel_notify_items) == ("New video from Artist", "New clip")


def test_hidden_videos_are_never_read_nor_shown(tmp_path):
    calls: list = []
    vids = [_Video(id="7", title="Clip", date="2026-09-01", duration=1)]
    s = _releases_stub(_stub(tmp_path, prefs={"releases_videos": "hide"}), _video_artist(vids, calls))
    s._releases.follow("A", "Artist", "")
    _step(s)
    assert calls == []
    s._waves_prefs["releases_videos"] = "show"
    s._releases.record_videos("A", [{"id": "7", "title": "Clip", "date": "2026-09-01"}])
    s._waves_prefs["releases_videos"] = "hide"
    _bind(s, "loadReleases")(0)
    rows = s.releasesLoaded.emits[-1][0]
    assert all(r["kind"] != "video" for r in rows) and rows
    _bind(s, "loadFollowed")()
    follow = s.followedLoaded.emits[-1][0]
    assert all(e.get("kind") != "video" for e in follow["releases"])


def test_hidden_featured_releases_are_never_shown_announced_nor_downloaded(tmp_path, monkeypatch):
    s = _stub(tmp_path, prefs={"releases_featured": "hide", "releases_auto_download": True})
    queued: list = []
    monkeypatch.setattr(
        s, "_releases_auto_download", lambda fresh: queued.extend(r["id"] for r in fresh), raising=False
    )
    s._releases.follow("A", "Artist", "")
    s._releases.follow("B", "Band", "")
    fresh = [
        {"id": "g", "date": "2026-10-01", "title": "Lullabies", "rtype": "album", "artist_id": "Z"},
        {"id": "c", "date": "2026-10-01", "title": "Collab", "rtype": "single", "artist_id": "B"},
        {"id": "n", "date": "2026-10-01", "title": "Own", "rtype": "ep", "artist_id": "A"},
    ]
    _bind(s, "_on_rel_step_done")({"artist_id": "A", "name": "Artist", "fresh": fresh})
    assert [i["title"] for i in s._rel_notify_items] == ["Collab", "Own"] and queued == ["c", "n"]
    s._releases.record_check("A", [{**r, "artist": "x"} for r in fresh])
    _bind(s, "loadReleases")(0)
    assert sorted(r["id"] for r in s.releasesLoaded.emits[-1][0]) == ["c", "n"]
    s._waves_prefs["releases_featured"] = "show"
    _bind(s, "_releases_featured_switched")(True)
    assert s.releasesChanged.emits[-1] == ()
    _bind(s, "loadReleases")(0)
    assert sorted(r["id"] for r in s.releasesLoaded.emits[-1][0]) == ["c", "g", "n"]


def test_showing_videos_again_takes_the_next_read_in_silently(tmp_path):
    s = _stub(tmp_path)
    s._releases.follow("A", "Artist", "")
    s._releases.record_videos("A", [{"id": "1", "date": "2026-01-01"}])
    assert s._releases.record_videos("A", [{"id": "2", "date": "2026-10-01"}]) != []
    s._waves_prefs["releases_videos"] = "show"
    _bind(s, "_releases_videos_switched")(True)
    assert s._releases.record_videos("A", [{"id": "3", "date": "2026-10-02"}]) == []
    assert s.releasesChanged.emits


def test_a_rate_limit_on_the_video_read_still_announces_the_music(tmp_path, monkeypatch):
    calls: list = []
    exc = type("TooManyRequests", (Exception,), {})()
    s = _releases_stub(_stub(tmp_path), _video_artist([], calls, raise_exc=exc))
    s._releases.follow("A", "Artist", "")
    s._releases.record_check("A", [{"id": "old", "date": "2026-01-01"}])
    out = _step(s)
    assert out.get("video_limited") is True and not out.get("rate_limited")
    assert [r["id"] for r in out["fresh"]] == ["1"]
    monkeypatch.setattr(rel.time, "monotonic", lambda: 1000.0)
    _bind(s, "_on_rel_step_done")(out)
    assert s._rel_notify_pending == 1
    assert s._rel_backoff_until == 1000.0 + rel._REL_BACKOFF_S[0]
    # Not stamped: the videos are read again on the next visit.
    assert s._releases.video_due("A", int(time.time()))


def test_auto_download_takes_a_video_only_with_music_videos_on(tmp_path):
    s = _stub(tmp_path, prefs={"releases_auto_download": True})
    s._releases.follow("A", "Artist", "")
    s._releases.record_videos("A", [{"id": "1", "date": "2026-01-01"}])
    fresh = s._releases.record_videos(
        "A", [{"id": "1", "date": "2026-01-01"}, {"id": "2", "date": "2026-10-01"}, {"id": "3", "date": "2099-01-01"}]
    )
    _bind(s, "_releases_auto_download")(fresh)
    assert s.queued_videos == [] and s.queued == []
    s.settings = SimpleNamespace(data=SimpleNamespace(video_download=True))
    _bind(s, "_releases_auto_download")(fresh)
    assert s.queued_videos == ["2"] and s.queued == []
    rows = {r["id"]: r for r in s._releases.feed()}
    assert rows["2"]["dl_state"] == DL_QUEUED and rows["3"]["dl_state"] == DL_WAITING
    s._releases_today = lambda: "2099-01-01"
    _bind(s, "_releases_drain_auto_downloads")()
    assert s.queued_videos == ["2", "3"]
    # A video's outcome settles its own row.
    _bind(s, "_releases_on_download_state")("3", "queued")
    assert "3" not in s._rel_auto_videos


def test_check_now_makes_every_follow_due_at_the_burst_pace(tmp_path, monkeypatch):
    s = _releases_stub(_stub(tmp_path), _fake_artist(albums=[_Album(id="1", title="LP")]))
    s._releases.follow("A", "Artist", "")
    # While a first check is still going, Check now is refused.
    assert _bind(s, "releasesCheckNow")() is False
    s._releases.record_check("A", [{"id": "1", "date": "2026-01-01"}])
    assert _bind(s, "releasesUnchecked")() == 0
    assert _bind(s, "_releases_step_ms")() > rel._REL_BURST_MS
    later = time.time() + 5
    monkeypatch.setattr(rel.time, "time", lambda: later)
    assert _bind(s, "releasesCheckNow")() is True
    assert s.releasesProgress.emits[-1] == (0, 1) and s._rel_timer.starts[-1] == 0
    assert _bind(s, "releasesUnchecked")() == 1
    assert _bind(s, "_releases_step_ms")() == rel._REL_BURST_MS
    # A second press while it runs does nothing.
    assert _bind(s, "releasesCheckNow")() is False and _bind(s, "releasesCheckNowReady")() is False
    # The tick picks the follow although its round is not up, and its videos too.
    assert s._releases.video_due("A", int(later), s._releases.rescan_at())
    monkeypatch.setattr(rel.time, "time", lambda: later + 1)
    _bind(s, "_releases_tick")()
    assert s._relStepDone.emits and s._relStepDone.emits[-1]["artist_id"] == "A"
    assert _bind(s, "releasesUnchecked")() == 0
    # Done, it rests for an hour before it may run again.
    assert _bind(s, "releasesCheckNowReady")() is False and _bind(s, "releasesCheckNow")() is False
    monkeypatch.setattr(rel.time, "time", lambda: later + rel._REL_CHECK_NOW_GAP_S + 1)
    assert _bind(s, "releasesCheckNowReady")() is True


def test_a_clock_set_back_after_check_now_does_not_keep_every_follow_due(tmp_path):
    s = _stub(tmp_path)
    s._releases.follow("A", "Artist", "")
    s._releases.record_check("A", [{"id": "1", "date": "2026-01-01"}])
    # Check now stamped an hour ahead of the clock (the clock then went back).
    s._releases.request_rescan(int(time.time()) + 3600)
    assert s._releases.rescan_at() == 0
    # A follow checked a few seconds ago is not due again, and Check now may run.
    with s._releases._lock:
        s._releases._conn.execute("UPDATE followed SET last_checked = ?", (int(time.time()) - 5,))
        s._releases._conn.commit()
    assert _bind(s, "releasesUnchecked")() == 0 and _bind(s, "releasesCheckNowReady")() is True


def test_a_video_rate_limit_backs_off_further_each_time(tmp_path, monkeypatch):
    s = _stub(tmp_path)
    s._releases.follow("A", "Artist", "")
    monkeypatch.setattr(rel.time, "monotonic", lambda: 1000.0)
    done = _bind(s, "_on_rel_step_done")
    done({"artist_id": "A", "name": "Artist", "fresh": [], "video_limited": True})
    assert s._rel_backoff_until == 1000.0 + rel._REL_BACKOFF_S[0]
    done({"artist_id": "A", "name": "Artist", "fresh": [], "video_limited": True})
    assert s._rel_backoff_until == 1000.0 + rel._REL_BACKOFF_S[1]
    # A clean step brings the ladder back down.
    done({"artist_id": "A", "name": "Artist", "fresh": []})
    assert s._rel_backoff_level == 0


def test_check_now_needs_a_sign_in_and_a_follow(tmp_path):
    s = _stub(tmp_path)
    assert _bind(s, "releasesCheckNow")() is False
    s._releases.follow("A", "Artist", "")
    s._releases.record_check("A", [{"id": "1", "date": "2026-01-01"}])
    s._logged_in = False
    assert _bind(s, "releasesCheckNow")() is False


# --- what the auto-download takes ------------------------------------------


def test_the_kinds_text_keeps_known_kinds_in_order():
    assert rel.releases_auto_kinds_text("single, ALBUM,bogus,featured") == "album,single,featured"
    assert rel.releases_auto_kinds_text(["ep"]) == "ep"
    assert rel.releases_auto_kinds_text("") == ""


def test_auto_download_takes_only_the_ticked_kinds(tmp_path):
    s = _stub(tmp_path, prefs={"releases_auto_download": True, "releases_auto_kinds": "album,single"})
    s._releases.follow("A", "Artist", "")
    s._releases.record_check("A", [{"id": "a", "date": "2026-01-01"}])
    fresh = s._releases.record_check(
        "A",
        [
            {"id": "a", "date": "2026-01-01"},
            {"id": "lp", "date": "2026-01-02", "rtype": "album"},
            {"id": "ep", "date": "2026-01-02", "rtype": "ep"},
            {"id": "sg", "date": "2026-01-02", "rtype": "single"},
            {"id": "sg2", "date": "2099-01-01", "rtype": "single"},
        ],
    )
    _bind(s, "_releases_auto_download")(fresh)
    assert s.queued == ["lp", "sg"]
    # Singles unticked while one waits for its day: it is not fetched.
    s._waves_prefs["releases_auto_kinds"] = "album"
    s._releases_today = lambda: "2099-01-01"
    _bind(s, "_releases_drain_auto_downloads")()
    assert s.queued == ["lp", "sg"]
    assert {r["id"]: r for r in s._releases.feed()}["sg2"]["dl_state"] == 0


def test_the_default_kinds_leave_featured_out(tmp_path):
    s = _stub(tmp_path, prefs={"releases_auto_download": True})
    assert _bind(s, "_releases_auto_kinds")() == frozenset({"album", "ep", "single"})
    assert not _bind(s, "_releases_guest_on")()
    s._waves_prefs["releases_auto_kinds"] = "featured"
    assert _bind(s, "_releases_guest_on")()
    s._waves_prefs["releases_auto_download"] = False
    assert not _bind(s, "_releases_guest_on")()


def _guest_artist(others, *, fail=None):
    def other_fn(limit=None):
        if fail is not None:
            raise fail
        return list(others)

    return SimpleNamespace(get_albums=lambda limit=None: [], get_ep_singles=lambda limit=None: [], get_other=other_fn)


def _guest_step(s, artist):
    """One sweep step for follow A reading ``artist``, landed on the GUI side."""
    s._objs["artist"]["A"] = artist
    _bind(s, "_releases_check_one")({"artist_id": "A", "name": "Artist"}, s._rel_gen)
    result = s._relStepDone.emits[-1]
    _bind(s, "_on_rel_step_done")(result)
    return result


def test_featured_releases_are_read_only_when_ticked_and_download_the_follows_tracks(tmp_path):
    s = _releases_stub(_stub(tmp_path, prefs={"releases_auto_download": True}), None)
    s._artist_recent_releases = lambda aid: ([{"id": "a", "date": "2026-01-01"}], [], False)
    s._releases_check_videos = lambda aid: []
    s._releases_deep_ask = lambda result: None
    s._releases_extra_pending = lambda: False
    s._releases.follow("A", "Artist", "")
    feat = _Album(id="f1", title="Duet", date="2026-01-01")
    comp = _Album(id="c1", title="Hits", date="2026-01-01", comp=True)
    new = _Album(id="f2", title="Remix", date="2026-01-03")
    newcomp = _Album(id="c2", title="More Hits", date="2026-01-03", comp=True)
    # Featured unticked: never read.
    assert _guest_step(s, _guest_artist([feat]))["guests"] == []
    assert s._releases.guest_due("A", int(time.time()))
    s._waves_prefs["releases_auto_kinds"] = "album,featured"
    # The first read is the silent baseline.
    assert _guest_step(s, _guest_artist([feat, comp]))["guests"] == []
    # A day later: the new featured release brings the follow's tracks; a
    # Various Artists compilation never does.
    s._releases._conn.execute("UPDATE followed SET guest_checked = 1")
    _guest_step(s, _guest_artist([newcomp, new, feat, comp]))
    assert s.queued_tracks == ["f2:A"]
    assert all(r["id"] != "f2" for r in s._releases.feed())
    assert s._rel_notify_pending == 0
    # Read again only a day on.
    _guest_step(s, _guest_artist([_Album(id="f3", title="Later", date="2026-01-04"), new, feat]))
    assert s.queued_tracks == ["f2:A"]


def test_a_rate_limited_featured_read_backs_off(tmp_path):
    s = _releases_stub(_stub(tmp_path, prefs={"releases_auto_download": True, "releases_auto_kinds": "featured"}), None)
    s._artist_recent_releases = lambda aid: ([{"id": "a", "date": "2026-01-01"}], [], False)
    s._releases_check_videos = lambda aid: []
    s._releases.follow("A", "Artist", "")
    limit = RuntimeError("429")
    limit.response = SimpleNamespace(status_code=429)
    s._objs["artist"]["A"] = _guest_artist([], fail=limit)
    _bind(s, "_releases_check_one")({"artist_id": "A", "name": "Artist"}, s._rel_gen)
    result = s._relStepDone.emits[-1]
    assert result["video_limited"] is True and result["guests"] == []
    # Still due: the next visit reads it.
    assert s._releases.guest_due("A", int(time.time()))


def test_a_featured_release_dated_ahead_waits_and_a_failed_read_tries_again(tmp_path):
    s = _stub(tmp_path, prefs={"releases_auto_download": True, "releases_auto_kinds": "featured"})
    s._releases.follow("A", "Artist", "")
    guests = _bind(s, "_releases_auto_download_guests")
    drain = _bind(s, "_releases_drain_auto_downloads")
    guests(
        [{"id": "soon", "date": "2099-01-01", "guest_of": "A"}, {"id": "now", "date": "2026-01-01", "guest_of": "A"}]
    )
    assert s.queued_tracks == ["now:A"]
    s._releases_today = lambda: "2099-01-01"
    drain()
    assert s.queued_tracks == ["now:A", "soon:A"]
    calls = []

    def flaky(rid, aid):
        calls.append(rid)
        raise RuntimeError("dropped")

    s._guest_track_keys = flaky
    guests([{"id": "bad", "date": "2026-01-01", "guest_of": "A"}])
    for _ in range(5):
        drain()
    assert calls == ["bad"] * rel._REL_AUTO_DL_TRIES

    # A read that fails after Featured was unticked does not wait again.
    def unticked_meanwhile(rid, aid):
        s._waves_prefs["releases_auto_kinds"] = "album"
        raise RuntimeError("dropped")

    s._guest_track_keys = unticked_meanwhile
    _bind(s, "_releases_queue_guest")("late", "A")
    assert s._releases.guests_due("9999-12-31") == []


def test_a_featured_release_a_follow_lists_as_its_own_is_left_to_that_row(tmp_path):
    s = _stub(tmp_path, prefs={"releases_auto_download": True, "releases_auto_kinds": "album,featured"})
    s._releases.follow("B", "Other", "")
    s._releases.record_check("B", [{"id": "x", "date": "2026-01-01", "rtype": "album"}])
    s._releases.record_check(
        "B", [{"id": "s", "date": "2026-01-01", "rtype": "single"}, {"id": "x", "date": "2026-01-01"}]
    )
    guests = _bind(s, "_releases_auto_download_guests")
    guests([{"id": "x", "date": "2026-01-01", "guest_of": "A"}, {"id": "s", "date": "2026-01-01", "guest_of": "A"}])
    # The album downloads whole through B's own row; the unticked single
    # still brings A's tracks.
    assert s.queued_tracks == ["s:A"]


def test_switching_featured_on_reseeds_and_off_drops_the_waiting(tmp_path):
    s = _stub(tmp_path, prefs={"releases_auto_download": True, "releases_auto_kinds": "featured"})
    s._releases.follow("A", "Artist", "")
    s._releases.record_guests("A", [{"id": "g1", "date": "2026-01-01"}])
    s._releases.guest_wait("w", "2099-01-01", "A")
    switched = _bind(s, "_releases_guest_switched")
    switched(False)
    assert s._releases.guests_due("2099-01-01") == []
    switched(True)
    assert s._releases.record_guests("A", [{"id": "g2", "date": "2026-01-02"}]) == []
