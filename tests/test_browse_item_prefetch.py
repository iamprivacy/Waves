"""The item page (playlist / mix / album) paints its art from what the card
already fetched, and a hover can have the page ready before the click.

Cover URLs embed their pixel size, so a hero requested at a size the cards
never fetch is a cold download on every first open, sitting on the "art: GET"
placeholder while the card's own thumbnail is right there on screen. These
pin the size discipline (explicit, not tidalapi's size-rejection fallback)
and the silent hover prefetch slot.
"""

from __future__ import annotations

from threading import Lock
from types import SimpleNamespace

import pytest
from conftest import _InlinePool, _Signal
from tidalapi.media import Track

import waves.waves_ui.backend as backend
from waves.waves_ui.backend import WavesBridge

# ----- fakes ----------------------------------------------------------------


class _Cover:
    """A tidalapi-shaped media object whose image() honours EVERY size it is
    asked for, so a header that asked for 480 would actually get a 480 URL
    (tidalapi rejects 480 for albums, which used to hide the bug there)."""

    def __init__(self, kind, mid, tracks):
        self.kind = kind
        self.id = mid
        self.name = f"{kind} {mid}"
        self.full_name = self.name
        self.artist = SimpleNamespace(id=7, name="Artist", roles=None)
        self.artists = [self.artist]
        self.audio_modes = None
        self.audio_quality = None
        self.media_metadata_tags = None
        self.release_date = None
        self.creator = SimpleNamespace(name="Curator")
        self.description = ""
        self._tracks = tracks

    def image(self, dimensions=320):
        return f"https://img.test/{self.kind}/{self.id}/{dimensions}x{dimensions}.jpg"

    def tracks(self, limit=None):
        return list(self._tracks)

    def items(self, limit=100, offset=0):
        return list(self._tracks)[offset : offset + limit]


def _fake_track(tid, album):
    # A real Track (the page builder keeps only Track | Video rows), unbuilt.
    t = Track.__new__(Track)
    t.id = tid
    t.duration = 200
    t.album = album
    t.name = t.full_name = f"t{tid}"
    return t


def _bridge(obj, kind):
    b = WavesBridge.__new__(WavesBridge)
    b._logged_in = True
    b._browse_pages = {}
    b._browse_loading = set()
    b._browse_gen = 0
    b._browse_lock = Lock()
    b._evict_lock = Lock()
    b._objs = {"album": {}, "playlist": {}, "mix": {}, "track": {}, "video": {}}
    b._objs_lock = Lock()
    b._objs_max = 100
    b.recorded = []
    b._ownership = SimpleNamespace(record_members_replace=lambda cid, ids: b.recorded.append((cid, list(ids))))
    b.collectionMembershipChanged = _Signal()
    b.browsePageLoaded = _Signal()
    b.browsePagePrefetched = _Signal()
    b.threadpool = _InlinePool()
    b.tidal = SimpleNamespace(session=SimpleNamespace())
    b.busy_log = []
    b.status_log = []
    b._set_busy = lambda v: b.busy_log.append(v)
    b._set_status = lambda v: b.status_log.append(v)
    b._save_page_cache = lambda: None
    b._prefetch_lock = Lock()
    b._prefetch_key = None
    b._prefetch_claimed = False
    b._prefetch_unrecorded = set()
    b._item_fetch_ts = {}
    if obj is not None:
        b._objs[kind][str(obj.id)] = obj
    # The row builder is not under test: a thin stand-in that still reports
    # the 160 art a real _track_dict would, so the album override is visible.
    b._track_dict = lambda t: {
        "id": str(t.id),
        "kind": "track",
        "num": 1,
        "vol": 1,
        "art": backend._image(t, 160),
        "duration": "3:20",
        "duration_sec": t.duration,
    }
    return b


# The real one, captured before the autouse fixture below silences it, for
# the pin that needs the crumb it writes.
_REAL_DEVLOG_DONE = backend.devlog.done


@pytest.fixture(autouse=True)
def _quiet_devlog(monkeypatch):
    monkeypatch.setattr(backend.devlog, "clock", lambda: 0.0, raising=True)
    monkeypatch.setattr(backend.devlog, "done", lambda *a, **k: None, raising=True)
    monkeypatch.setattr(backend.devlog, "event", lambda *a, **k: None, raising=True)


