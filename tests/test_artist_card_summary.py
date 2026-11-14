"""The artist card's bridge side: a summary read off the page cache and a
request path that builds the page beside the hover prefetch.

A card is opened by a pointer resting on an artist's name. Its summary comes
from a built artist page (artistSummary, no I/O); a page not built yet is
built through the hover slot (requestArtistCard), and a request that finds
the slot busy is parked rather than dropped, newest wins, so moving from name
to name never leaves the card on its skeleton.
"""

from __future__ import annotations

from threading import Lock
from types import SimpleNamespace

from waves.waves_ui import backend as backend_mod
from waves.waves_ui.backend import WavesBridge, _card_bio


class _Sig:
    def __init__(self):
        self.emits = []

    def emit(self, *a):
        self.emits.append(a)


class _Pool:
    def __init__(self):
        self.workers = []

    def start(self, w):
        self.workers.append(w)

    def run_all(self):
        while self.workers:
            self.workers.pop(0).run()


class _Stub:
    loadArtist = WavesBridge.loadArtist
    prefetchArtist = WavesBridge.prefetchArtist
    artistSummary = WavesBridge.artistSummary
    requestArtistCard = WavesBridge.requestArtistCard
    _artist_card_request = WavesBridge._artist_card_request
    _artist_card_landed = WavesBridge._artist_card_landed
    _artist_card_pop_fetch = WavesBridge._artist_card_pop_fetch
    _artist_card_page = WavesBridge._artist_card_page
    _artist_card_early_land = WavesBridge._artist_card_early_land
    _artist_card_early_drop = WavesBridge._artist_card_early_drop
    _start_artist_build = WavesBridge._start_artist_build
    _pop_cached = WavesBridge._pop_cached
    _remember_capped = WavesBridge._remember_capped
    _ARTIST_POP_TTL = WavesBridge._ARTIST_POP_TTL
    _ARTIST_POP_MAX = WavesBridge._ARTIST_POP_MAX

    _artist_art_summary = staticmethod(WavesBridge._artist_art_summary)
    _artist_summary_of = staticmethod(WavesBridge._artist_summary_of)

    def __init__(self, *, fail=(), collapse=False):
        self.threadpool = _Pool()
        self._logged_in = True
        self._collapse = collapse
        self._fail = set(fail)
        self._artist_cache = {}
        self._artist_loading = set()
        self._artist_prefetch = None
        self._artist_prefetch_claimed = False
        self._artist_card_want = None
        self._artist_pop_inflight = set()
        self._artist_card_early = {}
        self._artist_pop_cache = {}
        self._prefetch_lock = Lock()
        self._evict_lock = Lock()
        self._browse_gen = 0
        self.artistLoaded = _Sig()
        self.artistLoadFailed = _Sig()
        self.artistPagePrefetched = _Sig()
        self.artistSummaryReady = _Sig()
        self.artistMetaLoaded = _Sig()
        self.busy = []
        self.statuses = []
        self.fetched = []
        self.tidal = SimpleNamespace(session=SimpleNamespace())

    def _artist_page_collapses_editions(self):
        return self._collapse

    def _set_status(self, s):
        self.statuses.append(s)

    def _set_busy(self, b):
        self.busy.append(b)

    def _get_artist(self, artist_id):
        self.fetched.append(artist_id)
        if artist_id in self._fail:
            return None
        return SimpleNamespace(
            id=artist_id,
            name=f"Artist {artist_id}",
            get_bio=lambda: "",
            get_albums=lambda: [SimpleNamespace(id="al1")],
            get_ep_singles=lambda: [],
            get_top_tracks=lambda limit=10: [],
            get_videos=lambda limit=0: [],
        )

    def _dedup_albums(self, a):
        return a

    def _dedup_tracks(self, t):
        return t

    def _dedup_videos(self, v):
        return v

    def _hide_subset_editions(self, albums, eps):
        return albums, eps

    def _album_dict(self, a):
        return {"id": a.id, "title": "Record", "rtype": "album", "year": "2020"}

    def _track_dict(self, t):
        return {"id": t}

    def _video_dict(self, v):
        return {"id": v}

    def _remember_artist_page(self, artist_id, payload):
        self._artist_cache[artist_id] = payload

    def _save_page_cache(self):
        pass


def _ready(b):
    return [(aid, s.get("known"), s.get("failed", False)) for aid, s in b.artistSummaryReady.emits]


