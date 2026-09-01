"""Naming a song whose album TIDAL will not hand over.

Issue #35's fallback keeps a track downloadable when the album re-fetch 404s
(the song still streams; only the album entry is gone). What it keeps is the
album summary embedded in the track's own JSON: an id, a title, a cover, and
nothing else. The tagger was taught to tolerate that; the path formatter was
not, so the very songs the fallback rescued landed as

    Artist/[None] Album/1-{album_track_num}. Artist - Song.flac

a folder and a file named after the code's own gaps, which the app then never
renames and never deletes.

Every album token has to survive both a hollow album and no album at all.
"""

from __future__ import annotations

import pytest
from tidalapi import Album, Artist, Track

from waves.helper.path import format_path_media
from waves.model.cfg import Settings

TRACK_TEMPLATE = Settings().format_track


def _summary_album(**over) -> Album:
    """The album a track carries inside its own payload."""
    a = Album.__new__(Album)
    a.id = 77
    a.name = "Delisted Album"
    a.release_date = None
    a.tidal_release_date = None
    a.num_tracks = None
    a.num_volumes = None
    a.explicit = None
    a.type = None
    a.artist = None
    for key, value in over.items():
        setattr(a, key, value)
    return a


def _track(album: Album | None, track_num: int = 5) -> Track:
    t = Track.__new__(Track)
    t.id = 5
    t.name = "Song"
    t.full_name = "Song"
    t.version = None
    t.track_num = track_num
    t.volume_num = 1
    t.explicit = False
    t.isrc = "ISRC1"
    t.media_metadata_tags = []
    artist = Artist.__new__(Artist)
    artist.id = 3
    artist.name = "Artist"
    t.artist = artist
    t.artists = [artist]
    t.album = album
    return t


def test_a_rescued_track_is_not_named_after_the_missing_album_details():
    out = format_path_media(TRACK_TEMPLATE, _track(_summary_album()), album_track_num_pad_min=2)

    assert out == "Artist/Delisted Album/05. Artist - Song"


@pytest.mark.parametrize("token", ["{album_year}", "{album_track_num}", "{album_title}"])
def test_no_template_token_survives_into_a_name(token):
    """The blanket catch in the formatter substitutes nothing when a token
    raises, so a gap used to reach the filesystem verbatim."""
    out = format_path_media(TRACK_TEMPLATE, _track(None), album_track_num_pad_min=2)

    assert token not in out


def test_a_track_with_no_album_at_all_lands_in_the_artist_folder():
    out = format_path_media(TRACK_TEMPLATE, _track(None), album_track_num_pad_min=2)

    assert out == "Artist/05. Artist - Song"


def test_an_unknown_track_count_still_pads_to_the_users_minimum():
    """The count is what the padding is normally read from; without it the
    track's own number is, so the user's minimum still holds and the number
    never comes out unpadded next to its neighbours."""
    out = format_path_media("{album_track_num}", _track(_summary_album(), track_num=7), album_track_num_pad_min=3)

    assert out == "007"


def test_a_known_track_count_still_sets_the_padding():
    out = format_path_media("{album_track_num}", _track(_summary_album(num_tracks=120)), album_track_num_pad_min=2)

    assert out == "005"


def test_an_unknown_disc_count_writes_no_disc_prefix():
    """A disc prefix is a claim about a set: "1-" in front of every track of
    an album nothing is known about is a claim that cannot be made."""
    assert format_path_media("{track_volume_num_optional}x", _track(_summary_album())) == "x"
    assert format_path_media("{track_volume_num_optional_CD}x", _track(_summary_album())) == "x"


def test_a_real_multi_disc_album_keeps_its_prefix():
    track = _track(_summary_album(num_volumes=2))
    track.volume_num = 2

    assert format_path_media("{track_volume_num_optional}x", track) == "2-x"


def test_an_unknown_year_takes_the_empty_brackets_with_it():
    """The shipped template dresses the year in brackets, so a token that
    substitutes nothing would otherwise leave "[] Album" behind."""
    out = format_path_media("[{album_year}] {album_title}", _track(_summary_album()))

    assert out == "Delisted Album"


def test_a_known_year_is_untouched():
    import datetime

    album = _summary_album(release_date=datetime.datetime(2019, 5, 1))
    out = format_path_media("[{album_year}] {album_title}", _track(album))

    assert out == "[2019] Delisted Album"


def test_a_bracket_pair_the_title_itself_carries_is_left_alone():
    album = _summary_album(name="Album [Deluxe]")
    out = format_path_media("{album_title}", _track(album))

    assert out == "Album [Deluxe]"


def test_the_other_album_tokens_answer_empty_rather_than_none():
    for template in ("{album_num_tracks}", "{album_id}", "{media_type}", "{album_explicit}"):
        for album in (_summary_album(), None):
            out = format_path_media(template + "|", _track(album))
            assert "None" not in out, template
            assert "{" not in out, template


def test_a_failed_token_reaches_the_breadcrumbs(caplog):
    """The formatter's blanket catch may never be the end of the story: a
    packaged build has no stdout to print to."""

    class Exploding(Track):
        @property
        def full_name(self):
            raise ValueError("boom")

    track = _track(_summary_album())
    track.__class__ = Exploding

    with caplog.at_level("WARNING", logger="waves.path"):
        format_path_media("{track_title}", track)

    assert any("track_title" in record.getMessage() for record in caplog.records)