def _page(b):
    assert len(b.browsePageLoaded.emits) == 1, b.browsePageLoaded.emits
    payload = b.browsePageLoaded.emits[0]
    assert payload["error"] is False
    return payload


# ----- the hero is the card's URL ---------------------------------------------


def test_playlist_hero_is_the_card_size_even_when_480_is_accepted():
    album = _Cover("album", 1, [])
    pl = _Cover("playlist", "p1", [_fake_track(11, album), _fake_track(12, album)])
    b = _bridge(pl, "playlist")
    b.openBrowseItem("playlist", "p1")
    page = _page(b)
    assert page["header"]["art"] == pl.image(320), "the hero must ask for the size the playlist card fetched"
    assert page["header"]["art"].endswith("/320x320.jpg")


def test_album_hero_and_every_row_reuse_the_album_card_cover():
    album = _Cover("album", 42, [])
    album._tracks = [_fake_track(1, album), _fake_track(2, album), _fake_track(3, album)]
    b = _bridge(album, "album")
    b.openBrowseItem("album", "42")
    page = _page(b)
    card_url = album.image(320)
    assert page["header"]["art"] == card_url
    rows = [it for sec in page["sections"] for it in sec["items"]]
    assert len(rows) == 3
    assert all(it["art"] == card_url for it in rows), "album rows must not fetch a fresh 160 of the same cover"


def test_playlist_rows_keep_their_own_album_art():
    a1, a2 = _Cover("album", 1, []), _Cover("album", 2, [])
    pl = _Cover("playlist", "p2", [_fake_track(11, a1), _fake_track(12, a2)])
    b = _bridge(pl, "playlist")
    b.openBrowseItem("playlist", "p2")
    page = _page(b)
    rows = [it for sec in page["sections"] for it in sec["items"]]
    assert [it["art"] for it in rows] == [a1.image(160), a2.image(160)]


# ----- the hover prefetch ------------------------------------------------------


class _HeldPool:
    """Captures workers instead of running them, so a test can interleave a
    click with a prefetch still in flight and then run the worker by hand."""

    def __init__(self):
        self.workers = []

    def start(self, worker, priority: int = 0) -> None:
        self.workers.append(worker)


def _prefetch_bridge(payload=None, fail=False):
    b = _bridge(None, "playlist")
    b.threadpool = _HeldPool()
    b.built = []

    def build(kind, media_id, key, *, record=True):
        b.built.append(key)
        if fail:
            raise RuntimeError("wire down")
        page = dict(payload or _payload(key))
        if record:
            WavesBridge._record_page_members(b, page)
        return page

    b._build_browse_item = build
    return b


def _payload(key, rows=3):
    items = [{"id": f"t{i}", "art": f"https://img.test/row{i}/160x160.jpg"} for i in range(rows)]
    return {
        "key": key,
        "title": "Night Drive",
        "header": {"id": "p1", "art": "https://img.test/hero/320x320.jpg"},
        "sections": [{"rowKind": "tracks", "title": "Tracks", "items": items}],
        "error": False,
    }


def test_prefetch_of_a_cached_page_only_warms_its_covers():
    b = _prefetch_bridge()
    b._browse_pages["item:playlist:p1"] = _payload("item:playlist:p1")
    b.prefetchBrowseItem("playlist", "p1")
    assert b.built == [] and b.threadpool.workers == []
    assert b.browsePageLoaded.emits == []
    assert b.browsePagePrefetched.emits == [
        {
            "key": "item:playlist:p1",
            "art": "https://img.test/hero/320x320.jpg",
            "rowArts": [f"https://img.test/row{i}/160x160.jpg" for i in range(3)],
        }
    ]


def test_prefetch_is_a_no_op_while_a_click_on_it_is_running():
    b = _prefetch_bridge()
    b._browse_loading.add("item:playlist:p1")
    b.prefetchBrowseItem("playlist", "p1")
    assert b.threadpool.workers == [] and b._prefetch_key is None


