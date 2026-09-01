"""Stripping an illegal character must not leave its spaces behind.

``pathvalidate`` deletes characters a filesystem rejects but keeps whatever
surrounded them, so an album called ``The Better Life / Dead Love`` landed in
a folder named ``The Better Life  Dead Love``, with a double space where the
slash had been (issue #15). Token values now collapse runs of whitespace and
trim their edges.

The self-dressing tokens are the delicate part: ``{video_year_optional}``
renders "[2026] " and relies on that trailing space to separate itself from
the title, and the explicit marker carries a LEADING space. Both must survive
the tidying, so they are pinned here alongside it.
"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

from tidalapi import Album, Track, Video

from waves.constants import FORMAT_TEMPLATE_EXPLICIT
from waves.helper.path import format_path_media

_SLASHED = "The Better Life / Dead Love"


def _track(album_title: str = _SLASHED, title: str = "Song", explicit: bool = False) -> Track:
    t = Track.__new__(Track)
    t.id = 1
    t.name = title
    t.version = None
    # tidalapi resolves a track's display title through full_name; a stub
    # built with __new__ never runs the property's initialisation.
    t.full_name = title
    t.explicit = explicit
    t.track_num = 1
    t.volume_num = 1
    t.artists = [SimpleNamespace(name="Three Doors Down")]
    t.artist = SimpleNamespace(name="Three Doors Down")
    album = Album.__new__(Album)
    album.id = 1
    album.name = album_title
    album.artists = [SimpleNamespace(name="Three Doors Down", roles=None)]
    album.artist = SimpleNamespace(name="Three Doors Down")
    album.num_tracks = 1
    album.num_volumes = 1
    album.release_date = datetime(2000, 2, 8)
    t.album = album
    return t


def _video(title: str = "Kryptonite", release_date=datetime(2026, 6, 6)) -> Video:
    v = Video.__new__(Video)
    v.id = 1
    v.name = title
    v.full_name = title
    v.artists = [SimpleNamespace(name="Three Doors Down")]
    v.artist = SimpleNamespace(name="Three Doors Down")
    v.album = None
    v.explicit = False
    v.release_date = release_date
    return v


class TestStrippedCharactersLeaveNoGap:
    def test_slash_in_an_album_name_does_not_double_the_space(self):
        out = format_path_media("{album_title}", _track())

        assert "  " not in out
        assert out == "The Better Life Dead Love"

    def test_every_illegal_character_collapses(self):
        # The union of what macOS, Linux and Windows reject.
        for raw in ("A / B", "A \\ B", "A : B", "A * B", "A ? B", 'A " B', "A < B", "A > B", "A | B"):
            out = format_path_media("{album_title}", _track(album_title=raw))

            assert "  " not in out, f"{raw!r} left a doubled space"
            assert out == "A B", f"{raw!r} produced {out!r}"

    def test_a_leading_or_trailing_illegal_character_leaves_no_edge_space(self):
        for raw in ("? Song", "Song ?", "  Song  "):
            out = format_path_media("{album_title}", _track(album_title=raw))

            assert out == "Song", f"{raw!r} produced {out!r}"

    def test_an_ordinary_name_is_untouched(self):
        out = format_path_media("{album_title}", _track(album_title="Away From The Sun"))

        assert out == "Away From The Sun"

    def test_the_template_s_own_separators_survive(self):
        # Only token VALUES are tidied; spacing the template spells out itself
        # (here " - ") must come through intact.
        out = format_path_media("{album_title} - {track_title}", _track())

        assert out == "The Better Life Dead Love - Song"


class TestSelfDressingTokensKeepTheirSpace:
    def test_video_year_prefix_keeps_its_separator(self):
        out = format_path_media("{video_year_optional}{track_title}", _video())

        assert out == "[2026] Kryptonite"

    def test_video_without_a_date_adds_no_stray_space(self):
        out = format_path_media("{video_year_optional}{track_title}", _video(release_date=None))

        assert out == "Kryptonite"

    def test_explicit_marker_keeps_its_leading_space(self):
        out = format_path_media("{track_title}{track_explicit}", _track(explicit=True))

        assert out == f"Song{FORMAT_TEMPLATE_EXPLICIT}"