def _page(**over):
    page = {
        "id": "42",
        "name": "Mara Sol",
        "art": "pic",
        "editions_collapsed": False,
        "bio": "First <b>line</b><br/>still first.<br/><br/>Second &amp; last.",
        "albums": [
            {"id": "a1", "title": "Old", "year": "2015", "date": "2015-03-01", "art": "c1", "rtype": "album"},
            {"id": "a2", "title": "New", "year": "2023", "date": "2023-05-01", "art": "c2", "rtype": "album"},
        ],
        "eps": [
            {"id": "e1", "title": "Mid EP", "year": "2019", "date": "2019-01-01", "art": "c3", "rtype": "ep"},
            {"id": "s1", "title": "Single", "year": "2024", "date": "2024-01-01", "art": "c4", "rtype": "single"},
            {"id": "s2", "title": "Old row", "year": "2016", "tracks": 2},
            {"id": "e2", "title": "Old EP row", "year": "2017", "tracks": 5},
        ],
        "tracks": [
            {
                "id": str(n),
                "title": f"Song {n}",
                "duration": "3:0" + str(n),
                "duration_sec": 180 + n,
                "album": "New",
                "album_id": "a2",
                "art": f"t{n}",
            }
            for n in range(1, 6)
        ],
    }
    page.update(over)
    return page


# ----- the summary shape -----


def test_counts_go_by_release_shape_not_by_list():
    s = WavesBridge._artist_summary_of(_page(), 57)
    # "eps" holds EPs and singles together; an older row without a shape is
    # judged by its track count.
    assert (s["albums"], s["eps"], s["singles"]) == (2, 2, 2)
    assert s["known"] is True and s["popularity"] == 57
    assert (s["id"], s["name"], s["art"]) == ("42", "Mara Sol", "pic")


def test_the_strip_is_albums_and_eps_newest_first_without_singles():
    s = WavesBridge._artist_summary_of(_page())
    assert [r["id"] for r in s["releases"]] == ["a2", "e1", "e2", "a1"]
    assert [r["rtype"] for r in s["releases"]] == ["album", "ep", "ep", "album"]
    assert s["releases"][0] == {"id": "a2", "title": "New", "year": "2023", "art": "c2", "rtype": "album"}


def test_an_artist_with_only_singles_still_gets_a_strip():
    page = _page(albums=[], eps=[{"id": f"s{n}", "year": str(2010 + n), "rtype": "single"} for n in range(8)])
    s = WavesBridge._artist_summary_of(page)
    assert s["singles"] == 8 and s["albums"] == 0 and s["eps"] == 0
    assert [r["id"] for r in s["releases"]] == ["s7", "s6", "s5", "s4", "s3", "s2"]


def test_the_top_tracks_are_the_first_three_with_their_album():
    s = WavesBridge._artist_summary_of(_page())
    assert [t["id"] for t in s["top"]] == ["1", "2", "3"]
    assert s["top"][0] == {
        "id": "1",
        "title": "Song 1",
        "duration": "3:01",
        "duration_sec": 181,
        "album": "New",
        "album_id": "a2",
        "art": "t1",
    }


def test_the_bio_comes_out_as_plain_paragraphs():
    s = WavesBridge._artist_summary_of(_page())
    assert s["bio"] == "First line still first.\n\nSecond & last."
    assert _card_bio("") == "" and _card_bio("  one   two  ") == "one two"


# ----- the synchronous read -----


def test_a_cached_page_reads_as_known_with_the_memoized_popularity():
    b = _Stub()
    b._artist_cache["42"] = _page()
    b._artist_pop_cache["42"] = (backend_mod.time.monotonic(), 64)
    s = b.artistSummary("42")
    assert s["known"] is True and s["popularity"] == 64 and s["albums"] == 2


def test_an_unknown_artist_or_another_edition_rule_reads_as_not_known():
    b = _Stub(collapse=True)
    b._artist_cache["42"] = _page()  # built with editions_collapsed False
    assert b.artistSummary("42") == {"id": "42", "known": False}
    assert b.artistSummary("7") == {"id": "7", "known": False}
    assert b.threadpool.workers == [], "the read never fetches"


# ----- the request path -----


def test_a_cached_request_answers_at_once_and_builds_nothing():
    b = _Stub()
    b._artist_cache["42"] = _page()
    b._artist_pop_cache["42"] = (backend_mod.time.monotonic(), 50)
    b.requestArtistCard("42")
    assert _ready(b) == [("42", True, False)]
    assert b.threadpool.workers == [] and b.fetched == []