def test_prefetch_rejects_signed_out_and_unknown_kinds():
    b = _prefetch_bridge()
    b.prefetchBrowseItem("artist", "a1")
    b._logged_in = False
    b.prefetchBrowseItem("playlist", "p1")
    assert b.threadpool.workers == [] and b._browse_loading == set()


def test_one_prefetch_in_flight_a_second_hover_is_dropped_not_queued():
    b = _prefetch_bridge()
    b.prefetchBrowseItem("playlist", "p1")
    b.prefetchBrowseItem("album", "42")  # dropped: p1 is still running
    assert len(b.threadpool.workers) == 1
    assert b._prefetch_key == "item:playlist:p1" and "item:album:42" not in b._browse_loading
    b.threadpool.workers[0].run()
    assert b._prefetch_key is None and b._browse_loading == set()
    b.prefetchBrowseItem("album", "42")  # free again
    assert len(b.threadpool.workers) == 2 and b._prefetch_key == "item:album:42"


def test_prefetch_completion_is_silent_and_leaves_the_page_cached_and_fresh(monkeypatch):
    monkeypatch.setattr(backend.time, "monotonic", lambda: 1000.0)
    b = _prefetch_bridge()
    b.prefetchBrowseItem("playlist", "p1")
    b.threadpool.workers[0].run()
    assert b.built == ["item:playlist:p1"]
    assert b._browse_pages["item:playlist:p1"]["title"] == "Night Drive"
    assert b._item_fetch_ts == {"item:playlist:p1": 1000.0}
    assert b.browsePageLoaded.emits == [], "a page the user never opened is never sent"
    assert [e["key"] for e in b.browsePagePrefetched.emits] == ["item:playlist:p1"]
    assert b.busy_log == [] and b.status_log == [], "a hover must not touch the busy indicator or the status line"


def test_click_mid_flight_claims_the_prefetch_and_lands_as_the_open():
    b = _prefetch_bridge()
    b.prefetchBrowseItem("playlist", "p1")
    b.openBrowseItem("playlist", "p1")
    assert len(b.threadpool.workers) == 1, "the click must not start a second fetch"
    assert b.busy_log == [True] and b.status_log == ["Opening…"]
    assert b.browsePageLoaded.emits == []
    b.threadpool.workers[0].run()
    assert [e["key"] for e in b.browsePageLoaded.emits] == ["item:playlist:p1"]
    assert b.status_log[-1] == "Night Drive" and b.busy_log[-1] is False
    assert b._prefetch_key is None and b._prefetch_claimed is False


def test_failed_prefetch_stores_nothing_and_frees_the_slot():
    b = _prefetch_bridge(fail=True)
    b.prefetchBrowseItem("playlist", "p1")
    b.threadpool.workers[0].run()
    assert b._browse_pages == {} and b._item_fetch_ts == {}
    assert b.browsePageLoaded.emits == [] and b.browsePagePrefetched.emits == []
    assert b._prefetch_key is None and b._browse_loading == set()
    assert b.busy_log == [] and b.status_log == []


def test_failed_prefetch_that_was_claimed_still_answers_the_click():
    b = _prefetch_bridge(fail=True)
    b.prefetchBrowseItem("playlist", "p1")
    b.openBrowseItem("playlist", "p1")
    b.threadpool.workers[0].run()
    assert b.browsePageLoaded.emits[-1]["error"] is True
    assert b.status_log[-1] == "Could not open that item" and b.busy_log[-1] is False


def test_open_within_the_minute_serves_the_cache_without_a_revalidate(monkeypatch):
    now = {"t": 1000.0}
    monkeypatch.setattr(backend.time, "monotonic", lambda: now["t"])
    b = _prefetch_bridge()
    b.prefetchBrowseItem("playlist", "p1")
    b.threadpool.workers[0].run()
    now["t"] = 1030.0
    b.openBrowseItem("playlist", "p1")
    assert [e["key"] for e in b.browsePageLoaded.emits] == ["item:playlist:p1"]
    # The one worker this open starts is the membership record the hover
    # left for it, not a revalidate: nothing is built again.
    for w in b.threadpool.workers[1:]:
        w.run()
    assert b.built == ["item:playlist:p1"], "fresh from the hover: no revalidate round trip"
    now["t"] = 1000.0 + WavesBridge._ITEM_FRESH_S + 1
    b.openBrowseItem("playlist", "p1")
    for w in b.threadpool.workers[2:]:
        w.run()
    assert b.built == ["item:playlist:p1"] * 2, "past the window the open revalidates as it always did"
    assert b.busy_log == [], "a cached page never flips busy, before or after the window"


