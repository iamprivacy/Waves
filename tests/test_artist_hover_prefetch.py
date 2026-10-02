"""A pointer resting on an artist card has the page ready before the click.

prefetchArtist is the artist half of the hover family: silent, one in
flight, a second hover dropped, a click mid-flight claims the build, and a
page already cached under the current edition rule is left to the click.
"""

from __future__ import annotations

from threading import Lock
from types import SimpleNamespace

from waves.waves_ui.backend import WavesBridge


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


class _Stub:
    loadArtist = WavesBridge.loadArtist
    prefetchArtist = WavesBridge.prefetchArtist
    _start_artist_build = WavesBridge._start_artist_build

    _artist_art_summary = staticmethod(WavesBridge._artist_art_summary)

    def __init__(self, *, fail=False, collapse=False):
        self.threadpool = _Pool()
        self._logged_in = True
        self._collapse = collapse
        self._fail = fail
        self._artist_cache = {}
        self._artist_loading = set()
        self._artist_prefetch = None
        self._artist_prefetch_claimed = False
        self._prefetch_lock = Lock()
        self._browse_gen = 0
        self.artistLoaded = _Sig()
        self.artistLoadFailed = _Sig()
        self.artistPagePrefetched = _Sig()
        self.busy = []
        self.statuses = []
        self.saves = 0
        self.fetches = 0

    def _artist_page_collapses_editions(self):
        return self._collapse

    def _set_status(self, s):
        self.statuses.append(s)

    def _set_busy(self, b):
        self.busy.append(b)

    def _get_artist(self, artist_id):
        self.fetches += 1
        if self._fail:
            return None
        return SimpleNamespace(
            id=artist_id,
            name="Doomcrusher",
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
        return {"id": a.id}

    def _track_dict(self, t):
        return {"id": t}

    def _video_dict(self, v):
        return {"id": v}

    def _remember_artist_page(self, artist_id, payload):
        self._artist_cache[artist_id] = payload

    def _save_page_cache(self):
        self.saves += 1


def test_a_hover_builds_the_page_silently_and_leaves_it_cached():
    b = _Stub()
    b.prefetchArtist("7")
    assert len(b.threadpool.workers) == 1 and b._artist_prefetch == "7" and "7" in b._artist_loading
    b.threadpool.workers[0].run()
    assert b._artist_cache["7"]["name"] == "Doomcrusher" and b.saves == 1
    assert b.artistLoaded.emits == [], "a page the user never opened is never sent"
    assert b.busy == [] and b.statuses == [], "a hover touches neither busy nor the status line"
    assert b._artist_prefetch is None and b._artist_loading == set()


def test_the_click_after_a_finished_hover_paints_from_the_cache():
    b = _Stub()
    b.prefetchArtist("7")
    b.threadpool.workers[0].run()
    b.loadArtist("7")
    assert b.artistLoaded.emits[0][0]["name"] == "Doomcrusher", "instant, from the cache"
    assert b.statuses == ["Doomcrusher"] and b.busy == [], "no loading state: only the revalidate runs"
    assert len(b.threadpool.workers) == 2


def test_a_click_mid_flight_claims_the_hover_build_and_lands_as_the_click():
    b = _Stub()
    b.prefetchArtist("7")
    b.loadArtist("7")
    assert len(b.threadpool.workers) == 1, "the click must not start a second build"
    assert b.busy == [True] and b.statuses == ["Loading artist…"]
    assert b._artist_prefetch_claimed is True
    b.threadpool.workers[0].run()
    assert [e[0]["name"] for e in b.artistLoaded.emits] == ["Doomcrusher"]
    assert b.statuses[-1] == "Doomcrusher" and b.busy[-1] is False
    assert b._artist_prefetch is None and b._artist_prefetch_claimed is False


def test_a_second_hover_while_one_runs_is_dropped_not_queued():
    b = _Stub()
    b.prefetchArtist("7")
    b.prefetchArtist("8")
    assert len(b.threadpool.workers) == 1 and "8" not in b._artist_loading
    b.threadpool.workers[0].run()
    b.prefetchArtist("8")
    assert len(b.threadpool.workers) == 2 and b._artist_prefetch == "8"


def test_a_hover_on_a_cached_page_builds_nothing_but_the_other_edition_rule_rebuilds():
    b = _Stub()
    b._artist_cache["7"] = {"id": "7", "name": "cached", "editions_collapsed": False, "art": "photo"}
    b.prefetchArtist("7")
    assert b.threadpool.workers == []
    b._collapse = True  # the page on disk was built under the other rule
    b.prefetchArtist("7")
    assert len(b.threadpool.workers) == 1


def test_a_hover_on_a_cached_page_warms_its_opening_covers():
    """Nothing to build, but the click that follows paints its covers from the
    warm pool: the hover sends the photo and each section's first covers, in
    page order, and never the page itself."""
    b = _Stub()
    b._artist_cache["7"] = {
        "id": "7",
        "name": "cached",
        "editions_collapsed": False,
        "art": "photo",
        "tracks": [{"art": f"t{i}"} for i in range(8)],
        "albums": [{"art": "a0"}, {"art": "a0"}, {"art": ""}, {"art": "a1"}],
        "eps": [],
        "videos": [{"art": "v0", "art_big": "V0"}],
    }
    b.prefetchArtist("7")
    assert b.artistPagePrefetched.emits == [
        (
            {
                "id": "7",
                "art": "photo",
                "tracks": ["t0", "t1", "t2", "t3", "t4"],
                "albums": ["a0", "a1"],
                "eps": [],
            },
        )
    ], "the opening screen's covers, deduped, five a section, videos left to the page"
    assert b.artistLoaded.emits == [] and b.busy == [] and b.statuses == []


def test_a_fresh_build_sends_its_covers_before_the_page():
    """A click on an artist not yet cached: the opening covers go out as soon
    as the sections are in, ahead of the edition compare and the page, so they
    download while the page is still being put together."""
    b = _Stub(collapse=True)
    order = []
    b.artistPagePrefetched.emit = lambda *a: order.append(("covers", a[0]["id"]))
    b._hide_subset_editions = lambda albums, eps: (order.append(("compare", None)), (albums, eps))[1]
    b.artistLoaded.emit = lambda *a: order.append(("page", a[0]["id"]))
    b.loadArtist("7")
    b.threadpool.workers[0].run()
    assert order == [("covers", "7"), ("compare", None), ("page", "7")]


def test_a_revalidate_sends_no_covers():
    b = _Stub()
    b._artist_cache["7"] = {"id": "7", "name": "cached", "editions_collapsed": False}
    b.loadArtist("7")  # served from the cache, then revalidated on the pool
    b.threadpool.workers[0].run()
    assert b.artistPagePrefetched.emits == [], "the page on screen already has its covers"


def test_a_hover_while_a_click_is_loading_that_artist_is_a_no_op():
    b = _Stub()
    b.loadArtist("7")
    b.prefetchArtist("7")
    assert len(b.threadpool.workers) == 1 and b._artist_prefetch is None


def test_a_failed_hover_stores_nothing_says_nothing_and_frees_the_slot():
    b = _Stub(fail=True)
    b.prefetchArtist("7")
    b.threadpool.workers[0].run()
    assert b._artist_cache == {} and b.artistLoaded.emits == [] and b.artistLoadFailed.emits == []
    assert b.busy == [] and b.statuses == []
    assert b._artist_prefetch is None and b._artist_loading == set()


def test_a_failed_hover_that_was_claimed_still_answers_the_click():
    b = _Stub(fail=True)
    b.prefetchArtist("7")
    b.loadArtist("7")
    b.threadpool.workers[0].run()
    assert b.statuses[-1] == "Could not load artist" and b.busy[-1] is False
    assert b.artistLoadFailed.emits == [("7",)]


def test_signed_out_or_blank_hovers_do_nothing():
    b = _Stub()
    b.prefetchArtist("")
    b._logged_in = False
    b.prefetchArtist("7")
    assert b.threadpool.workers == [] and b._artist_loading == set()


def test_a_plain_click_still_loads_and_lands_as_before():
    b = _Stub()
    b.loadArtist("7")
    assert b.busy == [True] and b.statuses == ["Loading artist…"]
    b.threadpool.workers[0].run()
    assert b.artistLoaded.emits[0][0]["name"] == "Doomcrusher"
    assert b.statuses[-1] == "Doomcrusher" and b.busy[-1] is False and b._artist_loading == set()


def test_the_five_section_reads_are_in_flight_together():
    """Bio, albums, EPs, top tracks and videos are independent requests about
    one artist. Read one after another they cost a round trip each (~1.35 s
    on a big artist's first open); together, about one. Each fake read here
    waits at a five-party barrier, so a serial walk breaks the barrier and the
    page comes back gutted instead of whole."""
    import threading
    import time

    from waves.waves_ui import backend

    gate = threading.Barrier(5, timeout=2.0)

    def read(value):
        def call(*_a, **_k):
            gate.wait()
            return value

        return call

    b = _Stub()
    b._get_artist = lambda artist_id: SimpleNamespace(
        id=artist_id,
        name="Doomcrusher",
        get_bio=read("bio"),
        get_albums=read([SimpleNamespace(id="al1")]),
        get_ep_singles=read([SimpleNamespace(id="ep1")]),
        get_top_tracks=read(["tr1"]),
        get_videos=read(["v1"]),
    )
    b.loadArtist("7")
    t0 = time.monotonic()
    b.threadpool.workers[0].run()
    took = time.monotonic() - t0
    page = b.artistLoaded.emits[0][0]
    assert not gate.broken, "the section reads ran one after another"
    assert page["bio"] == "bio" and page["albums"] == [{"id": "al1"}] and page["eps"] == [{"id": "ep1"}]
    assert page["tracks"] == [{"id": "tr1"}] and page["videos"] == [{"id": "v1"}]
    assert "7" in b._artist_cache, "a whole page is cached"
    assert took < 1.5, f"the build took {took:.2f}s"
    assert backend.ARTIST_GAUGE.activeThreadCount() == 0, "the gauge leaked a count"
    assert backend.ARTIST_GAUGE.peak >= 5


def test_the_section_fan_out_reports_to_diagnostics():
    from pathlib import Path

    src = (Path(__file__).resolve().parent.parent / "waves" / "waves_ui" / "backend.py").read_text(encoding="utf-8")
    assert 'diagnostics.register_pool("artist", ARTIST_GAUGE)' in src


def test_a_failed_section_still_marks_the_page_suspect():
    """One section failing must keep the page out of the cache (a 429 on the
    albums alone would otherwise persist a gutted page), while a failed bio
    alone costs nothing."""

    def boom(*_a, **_k):
        raise RuntimeError("429")

    for failing, cached in (("get_albums", False), ("get_videos", False), ("get_bio", True)):
        b = _Stub()
        fields = {
            "id": "7",
            "name": "Doomcrusher",
            "get_bio": lambda: "bio",
            "get_albums": lambda: [SimpleNamespace(id="al1")],
            "get_ep_singles": lambda: [],
            "get_top_tracks": lambda limit=10: [],
            "get_videos": lambda limit=0: [],
        }
        fields[failing] = boom
        b._get_artist = lambda artist_id, f=fields: SimpleNamespace(**f)
        b.loadArtist("7")
        b.threadpool.workers[0].run()
        assert b.artistLoaded.emits, f"{failing} failing hid the page entirely"
        assert ("7" in b._artist_cache) is cached, f"{failing} failing: cached={not cached}"


def test_a_first_open_lands_before_the_cache_is_written():
    """The snapshot re-serializes every cached page and fsyncs: the page the
    user is waiting on goes out first, as the browse workers already do."""
    b = _Stub()
    order = []
    b.artistLoaded.emit = lambda *a: order.append("page")
    b._save_page_cache = lambda: order.append("save")
    b.loadArtist("7")
    b.threadpool.workers[0].run()
    assert order == ["page", "save"]