def test_a_cold_request_builds_silently_and_answers_when_it_lands():
    b = _Stub()
    b._artist_pop_cache["9"] = (backend_mod.time.monotonic(), 50)  # no popularity fetch
    b.requestArtistCard("9")
    assert b._artist_prefetch == "9" and len(b.threadpool.workers) == 1
    b.threadpool.run_all()
    assert _ready(b) == [("9", True, False)]
    assert b.artistSummaryReady.emits[0][1]["name"] == "Artist 9"
    assert b.artistLoaded.emits == [] and b.busy == [] and b.statuses == []
    assert b._artist_prefetch is None and "9" in b._artist_cache


def test_the_card_fills_before_the_videos_land_and_only_once():
    """The card shows no videos: it fills as soon as the rest of the page is
    in, and a card that opens before the page is cached reads it as known."""
    import threading

    b = _Stub()
    b._artist_pop_cache["9"] = (backend_mod.time.monotonic(), 50)
    go = threading.Event()
    seen = {}

    class _Hooked(_Sig):
        def emit(self, *a):
            super().emit(*a)
            # A second card opening right now, before the cache holds the page.
            seen["read"] = b.artistSummary("9").get("known")
            seen["cached"] = "9" in b._artist_cache
            go.set()

    b.artistSummaryReady = _Hooked()
    real = b._get_artist

    def get_artist(aid):
        a = real(aid)
        # The videos wait for the card to fill (up to 5 s, so a regression fails
        # instead of hanging).
        a.get_videos = lambda limit=0: (seen.setdefault("filled_first", go.wait(5)), [])[1]
        return a

    b._get_artist = get_artist
    b.requestArtistCard("9")
    b.threadpool.run_all()
    assert seen.get("filled_first") is True, "the card waited on the videos"
    assert seen["read"] is True and seen["cached"] is False
    assert _ready(b) == [("9", True, False)], "the card is filled once, not again when the build ends"
    assert "9" in b._artist_cache and b._artist_card_early == {}, "the page cache takes over"


def test_a_page_with_failed_videos_still_fills_the_card_and_is_not_kept():
    b = _Stub()
    b._artist_pop_cache["9"] = (backend_mod.time.monotonic(), 50)
    real = b._get_artist

    def get_artist(aid):
        a = real(aid)

        def boom(limit=0):
            raise RuntimeError("videos down")

        a.get_videos = boom
        return a

    b._get_artist = get_artist
    b.requestArtistCard("9")
    b.threadpool.run_all()
    assert _ready(b) == [("9", True, False)]
    assert "9" not in b._artist_cache and b._artist_card_early == {}, "a gutted page is neither cached nor held"


def test_the_same_artist_mid_build_rides_on_it():
    b = _Stub()
    b._artist_pop_cache["9"] = (backend_mod.time.monotonic(), 50)
    b.requestArtistCard("9")
    b.requestArtistCard("9")
    assert len(b.threadpool.workers) == 1
    b.threadpool.run_all()
    assert _ready(b) == [("9", True, False)]


def test_a_busy_slot_parks_the_newest_request_and_builds_it_next():
    b = _Stub()
    now = backend_mod.time.monotonic()
    for aid in ("1", "2", "3"):
        b._artist_pop_cache[aid] = (now, 50)
    b.requestArtistCard("1")
    b.requestArtistCard("2")
    b.requestArtistCard("3")
    assert len(b.threadpool.workers) == 1 and b._artist_card_want == "3"
    b.threadpool.run_all()
    assert b.fetched == ["1", "3"], "the skipped middle name is never built"
    assert _ready(b) == [("1", True, False), ("3", True, False)]
    assert b._artist_card_want is None and b._artist_prefetch is None


def test_a_hover_prefetch_holding_the_slot_still_hands_over_to_the_card():
    b = _Stub()
    b._artist_pop_cache["2"] = (backend_mod.time.monotonic(), 50)
    b.prefetchArtist("1")
    b.requestArtistCard("2")
    b.threadpool.run_all()
    assert b.fetched == ["1", "2"]
    assert ("2", True, False) in _ready(b)


def test_moving_back_to_the_building_artist_drops_the_parked_one():
    b = _Stub()
    now = backend_mod.time.monotonic()
    for aid in ("1", "2"):
        b._artist_pop_cache[aid] = (now, 50)
    b.requestArtistCard("1")
    b.requestArtistCard("2")
    b.requestArtistCard("1")
    b.threadpool.run_all()
    assert b.fetched == ["1"]


