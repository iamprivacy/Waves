"""Unit tests for the opt-in "most complete edition only" collapsing.

Pure-function tests: no network and no Qt runtime (the module-level helpers in
``backend`` operate on plain objects via injected ``titles_of`` / ``quality_of``).
"""

import pytest

from tidaler.waves_ui.backend import (
    _collapse_album_editions,
    _edition_base_key,
    _norm_track_title,
    _strip_edition_quals,
)


class _Artist:
    def __init__(self, name):
        self.name = name


class _Album:
    """Minimal stand-in for a tidalapi Album (enough for name_builder_title /
    _primary_artist_name and the injected title/quality callbacks)."""

    def __init__(self, name, tracks=(), quality=3, artist="Band"):
        # tracks: each item is a "title" (duration defaults) or a (title, duration) tuple
        self.name = name
        self.artist = _Artist(artist)
        self.quality = quality
        self._tracks = [t if isinstance(t, tuple) else (t, 200) for t in tracks]
        self.num_tracks = len(self._tracks)


def _tracks_of(a):
    return [(_norm_track_title(t), d) for (t, d) in a._tracks if _norm_track_title(t)]


def _quality_of(a):
    return a.quality


def _collapse(albums, conflict="keep_both"):
    return _collapse_album_editions(albums, _tracks_of, _quality_of, conflict)


def _key(title):
    return _edition_base_key(_Album(title))


# ---- _strip_edition_quals ---------------------------------------------------
@pytest.mark.parametrize(
    "title,expected",
    [
        ("album", "album"),
        ("album (deluxe edition)", "album"),
        ("album [deluxe]", "album"),
        ("album (super deluxe)", "album"),
        ("album (expanded edition)", "album"),
        ("album (platinum edition)", "album"),
        ("album (3am edition)", "album"),
        ("album (til dawn edition)", "album"),
        ("album (bonus track version)", "album"),
        ("album (deluxe edition) (bonus)", "album"),  # peel nested
        # keep-markers: never stripped
        ("album (2011 remaster)", "album (2011 remaster)"),
        ("album (taylor's version)", "album (taylor's version)"),
        ("album (anniversary edition)", "album (anniversary edition)"),
        ("album (deluxe anniversary edition)", "album (deluxe anniversary edition)"),
        ("album (special edition)", "album (special edition)"),
        ("album (collector's edition)", "album (collector's edition)"),
        ("album (live)", "album (live)"),
        ("album (acoustic)", "album (acoustic)"),
        ("album (2021 mix)", "album (2021 mix)"),  # a new mix is a distinct master
        ("album (stereo mix)", "album (stereo mix)"),
        # internal dash must survive (no trailing-dash stripping)
        ("the beatles 1967 – 1970", "the beatles 1967 – 1970"),
    ],
)
def test_strip_edition_quals(title, expected):
    assert _strip_edition_quals(title) == expected


# ---- _edition_base_key grouping --------------------------------------------
def test_edition_variants_group_together():
    base = _key("Album")
    for variant in [
        "Album (Deluxe Edition)",
        "Album [Deluxe]",
        "Album (Super Deluxe)",
        "Album (Expanded Edition)",
        "Album (Platinum Edition)",
        "Album (3am Edition)",
        "Album (Bonus Track Version)",
    ]:
        assert _key(variant) == base, variant


def test_keep_markers_stay_separate():
    base = _key("Album")
    for distinct in [
        "Album (2011 Remaster)",
        "Album (Taylor's Version)",
        "Album (Anniversary Edition)",
        "Album (Deluxe Anniversary Edition)",  # keep-marker beats the deluxe word
        "Album (Special Edition)",
        "Album (Collector's Edition)",
        "Album (Live)",
        "Album (Acoustic)",
        "Album (2021 Mix)",
        "Album (New Stereo Mix)",
    ]:
        assert _key(distinct) != base, distinct


def test_different_artist_not_grouped():
    assert _key("Album") != _edition_base_key(_Album("Album", artist="Other"))


# ---- _norm_track_title ------------------------------------------------------
@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Song", "song"),
        ("Song (feat. Bon Iver)", "song"),
        ("Song (2011 Remaster)", "song"),
        ("Song (Acoustic Version)", "song"),
        ("Song (Live)", "song"),
        ("Song - Single Mix", "song - single mix"),  # only bracketed quals stripped
    ],
)
def test_norm_track_title(raw, expected):
    assert _norm_track_title(raw) == expected