def test_a_page_restored_from_disk_has_no_stamp_and_revalidates(monkeypatch):
    b = _prefetch_bridge()
    b._browse_pages["item:playlist:p1"] = _payload("item:playlist:p1")
    b.openBrowseItem("playlist", "p1")
    assert len(b.browsePageLoaded.emits) == 1 and len(b.threadpool.workers) == 1


def test_a_page_restored_from_disk_revalidates_even_in_the_first_minute_of_uptime(monkeypatch):
    # monotonic starts near zero on some platforms. A missing stamp read as
    # 0.0 was 'fetched 30 s ago' on a launch right after boot, and the
    # disk-restored page was served without its revalidate.
    monkeypatch.setattr(backend.time, "monotonic", lambda: 30.0)
    b = _prefetch_bridge()
    b._browse_pages["item:playlist:p1"] = _payload("item:playlist:p1")
    b.openBrowseItem("playlist", "p1")
    assert len(b.threadpool.workers) == 1, "no stamp is never fresh"
    b.threadpool.workers[0].run()
    assert b.built == ["item:playlist:p1"]


def test_art_summary_dedupes_and_caps_at_a_screenful():
    rows = [{"id": str(i), "art": f"https://img.test/{i % 5}/160x160.jpg"} for i in range(40)]
    payload = {"key": "k", "header": {"art": "H"}, "sections": [{"items": rows}, {"items": rows}]}
    out = WavesBridge._page_art_summary(payload)
    assert out == {"key": "k", "art": "H", "rowArts": [f"https://img.test/{i}/160x160.jpg" for i in range(5)]}
    many = [{"id": str(i), "art": f"u{i}"} for i in range(40)]
    # A screenful of rows: the window shows ~12 and the row loader builds
    # a few more just below it.
    assert len(WavesBridge._page_art_summary({"key": "k", "sections": [{"items": many}]})["rowArts"]) == 16


# ----- the page is handed over before the disk snapshot -----------------------


def _ordered(b):
    """Record the order of what the worker does, so a snapshot that costs a
    whole-map re-serialize plus an fsync cannot creep back in front of the
    page it is snapshotting."""
    order: list[str] = []
    b._save_page_cache = lambda: order.append("save")
    b.browsePageLoaded.emit = lambda p: order.append("page")
    b.browsePagePrefetched.emit = lambda p: order.append("prefetched")
    b._set_busy = lambda v: order.append(f"busy:{v}")
    return order


def test_a_cold_open_delivers_the_page_and_drops_busy_before_saving():
    album = _Cover("album", 42, [])
    album._tracks = [_fake_track(1, album)]
    b = _bridge(album, "album")
    order = _ordered(b)
    b.openBrowseItem("album", "42")
    assert order == ["busy:True", "page", "busy:False", "save"], order


def test_a_revalidate_re_emits_before_saving():
    album = _Cover("album", 42, [])
    album._tracks = [_fake_track(1, album)]
    b = _bridge(album, "album")
    # A cached page with no stamp (the shape restored from disk): it is served
    # at once, then revalidated in the background.
    b._browse_pages["item:album:42"] = _payload("item:album:42")
    order = _ordered(b)
    b.openBrowseItem("album", "42")
    assert order == ["page", "page", "save"], order
    assert "busy:True" not in order, "a silent revalidate must not raise the spinner"


def test_a_claimed_prefetch_delivers_the_page_before_saving():
    b = _prefetch_bridge()
    b.prefetchBrowseItem("playlist", "p1")
    b.openBrowseItem("playlist", "p1")  # clicked mid-flight: claims the build
    order = _ordered(b)
    b.threadpool.workers[0].run()
    assert order == ["prefetched", "page", "busy:False", "save"], order


# ----- a hover records nothing; the open does ---------------------------------


