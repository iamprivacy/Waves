"""Unit tests for the opt-in 'Clean album-artist tag' setting.

The album-artist METADATA tag is written from ``get_album_artists`` (download.py).
Waves wraps that one symbol so the tag can be reduced to just the primary artist
(multi-value album-artist fields confuse Plex). Folder paths are untouched.

Pure-function tests: no Qt, no network. We drive the real upstream helper with
fake media objects, so the wrapper is exercised end to end.
"""

import pytest
from tidalapi.artist import Role

import tidaler.download as _tidaler_download
from tidaler.waves_ui.backend import (
    _album_artists_for_metadata,
    _clean_album_artists,
    _set_clean_album_artist,
)


@pytest.fixture(autouse=True)
def _reset_flag():
    # The setting is a module-global flag; never let one test leak into another.
    _set_clean_album_artist(False)
    yield
    _set_clean_album_artist(False)


class _Artist:
    def __init__(self, name, roles=(Role.main,)):
        self.name = name
        self.roles = list(roles)


class _Media:
    """Non-Track media (e.g. an Album): the upstream helper reads ``.artists``."""

    def __init__(self, artists):
        self.artists = artists


# ---- _clean_album_artists (pure) -------------------------------------------
@pytest.mark.parametrize(
    "names,expected",
    [
        (["Solo"], ["Solo"]),
        (["A", "B", "C"], ["A"]),
        ([], []),
    ],
)
def test_clean_album_artists(names, expected):
    assert _clean_album_artists(names) == expected


# ---- the installed wrapper honours the flag --------------------------------
def test_wrapper_is_installed_on_download_module():
    assert _tidaler_download.get_album_artists is _album_artists_for_metadata


def test_default_off_keeps_every_main_album_artist():
    media = _Media([_Artist("Queen"), _Artist("David Bowie")])
    assert _album_artists_for_metadata(media) == ["Queen", "David Bowie"]


def test_on_collapses_to_primary_only():
    media = _Media([_Artist("Queen"), _Artist("David Bowie")])
    _set_clean_album_artist(True)
    assert _album_artists_for_metadata(media) == ["Queen"]


def test_on_is_noop_for_single_artist():
    media = _Media([_Artist("Adele")])
    _set_clean_album_artist(True)
    assert _album_artists_for_metadata(media) == ["Adele"]


def test_non_main_roles_still_filtered_before_cleaning():
    # The underlying helper keeps only Role.main artists; a featured-only credit
    # is dropped, so the "primary" after cleaning is the first MAIN artist.
    media = _Media(
        [
            _Artist("Featured Guest", roles=[Role.featured]),
            _Artist("Primary", roles=[Role.main]),
            _Artist("Second Main", roles=[Role.main]),
        ]
    )
    assert _album_artists_for_metadata(media) == ["Primary", "Second Main"]
    _set_clean_album_artist(True)
    assert _album_artists_for_metadata(media) == ["Primary"]