# ---- _collapse_album_editions ----------------------------------------------
def test_standard_subset_of_deluxe_collapses():
    std = _Album("Album", ["A", "B", "C"])
    dlx = _Album("Album (Deluxe Edition)", ["A", "B", "C", "D", "E"])
    assert _collapse([std, dlx]) == [dlx]


def test_three_edition_chain_keeps_only_the_largest():
    std = _Album("Album", ["A", "B"])
    dlx = _Album("Album (Deluxe)", ["A", "B", "C"])
    sup = _Album("Album (Super Deluxe)", ["A", "B", "C", "D"])
    assert _collapse([std, dlx, sup]) == [sup]


def test_keep_both_when_not_a_subset():
    # the smaller edition has a track the "complete" one lacks -> not contained
    std = _Album("Album", ["A", "B", "C", "X"])
    dlx = _Album("Album (Deluxe Edition)", ["A", "B", "C", "D"])
    assert set(_collapse([std, dlx])) == {std, dlx}


def test_same_base_but_disjoint_tracks_kept():
    # "(Motion Picture Soundtrack)" isn't a keep-marker, so it shares the base
    # title, but disjoint tracks mean it's not a subset -> keep both.
    alb = _Album("Album", ["A", "B"])
    ost = _Album("Album (Motion Picture Soundtrack)", ["Z1", "Z2", "Z3"])
    assert set(_collapse([alb, ost])) == {alb, ost}


def test_remaster_never_collapsed_even_if_contained():
    std = _Album("Album", ["A", "B", "C"])
    rem = _Album("Album (2011 Remaster)", ["A", "B", "C", "D"])  # superset titles, but keep-marker
    assert set(_collapse([std, rem])) == {std, rem}


def test_unknown_tracks_keeps_both():
    std = _Album("Album", [])  # no tracks fetched -> unknown -> keep
    dlx = _Album("Album (Deluxe Edition)", ["A", "B", "C"])
    assert set(_collapse([std, dlx])) == {std, dlx}


def test_same_title_different_duration_kept_both():
    # "Song" exists in both but as a different-length recording -> not the same
    # song -> not a subset -> keep both (don't lose the standard cut).
    std = _Album("Album", [("Song", 225), ("B", 200)])
    dlx = _Album("Album (Deluxe Edition)", [("Song", 270), ("B", 200), ("C", 200)])
    assert set(_collapse([std, dlx])) == {std, dlx}


def test_duration_within_tolerance_collapses():
    # a 1-second metadata difference is still the same song -> collapse
    std = _Album("Album", [("Song", 225), ("B", 200)])
    dlx = _Album("Album (Deluxe Edition)", [("Song", 226), ("B", 200), ("C", 200)])
    assert _collapse([std, dlx]) == [dlx]


def test_half_length_snippet_blocks_collapse():
    # a radio "snippet" (same title, ~half length) is a distinct track, so the
    # snippet edition is not a subset of the full album.
    full = _Album("Album", [("Song", 213), ("B", 200), ("C", 200)])
    promo = _Album("Album (Radio Special)", [("Song", 105), ("B", 100)])
    assert set(_collapse([full, promo])) == {full, promo}


def test_no_quality_conflict_drops_subset_regardless_of_mode():
    std = _Album("Album", ["A", "B", "C"], quality=3)
    dlx = _Album("Album (Deluxe Edition)", ["A", "B", "C", "D"], quality=4)  # complete AND higher quality
    for mode in ("keep_both", "completeness", "quality"):
        assert _collapse([std, dlx], mode) == [dlx], mode


def test_quality_conflict_modes():
    std = _Album("Album", ["A", "B", "C"], quality=4)  # hi-res, fewer tracks
    dlx = _Album("Album (Deluxe Edition)", ["A", "B", "C", "D"], quality=3)  # lossless, more complete
    assert set(_collapse([std, dlx], "keep_both")) == {std, dlx}
    assert _collapse([std, dlx], "completeness") == [dlx]
    assert _collapse([std, dlx], "quality") == [std]


def test_singletons_and_order_preserved():
    a = _Album("First", ["x"])
    b = _Album("Second", ["y"])
    c = _Album("Third", ["z"])
    assert _collapse([a, b, c]) == [a, b, c]