def test_a_hover_alone_records_no_membership_and_wakes_no_card():
    b = _prefetch_bridge()
    b.prefetchBrowseItem("playlist", "p1")
    b.threadpool.workers[0].run()
    assert b.built == ["item:playlist:p1"]
    assert "item:playlist:p1" in b._browse_pages
    assert b.recorded == []
    assert b.collectionMembershipChanged.emits == []


def test_a_click_that_claims_the_hover_records_as_an_open_would():
    b = _prefetch_bridge()
    b.prefetchBrowseItem("playlist", "p1")
    b.openBrowseItem("playlist", "p1")
    b.threadpool.workers[0].run()
    assert b.recorded == [("p1", ["t0", "t1", "t2"])]
    assert b.collectionMembershipChanged.emits == ["p1"]
    # Recorded before the page was handed over, so the card's rollup the
    # page's arrival triggers already finds the members.
    assert len(b.browsePageLoaded.emits) == 1


def test_an_open_from_the_fresh_cache_records_the_hover_built_page_once(monkeypatch):
    b = _prefetch_bridge()
    b.prefetchBrowseItem("playlist", "p1")
    b.threadpool.workers[0].run()
    assert b.recorded == []
    monkeypatch.setattr(backend.time, "monotonic", lambda: b._item_fetch_ts["item:playlist:p1"] + 1.0)
    b.openBrowseItem("playlist", "p1")
    # Off the GUI thread: the record is a commit.
    assert len(b.threadpool.workers) == 2
    b.threadpool.workers[1].run()
    assert b.recorded == [("p1", ["t0", "t1", "t2"])]
    assert b.collectionMembershipChanged.emits == ["p1"]
    # A Back a moment later does not record it again.
    b.openBrowseItem("playlist", "p1")
    assert len(b.threadpool.workers) == 2


def test_a_failed_hover_leaves_nothing_to_record_later():
    b = _prefetch_bridge(fail=True)
    b.prefetchBrowseItem("playlist", "p1")
    b.threadpool.workers[0].run()
    assert b._prefetch_unrecorded == set()


# ----- a hover leaves no breadcrumb; an open that rode on it does -------------


def test_a_hover_leaves_no_info_crumb_and_a_claimed_hover_leaves_an_open(caplog, monkeypatch):
    # INFO feeds the 250-line breadcrumb ring a crash report is stitched
    # from. A hover is not a user action: at one INFO line per card rested
    # on, minutes of browsing evicted the sign-in and the failing download
    # from the trail. The open that claims a prefetch IS an action, and
    # leaves the crumb an open leaves.
    import logging

    monkeypatch.setattr(backend.devlog, "done", _REAL_DEVLOG_DONE, raising=True)
    b = _prefetch_bridge()
    with caplog.at_level(logging.DEBUG, logger="waves"):
        b.prefetchBrowseItem("playlist", "p1")
        b.threadpool.workers[0].run()
    info = [r.message for r in caplog.records if r.levelno >= logging.INFO]
    assert info == [], info
    assert any("prefetch" in r.message for r in caplog.records), "the verbose log still carries it"

    caplog.clear()
    b = _prefetch_bridge()
    with caplog.at_level(logging.DEBUG, logger="waves"):
        b.prefetchBrowseItem("playlist", "p1")
        b.openBrowseItem("playlist", "p1")
        b.threadpool.workers[0].run()
    info = [r.message for r in caplog.records if r.levelno >= logging.INFO]
    assert len(info) == 1 and "from hover" in info[0], info


def test_an_escape_after_the_build_still_frees_the_hover_slot():
    # The slot is the one-in-flight cap. Worker.run swallows whatever escapes
    # the body, so a raise between the build and the slot's own release used
    # to leave the key held, and every later hover was dropped until sign-out.
    b = _prefetch_bridge(payload={"key": "item:playlist:p1", "title": "x", "sections": None, "error": False})
    b.prefetchBrowseItem("playlist", "p1")
    b.threadpool.workers[0].run()  # any(...) over None escapes; Worker.run swallows it
    assert b._prefetch_key is None and "item:playlist:p1" not in b._browse_loading
    b.prefetchBrowseItem("playlist", "p2")
    assert b._prefetch_key == "item:playlist:p2", "the next hover is taken"
