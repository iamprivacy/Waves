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


def test_a_hover_on_a_cached_page_is_a_no_op_but_the_other_edition_rule_rebuilds():
    b = _Stub()
    b._artist_cache["7"] = {"name": "cached", "editions_collapsed": False}
    b.prefetchArtist("7")
    assert b.threadpool.workers == []
    b._collapse = True  # the page on disk was built under the other rule
    b.prefetchArtist("7")
    assert len(b.threadpool.workers) == 1


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