def test_a_click_mid_build_claims_the_card_build():
    b = _Stub()
    b._artist_pop_cache["9"] = (backend_mod.time.monotonic(), 50)
    b.requestArtistCard("9")
    b.loadArtist("9")  # OPEN ARTIST PAGE while the card's build runs
    assert len(b.threadpool.workers) == 1
    b.threadpool.run_all()
    assert len(b.artistLoaded.emits) == 1 and b.busy == [True, False]
    assert _ready(b) == [("9", True, False)]


def test_a_failed_build_says_so_and_still_builds_the_parked_one():
    b = _Stub(fail={"1"})
    now = backend_mod.time.monotonic()
    for aid in ("1", "2"):
        b._artist_pop_cache[aid] = (now, 50)
    b.requestArtistCard("1")
    b.requestArtistCard("2")
    b.threadpool.run_all()
    assert _ready(b) == [("1", False, True), ("2", True, False)]
    assert b.statuses == [] and b.artistLoadFailed.emits == [], "a card build stays silent"


def test_signed_out_a_request_does_nothing():
    b = _Stub()
    b._logged_in = False
    b.requestArtistCard("9")
    assert b.threadpool.workers == [] and b.artistSummaryReady.emits == []


def test_a_non_catalog_id_is_not_requested():
    b = _Stub()
    b.requestArtistCard("")
    b.requestArtistCard("not-an-id")
    assert b.threadpool.workers == []


def test_a_logout_mid_build_emits_nothing_and_builds_nothing_parked():
    b = _Stub()
    now = backend_mod.time.monotonic()
    for aid in ("1", "2"):
        b._artist_pop_cache[aid] = (now, 50)
    b.requestArtistCard("1")
    b.requestArtistCard("2")
    b._browse_gen += 1  # signed out while "1" was building
    b.threadpool.run_all()
    assert b.artistSummaryReady.emits == [] and b.fetched == ["1"]


# ----- popularity -----


def _pop_stub(monkeypatch, value):
    b = _Stub()
    b._artist_cache["42"] = _page()
    asked = []

    def by_id(session, artist_id):
        asked.append(artist_id)
        return value

    monkeypatch.setattr(backend_mod, "_artist_popularity_by_id", by_id)
    return b, asked


def test_popularity_without_a_memo_is_asked_once_and_memoized(monkeypatch):
    b, asked = _pop_stub(monkeypatch, 71)
    b.requestArtistCard("42")
    b.requestArtistCard("42")  # second open while the first ask is out
    assert len(b.threadpool.workers) == 1
    b.threadpool.run_all()
    assert asked == ["42"] and b.artistMetaLoaded.emits == [("42", 71)]
    assert b._pop_cached("42") == 71
    b.requestArtistCard("42")
    assert b.threadpool.workers == [], "a memo hit asks nothing"


def test_a_popularity_miss_is_not_sent(monkeypatch):
    b, asked = _pop_stub(monkeypatch, -1)
    b.requestArtistCard("42")
    b.threadpool.run_all()
    assert asked == ["42"] and b.artistMetaLoaded.emits == []
    assert b._artist_pop_inflight == set()


def _logout(b):
    """What the bridge's logout does to the card's state, in its order: the
    generation first, then the slot, the parked card and the marks."""
    b._browse_gen += 1
    with b._prefetch_lock:
        b._artist_prefetch = None
        b._artist_prefetch_claimed = False
        b._artist_card_want = None
        b._artist_pop_inflight.clear()
    b._artist_loading.clear()


def test_a_build_from_before_a_logout_leaves_the_new_accounts_slot_alone():
    b = _Stub()
    now = backend_mod.time.monotonic()
    for aid in ("1", "2"):
        b._artist_pop_cache[aid] = (now, 50)
    b.requestArtistCard("1")
    old = b.threadpool.workers.pop(0)  # still running when the account signs out
    _logout(b)
    b.requestArtistCard("1")  # the new account's card on the same artist
    b.requestArtistCard("2")  # parked behind it
    assert b._artist_card_want == "2"
    old.run()  # the old build lands first
    assert b._artist_prefetch == "1" and "1" in b._artist_loading, "the new build keeps its slot"
    assert b._artist_card_want == "2", "the parked card is not thrown away"
    b.threadpool.run_all()
    assert ("2", True, False) in _ready(b)
    assert b._artist_prefetch is None and b._artist_card_want is None


