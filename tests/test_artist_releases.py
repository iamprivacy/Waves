"""Unit tests for the discography 'Featured' vs 'Appears on' split.

Pure-function tests: no network, no Qt. ``_is_compilation_release`` decides
whether a release out of TIDAL's COMPILATIONS bucket (``get_other``) is a
various-artists compilation ('Appears on') or a named artist's release the
target is a featured guest on ('Featured'), purely from the primary credit.
"""

import pytest
from tidalapi.album import Album

from tidaler.waves_ui.backend import _is_album_entity, _is_compilation_release


class _Artist:
    def __init__(self, name="", aid=None):
        self.name = name
        self.id = aid


class _Album:
    """Minimal Album stand-in: either a single ``.artist`` or an ``.artists`` list."""

    def __init__(self, name="Album", artist=None, artists=None):
        self.name = name
        self.artist = artist
        self.artists = artists


# ---- Various Artists / compilations -> 'Appears on' -------------------------
@pytest.mark.parametrize(
    "primary",
    [
        _Artist("Various Artists", aid=2935),  # canonical TIDAL VA id
        _Artist("Various Artists"),  # English name, any id
        _Artist("various artists"),  # case-insensitive
        _Artist("  Various   Artists  "),  # whitespace-insensitive (regex search)
        _Artist("ヴァリアス・アーティスト", aid=9174206),  # localized VA, distinct id
        _Artist("群星"),  # Chinese
        _Artist("Verschiedene Interpreten"),  # German
        _Artist("Varios Artistas"),  # Spanish
        _Artist("Vários Artistas"),  # Portuguese (accented)
        _Artist("Artisti Vari"),  # Italian
        _Artist("Multi-interprètes"),  # French
        _Artist("Some Mystery Id", aid=2935),  # id wins even with an odd name
    ],
)
def test_compilation_primaries(primary):
    assert _is_compilation_release(_Album(artist=primary)) is True


# ---- named single artist -> 'Featured' -------------------------------------
@pytest.mark.parametrize(
    "name",
    [
        "Travis Scott",
        "U.S.A. for Africa",  # a supergroup credit is still a named primary
        "Gracie Abrams",
        "The Jackson 5",
        "Quincy Jones",
        "Various Voices",  # 'various' alone must NOT trip the VA matcher
        "Artist of the Year",  # contains 'artist' but not the VA phrase
    ],
)
def test_named_artist_is_featured(name):
    assert _is_compilation_release(_Album(artist=_Artist(name))) is False


# ---- structural fallbacks ---------------------------------------------------
def test_artists_list_fallback_when_no_artist_attr():
    alb = _Album(artist=None, artists=[_Artist("Various Artists", aid=2935)])
    assert _is_compilation_release(alb) is True
    alb2 = _Album(artist=None, artists=[_Artist("Travis Scott")])
    assert _is_compilation_release(alb2) is False


def test_primary_taken_from_first_of_artists():
    # first credited artist decides; a VA placeholder first -> compilation
    alb = _Album(artist=None, artists=[_Artist("Various Artists"), _Artist("Travis Scott")])
    assert _is_compilation_release(alb) is True


def test_no_credit_treated_as_compilation():
    assert _is_compilation_release(_Album(artist=None, artists=[])) is True
    assert _is_compilation_release(_Album(artist=None, artists=None)) is True


# ---- album-entity guard: discography is albums only, never playlists/mixes --
class _Playlist:
    """Stand-in for a tidalapi Playlist/Mix; it must never reach a discography."""

    def __init__(self):
        self.id = "pl-1"
        self.type = "PLAYLIST"


def test_real_album_is_an_album_entity():
    # Album.__new__ gives a genuine Album instance without TIDAL/network init.
    assert _is_album_entity(Album.__new__(Album)) is True


@pytest.mark.parametrize("obj", [_Playlist(), object(), None, "not-an-album", 123])
def test_non_albums_are_rejected(obj):
    assert _is_album_entity(obj) is False


# ---- guest releases contribute only the artist's own tracks -----------------
class _Track:
    def __init__(self, tid, artists=None, artist=None):
        self.id = tid
        self.artists = artists
        self.artist = artist


def test_artist_on_track_matches_credited_artist():
    from tidaler.waves_ui.backend import _artist_on_track

    me = _Artist("Me", aid=42)
    other = _Artist("Other", aid=7)
    assert _artist_on_track(_Track("t1", artists=[other, me]), "42") is True
    assert _artist_on_track(_Track("t2", artists=[other]), "42") is False
    # single .artist credit counts too
    assert _artist_on_track(_Track("t3", artist=me), "42") is True
    # no credits at all -> never matched
    assert _artist_on_track(_Track("t4"), "42") is False


def test_artist_on_track_compares_ids_as_strings():
    from tidaler.waves_ui.backend import _artist_on_track

    me = _Artist("Me", aid=42)
    assert _artist_on_track(_Track("t1", artists=[me]), 42) is True
