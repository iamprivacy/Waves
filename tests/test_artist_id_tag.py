"""A file carries the ids of the artists it is credited to, not just their names.

Two artists can share a name (the band Marina and the singer Marina), and a
name is all a downloaded file has ever recorded. So a folder named for an
artist cannot say which of them it belongs to, an album by one can look
"already in your library" because the other's folder holds a same-titled
release, and a discography save can quietly deliver a stranger's music.

The fix starts here: stamp the TIDAL artist ids beside the names, using the
same custom-tag mechanism ``WAVES_TIDAL_ID`` has shipped with since v0.1.25.
This is the record only; nothing reads it to place folders yet. It has to land
first, because it can only ever describe files downloaded after it ships.

Untagged stays "unknown", never "somebody else": every reader here proves an
absent tag answers empty rather than guessing.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import mutagen.flac
import mutagen.id3
import mutagen.mp3
import mutagen.mp4
import pytest
from tidalapi.artist import Role

from waves.download import _artist_ids
from waves.helper.tidal import get_album_artist_ids, get_album_artists
from waves.metadata import (
    ALBUM_ARTIST_ID_TAG,
    ARTIST_ID_TAG,
    ITEM_ID_TAG,
    Metadata,
    read_custom_ids,
    read_item_id,
)

_UPC = {"FLAC": "UPC", "MP4": "UPC", "MP3": "UPC"}


def _flac_stub():
    fake = mutagen.flac.FLAC.__new__(mutagen.flac.FLAC)
    fake.tags = None
    fake.metadata_blocks = []
    fake.save = lambda *a, **k: None
    return fake


def _mp3_stub():
    fake = mutagen.mp3.MP3.__new__(mutagen.mp3.MP3)
    fake.tags = None
    fake.save = lambda *a, **k: None
    return fake


def _mp4_stub():
    fake = mutagen.mp4.MP4.__new__(mutagen.mp4.MP4)
    fake.tags = None
    fake.save = lambda *a, **k: None
    return fake


def _write(stub, tmp_path, name, **kw):
    file = tmp_path / name
    file.write_bytes(b"x")
    with patch("waves.metadata.mutagen.File", return_value=stub):
        assert Metadata(path_file=file, target_upc=_UPC, **kw).save() is True
    return stub


def _artist(id_, name, roles=None):
    return SimpleNamespace(id=id_, name=name, roles=roles)


# --------------------------------------------------------------------------- #
# The ids reach every container Waves can write
# --------------------------------------------------------------------------- #
def test_flac_carries_both_id_lists(tmp_path):
    flac = _write(
        _flac_stub(),
        tmp_path,
        "t.flac",
        title="T",
        artists=["Marina", "Guest"],
        albumartist=["Marina"],
        artist_ids=["4676988", "77"],
        album_artist_ids=["4676988"],
    )
    assert flac.tags[ARTIST_ID_TAG] == ["4676988", "77"]
    assert flac.tags[ALBUM_ARTIST_ID_TAG] == ["4676988"]


def test_mp3_carries_both_id_lists(tmp_path):
    mp3 = _write(
        _mp3_stub(),
        tmp_path,
        "t.mp3",
        title="T",
        artists=["Marina", "Guest"],
        albumartist=["Marina"],
        artist_ids=["4676988", "77"],
        album_artist_ids=["4676988"],
    )
    assert mp3.tags[f"TXXX:{ARTIST_ID_TAG}"].text == ["4676988", "77"]
    assert mp3.tags[f"TXXX:{ALBUM_ARTIST_ID_TAG}"].text == ["4676988"]


def test_mp4_carries_both_id_lists_as_bytes(tmp_path):
    mp4 = _write(
        _mp4_stub(),
        tmp_path,
        "t.m4a",
        title="T",
        artists=["Marina", "Guest"],
        albumartist=["Marina"],
        artist_ids=["4676988", "77"],
        album_artist_ids=["4676988"],
    )
    assert mp4.tags[f"----:com.apple.iTunes:{ARTIST_ID_TAG}"] == [b"4676988", b"77"]
    assert mp4.tags[f"----:com.apple.iTunes:{ALBUM_ARTIST_ID_TAG}"] == [b"4676988"]


def test_a_music_video_carries_them_too(tmp_path):
    """set_mp4_video is a separate branch, and it is the one that gets forgotten."""
    mp4 = _write(
        _mp4_stub(),
        tmp_path,
        "v.m4a",
        title="V",
        artists=["Marina"],
        albumartist=["Marina"],
        is_video=True,
        artist_ids=["4676988"],
        album_artist_ids=["4676988"],
    )
    assert mp4.tags[f"----:com.apple.iTunes:{ARTIST_ID_TAG}"] == [b"4676988"]
    assert mp4.tags[f"----:com.apple.iTunes:{ALBUM_ARTIST_ID_TAG}"] == [b"4676988"]


# --------------------------------------------------------------------------- #
# What goes in comes back out, per container
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("stub", "name"),
    [(_flac_stub, "t.flac"), (_mp3_stub, "t.mp3"), (_mp4_stub, "t.m4a")],
)
def test_the_ids_read_back_in_written_order(tmp_path, stub, name):
    written = _write(
        stub(),
        tmp_path,
        name,
        title="T",
        artists=["Marina", "Guest"],
        albumartist=["Marina"],
        artist_ids=["4676988", "77"],
        album_artist_ids=["4676988"],
    )
    with patch("waves.metadata.mutagen.File", return_value=written):
        assert read_custom_ids(tmp_path / name, ARTIST_ID_TAG) == ["4676988", "77"]
        assert read_custom_ids(tmp_path / name, ALBUM_ARTIST_ID_TAG) == ["4676988"]


@pytest.mark.parametrize(
    ("stub", "name"),
    [(_flac_stub, "t.flac"), (_mp3_stub, "t.mp3"), (_mp4_stub, "t.m4a")],
)
def test_an_untagged_file_is_unknown_not_different(tmp_path, stub, name):
    written = _write(stub(), tmp_path, name, title="T", artists=["Marina"], albumartist=["Marina"])
    with patch("waves.metadata.mutagen.File", return_value=written):
        assert read_custom_ids(tmp_path / name, ARTIST_ID_TAG) == []
        assert read_custom_ids(tmp_path / name, ALBUM_ARTIST_ID_TAG) == []


def test_an_unreadable_file_answers_empty(tmp_path):
    file = tmp_path / "broken.flac"
    file.write_bytes(b"not audio")
    with patch("waves.metadata.mutagen.File", return_value=None):
        assert read_custom_ids(file, ARTIST_ID_TAG) == []
    with patch("waves.metadata.mutagen.File", side_effect=OSError("gone")):
        assert read_custom_ids(file, ARTIST_ID_TAG) == []


# --------------------------------------------------------------------------- #
# The item id keeps its old contract through the shared reader
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("stub", "name"),
    [(_flac_stub, "t.flac"), (_mp3_stub, "t.mp3"), (_mp4_stub, "t.m4a")],
)
def test_read_item_id_still_returns_a_single_id(tmp_path, stub, name):
    written = _write(stub(), tmp_path, name, title="T", artists=["A"], albumartist=["A"], item_id="12345")
    with patch("waves.metadata.mutagen.File", return_value=written):
        assert read_item_id(tmp_path / name) == "12345"


def test_the_item_id_and_the_artist_id_are_different_questions():
    assert len({ITEM_ID_TAG, ARTIST_ID_TAG, ALBUM_ARTIST_ID_TAG}) == 3


# --------------------------------------------------------------------------- #
# Where the ids come from
# --------------------------------------------------------------------------- #
def test_credited_artist_ids_follow_the_credited_order():
    video = SimpleNamespace(artists=[_artist(4676988, "Marina"), _artist(77, "Guest")])
    assert _artist_ids(video) == ["4676988", "77"]


def test_an_id_less_stub_is_dropped_not_written_blank():
    """An empty value in an identity tag would read as a real, nameless artist."""
    track = SimpleNamespace(artists=[_artist(4676988, "Marina"), _artist(None, "Stub")])
    assert _artist_ids(track) == ["4676988"]


def test_no_credits_at_all_is_an_empty_list():
    assert _artist_ids(SimpleNamespace()) == []
    assert _artist_ids(SimpleNamespace(artists=None)) == []


def test_album_artist_ids_name_the_same_artists_the_name_tag_does():
    """One main-credit filter, so neither list carries an artist the other excludes."""
    album = SimpleNamespace(
        artists=[
            _artist(4676988, "Marina", [Role.main]),
            _artist(77, "Producer", [Role.contributor]),
            _artist(88, "Feed Artist", None),  # the V2 home feed leaves roles unset
        ]
    )
    assert get_album_artists(album) == ["Marina", "Feed Artist"]
    assert get_album_artist_ids(album) == ["4676988", "88"]


def test_an_id_less_album_artist_is_dropped_not_written_as_none():
    """The album-artist half needs the same proof the track half already has:
    without the filter the tag would carry the literal string "None"."""
    album = SimpleNamespace(artists=[_artist(4676988, "Marina", [Role.main]), _artist(None, "Stub", [Role.main])])
    assert get_album_artists(album) == ["Marina", "Stub"]
    assert get_album_artist_ids(album) == ["4676988"]


def test_an_album_that_never_arrived_has_no_artist_ids():
    assert get_album_artist_ids(SimpleNamespace(artists=None)) == []
