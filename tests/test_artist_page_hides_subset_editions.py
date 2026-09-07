"""An artist page hides the editions the discography sweep would skip.

With 'Most-complete edition only' on, a 5-track cut whose songs all sit in
the 7-track cut beside it showed as a second row of the same album on the
artist page, while 'Download discography' quietly skipped it. The page now
runs the sweep's own track-aware collapse over both shelves, judged together,
behind a sub-setting that lists every edition again for anyone who wants it.
"""

from __future__ import annotations

from threading import Lock
from types import SimpleNamespace

from waves.waves_ui import backend
from waves.waves_ui.backend import WavesBridge


class _Album:
    def __init__(self, ident, name, tracks, quality="HI_RES_LOSSLESS", fail=False):
        self.id = ident
        self.name = name
        self.artist = SimpleNamespace(name="Doomcrusher", id="d1")
        self.artists = [self.artist]
        self.audio_quality = quality
        self.num_tracks = len(tracks)
        self._tracks = tracks
        self._fail = fail
        self.fetches = 0
        self.item_fetches = 0
        self.short_tracks = None  # what /tracks serves when TIDAL truncates it
        self.explicit_names: set = set()  # which RECORDINGS are the explicit cut

    def _rows(self, names):
        return [
            SimpleNamespace(name=t, duration=200 + i, explicit=t in self.explicit_names) for i, t in enumerate(names)
        ]

    def tracks(self):
        self.fetches += 1
        if self._fail:
            raise RuntimeError("429")
        return self._rows(self.short_tracks if self.short_tracks is not None else self._tracks)

    def items(self, limit=100, offset=0):
        self.item_fetches += 1
        return self._rows(self._tracks)[offset : offset + limit]


class _Stub:
    _artist_page_collapses_editions = WavesBridge._artist_page_collapses_editions
    _hide_subset_editions = WavesBridge._hide_subset_editions
    _merge_pref_on = WavesBridge._merge_pref_on
    _waves_pref_bool = WavesBridge._waves_pref_bool
    _remember_capped = WavesBridge._remember_capped
    _EDITION_TRACKS_CACHE_MAX = WavesBridge._EDITION_TRACKS_CACHE_MAX

    def __init__(self, **prefs):
        self._waves_prefs = {"collapse_editions": True, "edition_conflict": "merge", **prefs}
        self._edition_tracks_cache = {}
        self._evict_lock = Lock()


def _forsaker():
    short = _Album("a5", "Forsaker", ["One", "Two", "Three", "Four", "Five"])
    full = _Album("a7", "Forsaker", ["One", "Two", "Three", "Four", "Five", "Six", "Seven"])
    other = _Album("b1", "Ruin", ["X", "Y"])
    return short, full, other


def test_the_subset_edition_is_hidden_and_the_fuller_one_kept():
    short, full, other = _forsaker()
    albums, eps = _Stub()._hide_subset_editions([other], [short, full])
    assert [a.id for a in albums] == ["b1"]
    assert [a.id for a in eps] == ["a7"]
    # A singleton title never costs a track fetch.
    assert other.fetches == 0
    assert short.fetches == 1 and full.fetches == 1


def test_the_two_shelves_are_judged_together():
    short, full, other = _forsaker()
    albums, eps = _Stub()._hide_subset_editions([full], [short, other])
    assert [a.id for a in albums] == ["a7"]
    assert [a.id for a in eps] == ["b1"]


def test_a_fetch_failure_keeps_both():
    short, full, _ = _forsaker()
    short._fail = True
    albums, eps = _Stub()._hide_subset_editions([], [short, full])
    assert [a.id for a in eps] == ["a5", "a7"]


def test_a_different_recording_is_never_hidden():
    short, full, _ = _forsaker()
    short._tracks = ["One", "Two", "Three", "Four", "Another"]
    _, eps = _Stub()._hide_subset_editions([], [short, full])
    assert [a.id for a in eps] == ["a5", "a7"]


def test_the_sub_setting_and_the_master_switch_gate_it():
    assert _Stub()._artist_page_collapses_editions() is True
    assert _Stub(artist_page_all_editions=True)._artist_page_collapses_editions() is False
    assert _Stub(collapse_editions=False)._artist_page_collapses_editions() is False
    assert _Stub(collapse_editions=False, artist_page_all_editions=True)._artist_page_collapses_editions() is False