def test_a_popularity_ask_from_before_a_logout_keeps_the_new_mark(monkeypatch):
    b, asked = _pop_stub(monkeypatch, 71)
    b.requestArtistCard("42")
    old = b.threadpool.workers.pop(0)
    _logout(b)
    b.requestArtistCard("42")  # the new account asks for the same artist
    assert b._artist_pop_inflight == {"42"}
    old.run()
    assert b._artist_pop_inflight == {"42"}, "the old ask does not clear the new one's mark"
    b.requestArtistCard("42")
    assert len(b.threadpool.workers) == 1, "no duplicate ask while the new one is out"
    assert b.artistMetaLoaded.emits == []


def test_a_revalidate_with_news_updates_the_open_page_and_saves_it():
    """Audit 2026-11-14b: the early card fill once paired the revalidate's
    early return with itself, so every refresh returned before the in-place
    update and the disk save, and an open page never showed a new release."""
    b = _Stub()
    saved = []
    b._save_page_cache = lambda: saved.append(True)
    stale = {"id": "9", "name": "Artist 9", "albums": [], "eps": [], "tracks": [], "videos": []}
    b._artist_cache["9"] = stale
    b._artist_loading.add("9")
    b._start_artist_build("9", stale, False, silent=False)
    b.threadpool.run_all()
    refreshes = [p for (p,) in b.artistLoaded.emits if p.get("refresh")]
    assert refreshes and refreshes[0]["albums"], "the open page never got the new release"
    assert saved, "the revalidated page never reached the disk snapshot"
    assert b._artist_cache["9"]["albums"], "the page cache keeps the new release"


def test_a_revalidate_with_nothing_new_ends_quietly():
    b = _Stub()
    saved = []
    b._save_page_cache = lambda: saved.append(True)
    b._artist_loading.add("9")
    b._start_artist_build("9", None, False, silent=False)
    b.threadpool.run_all()
    current = b._artist_cache["9"]
    b.artistLoaded.emits.clear()
    saved.clear()
    b._artist_loading.add("9")
    b._start_artist_build("9", current, False, silent=False)
    b.threadpool.run_all()
    assert b.artistLoaded.emits == [] and saved == []


def test_a_build_from_before_a_logout_holds_no_page_for_the_next_account():
    """The early fill is checked against the account generation under the
    same lock logout clears the held pages in: a build that read the
    generation before a logout, and lands after it, holds nothing."""
    b = _Stub()
    before = b._browse_gen
    with b._prefetch_lock:  # logout
        b._browse_gen += 1
        b._artist_card_early.clear()
    assert b._artist_card_early_land("9", {"id": "9", "albums": [{"id": "al1"}]}, before) is False
    assert b._artist_card_early == {} and b.artistSummaryReady.emits == []
    assert b._artist_card_page("9") is None, "the last account's page waits for the next one's card"
    assert b._artist_card_early_land("9", {"id": "9", "albums": [{"id": "al1"}]}, b._browse_gen) is True


def test_the_cover_warm_warms_the_cards_own_strip():
    """Audit 2026-11-14b: the card-size warm once took the first six albums
    and EPs in page order (singles counted), while the strip shows the newest
    non-single releases, so it decoded covers the card never shows."""
    albums = [{"id": f"a{i}", "art": f"art-a{i}", "rtype": "album", "date": f"20{10 + i}-01-01"} for i in range(6)]
    eps = [{"id": f"e{i}", "art": f"art-e{i}", "rtype": "ep", "date": f"202{i}-06-01"} for i in range(4)]
    eps += [{"id": f"s{i}", "art": f"art-s{i}", "rtype": "single", "date": "2026-01-01"} for i in range(3)]
    page = {"id": "9", "albums": albums, "eps": eps, "tracks": []}
    strip = [r["art"] for r in WavesBridge._artist_summary_of(page)["releases"]]
    warm = WavesBridge._artist_art_summary(page)
    assert warm["card"] == strip
    assert "art-s0" not in warm["card"] and warm["card"][0] == "art-e3"


def test_the_early_cover_warm_carries_the_strip_too():
    b = _Stub()
    b._artist_pop_cache["9"] = (backend_mod.time.monotonic(), 50)
    b.requestArtistCard("9")
    b.threadpool.run_all()
    (warm,) = b.artistPagePrefetched.emits[0]
    assert "card" in warm and len(warm["card"]) == 1