def test_the_setting_sits_under_its_master_in_the_schema():
    src = backend.__file__
    with open(src, encoding="utf-8") as fh:
        text = fh.read()
    assert '"artist_page_all_editions": False' in text
    i = text.index('"key": "artist_page_all_editions"')
    assert text.index('"key": "collapse_editions"') < i
    assert 'elif key == "artist_page_all_editions":' in text


def test_a_second_visit_costs_no_track_fetch():
    short, full, _ = _forsaker()
    stub = _Stub()
    stub._hide_subset_editions([], [short, full])
    _, eps = stub._hide_subset_editions([], [short, full])
    assert [a.id for a in eps] == ["a7"]
    assert short.fetches == 1 and full.fetches == 1


def test_a_failed_fetch_is_not_cached_so_the_next_visit_retries():
    short, full, _ = _forsaker()
    short._fail = True
    stub = _Stub()
    stub._hide_subset_editions([], [short, full])
    short._fail = False
    _, eps = stub._hide_subset_editions([], [short, full])
    assert [a.id for a in eps] == ["a7"]
    assert short.fetches == 2 and full.fetches == 1


def test_the_lower_quality_fuller_edition_keeps_both_under_keep_both():
    short, full, _ = _forsaker()
    full.audio_quality = "LOSSLESS"
    _, eps = _Stub(edition_conflict="keep_both")._hide_subset_editions([], [short, full])
    assert [a.id for a in eps] == ["a5", "a7"]
    _, eps = _Stub(edition_conflict="completeness")._hide_subset_editions([], [short, full])
    assert [a.id for a in eps] == ["a7"]


class _Pool:
    @staticmethod
    def start(worker, priority=0):
        worker.fn()


class _Sig:
    def __init__(self):
        self.emits = []

    def emit(self, *a):
        self.emits.append(a)


class _PageStub:
    """Enough of the bridge to drive loadArtist through the cache gate."""

    loadArtist = WavesBridge.loadArtist
    _start_artist_build = WavesBridge._start_artist_build

    def __init__(self, collapse, artist):
        self.threadpool = _Pool()
        self._collapse = collapse
        self._artist = artist
        self._artist_cache = {}
        self._artist_loading = set()
        self._artist_prefetch = None
        self._artist_prefetch_claimed = False
        self._prefetch_lock = Lock()
        self._browse_gen = 0
        self.artistLoaded = _Sig()
        self.artistLoadFailed = _Sig()
        self.statuses = []
        self.hidden_calls = 0

    def _artist_page_collapses_editions(self):
        return self._collapse

    def _set_status(self, s):
        self.statuses.append(s)

    def _set_busy(self, b):
        pass

    def _get_artist(self, artist_id):
        return self._artist

    def _dedup_albums(self, a):
        return a

    def _dedup_tracks(self, t):
        return t

    def _dedup_videos(self, v):
        return v

    def _hide_subset_editions(self, albums, eps):
        self.hidden_calls += 1
        return albums, [e for e in eps if e.id != "a5"]

    def _album_dict(self, a):
        return {"id": a.id}

    def _track_dict(self, t):
        return {"id": t}

    def _video_dict(self, v):
        return {"id": v}

    def _remember_artist_page(self, artist_id, payload):
        self._artist_cache[artist_id] = payload

    def _save_page_cache(self):
        pass


def _page_artist():
    short, full, _ = _forsaker()
    return SimpleNamespace(
        name="Doomcrusher",
        get_bio=lambda: "",
        get_albums=lambda: [],
        get_ep_singles=lambda: [short, full],
        get_top_tracks=lambda limit=10: [],
        get_videos=lambda limit=None: [],
    )


def test_a_page_cached_under_the_other_rule_is_not_shown_then_corrected():
    stub = _PageStub(False, _page_artist())
    stub.loadArtist("d1")
    assert [e["id"] for e in stub.artistLoaded.emits[-1][0]["eps"]] == ["a5", "a7"]

    # The rule flips on. The stale page must not flash both rows first.
    stub._collapse = True
    stub.artistLoaded.emits.clear()
    stub.loadArtist("d1")
    assert len(stub.artistLoaded.emits) == 1
    page = stub.artistLoaded.emits[0][0]
    assert "refresh" not in page
    assert [e["id"] for e in page["eps"]] == ["a7"]
    assert page["editions_collapsed"] is True
    assert "Scanning editions…" in stub.statuses

    # Now the cache matches the rule: served instantly, revalidated in place.
    stub.artistLoaded.emits.clear()
    stub.loadArtist("d1")
    assert [e["id"] for e in stub.artistLoaded.emits[0][0]["eps"]] == ["a7"]
    assert "refresh" not in stub.artistLoaded.emits[0][0]


def test_a_short_tracks_read_falls_back_to_the_items_listing():
    # Yellowcard, Better Days (Deluxe): /tracks served 3 of 14, /items all 14.
    short, full, _ = _forsaker()
    full.short_tracks = ["Three", "Six", "Seven"]
    _, eps = _Stub()._hide_subset_editions([], [short, full])
    assert [a.id for a in eps] == ["a7"]
    assert full.item_fetches == 1
    # The short read on the SMALL side must never make it look like a subset.
    short2, full2, _ = _forsaker()
    short2.short_tracks = ["One", "Two"]
    short2.items = lambda limit=100, offset=0: short2._rows(["One", "Two"])  # items short too
    _, eps = _Stub()._hide_subset_editions([], [short2, full2])
    assert [a.id for a in eps] == ["a5", "a7"]


def test_the_items_fallback_pages_and_drops_videos():
    names = [f"t{i}" for i in range(150)]
    album = _Album("big", "Big", names)
    album.short_tracks = ["t0"]
    calls = []

    def items(limit=100, offset=0):
        calls.append(offset)
        rows = album._rows(names)[offset : offset + limit]
        return rows + ([backend.Video.__new__(backend.Video)] if offset == 0 else [])

    album.items = items
    got = backend._album_tracks_full(album)
    assert len(got) == 150 and calls == [0, 100]


def test_a_short_read_on_the_fuller_edition_still_hides_the_complete_smaller_one():
    # Coheed and Cambria, Year Of The Black Rainbow: the Deluxe advertises 15
    # tracks and serves 14 on every endpoint (one delisted). The standard 12
    # are all among the 14, so the standard is the row to hide.
    short, full, _ = _forsaker()
    full.num_tracks = 8  # advertised one more than it serves
    _, eps = _Stub()._hide_subset_editions([], [short, full])
    assert [a.id for a in eps] == ["a7"]


# ---- 'best of both' splits clean from explicit before it hides anything ----


def _rumours():
    """A clean standard and an explicit deluxe: the deluxe carries the explicit
    cut of all twelve shared songs plus three of its own, so by track content
    alone the clean edition is a strict subset of it."""
    shared = [f"Song {i}" for i in range(12)]
    clean = _Album("c1", "Rumours", shared)
    deluxe = _Album("e1", "Rumours (Deluxe)", [*shared, "Bonus A", "Bonus B", "Bonus C"])
    deluxe.explicit_names = set(shared)
    return clean, deluxe


def test_the_clean_edition_survives_when_the_user_asked_for_clean():
    """The page used to run a bare subset collapse under 'best of both', which
    hides a clean cut inside its explicit twin. The sweep never does that: it
    splits the two sides first and merges only within one. The page said the
    user does not own this album while 'Download discography' would have
    fetched exactly it."""
    clean, deluxe = _rumours()
    albums, _eps = _Stub(explicit_mode="clean")._hide_subset_editions([clean, deluxe], [])
    assert [a.id for a in albums] == ["c1"]


def test_the_explicit_preference_keeps_the_explicit_side():
    clean, deluxe = _rumours()
    albums, _eps = _Stub(explicit_mode="explicit")._hide_subset_editions([clean, deluxe], [])
    assert [a.id for a in albums] == ["e1"]


def test_both_keeps_each_side_because_neither_may_absorb_the_other():
    clean, deluxe = _rumours()
    albums, _eps = _Stub(explicit_mode="both")._hide_subset_editions([clean, deluxe], [])
    assert [a.id for a in albums] == ["c1", "e1"]


def test_editions_that_do_not_disagree_are_still_collapsed_under_best_of_both():
    """The split only ever separates editions that really do carry the same
    song both ways. A plain subset with no clean/explicit dispute is hidden as
    before, whatever the explicit preference says."""
    short, full, _ = _forsaker()
    for mode in ("clean", "explicit", "both"):
        albums, _eps = _Stub(explicit_mode=mode)._hide_subset_editions([short, full], [])
        assert [a.id for a in albums] == ["a7"], mode
