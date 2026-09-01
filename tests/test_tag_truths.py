"""Tags may not claim what the download does not know.

Three findings from the gap round of the 2026-08-29 audit, all in the tag
writer:

* A track delivered through the album-404 fallback (the album summary carries
  no track count) was tagged "of 1" beside its real track number, so a player
  rendered "7 of 1" and a gap checker read the album as complete. The naming
  side of the same unknown had already settled the rule: a count we do not
  have is not a claim we make.
* Empty MP4 freeform atoms survived the empty-tag sweep, because freeform
  values are BYTES and the sweep only knew strings. The same track saved as
  FLAC carried no such fields.
* The MP3 branch is unreachable today (no download can produce an .mp3), and
  every frame in it was wrong: the album artist went into the Original Artist
  frame, the share URL frame was handed the ISRC through a keyword mutagen
  discards, the synced-lyrics frame was fed a shape that raises while
  rendering (which would abort the whole save), and the cover was added with
  no mime type.
"""

from __future__ import annotations

import pathlib
import threading
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import mutagen.flac
import mutagen.id3
import mutagen.mp3
import mutagen.mp4
import pytest
from tidalapi import Track

from waves.download import Download
from waves.metadata import Metadata


def _mp4_stub():
    fake = mutagen.mp4.MP4.__new__(mutagen.mp4.MP4)
    fake.tags = None
    fake.save = lambda *a, **k: None
    return fake


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


def _write(stub, tmp_path, name, **kw):
    file = tmp_path / name
    file.write_bytes(b"x")
    with patch("waves.metadata.mutagen.File", return_value=stub):
        assert Metadata(path_file=file, target_upc={"FLAC": "UPC", "MP4": "UPC", "MP3": "UPC"}, **kw).save() is True
    return stub


# --------------------------------------------------------------------------- #
# G-21: an unknown track count
# --------------------------------------------------------------------------- #
def test_an_unknown_track_count_is_not_written_as_one(tmp_path):
    flac = _write(
        _flac_stub(), tmp_path, "t.flac", title="T", artists=["A"], albumartist=["A"], tracknumber=7, totaltrack=0
    )
    assert flac.tags["TRACKNUMBER"] == ["7"]
    assert "TRACKTOTAL" not in flac.tags, "'7 of 1' is a claim the album summary never made"


def test_a_known_track_count_is_still_written(tmp_path):
    flac = _write(
        _flac_stub(), tmp_path, "t.flac", title="T", artists=["A"], albumartist=["A"], tracknumber=7, totaltrack=12
    )
    assert flac.tags["TRACKTOTAL"] == ["12"]


def test_the_mp4_track_pair_spells_an_unknown_total_as_zero(tmp_path):
    mp4 = _write(
        _mp4_stub(), tmp_path, "t.m4a", title="T", artists=["A"], albumartist=["A"], tracknumber=7, totaltrack=0
    )
    assert mp4.tags["trkn"] == [[7, 0]], "0 is how MP4 spells an unknown total, 1 would be a lie"


class _RecMeta:
    """Records the Metadata construction the tag writer performs."""

    last = None

    def __init__(self, **kw):
        type(self).last = self
        self.kw = kw

    def save(self):
        return True


def _album(num_tracks):
    return SimpleNamespace(
        name="Album",
        num_tracks=num_tracks,
        num_volumes=None,
        available_release_date=None,
        release_date=None,
        type="ALBUM",
        upc="",
        artists=[SimpleNamespace(id=4676988, name="A", roles=None)],
        image=lambda size: "",
    )


def _track(album):
    # A real Track: the album-artist helper branches on the type.
    track = Track.__new__(Track)
    track.name = "Song"
    track.album = album
    track.artists = [
        SimpleNamespace(id=4676988, name="A", roles=None),
        SimpleNamespace(id=77, name="Guest", roles=None),
    ]
    track.artist = SimpleNamespace(id=4676988, name="A", roles=None)
    track.track_num = 7
    track.volume_num = 1
    track.explicit = False
    track.isrc = ""
    track.copyright = ""
    track.share_url = ""
    track.id = 1
    return track


def _download() -> Download:
    dl = Download(
        tidal_obj=MagicMock(),
        skip_existing=False,
        path_base="./tmp",
        fn_logger=MagicMock(),
        progress=MagicMock(),
    )
    dl.settings = MagicMock()
    dl.settings.data.lyrics_embed = False
    dl.settings.data.lyrics_file = False
    dl.settings.data.metadata_cover_embed = False
    dl.settings.data.cover_album_file = False
    dl.settings.data.cover_single_track_file = False
    dl.settings.data.metadata_write_url = False
    dl.settings.data.metadata_replay_gain = False
    dl.settings.data.mark_explicit = False
    dl.settings.data.metadata_target_upc = "UPC"
    dl.event_abort = threading.Event()
    dl.event_run = threading.Event()
    dl.event_run.set()
    return dl


@pytest.mark.parametrize(("num_tracks", "expected"), [(None, 0), (0, 0), (12, 12)])
def test_the_writer_passes_the_count_it_has_and_nothing_more(num_tracks, expected):
    """The delisted-album fallback keeps the track's own album summary, whose
    JSON has no numberOfTracks, so tidalapi leaves num_tracks None."""
    dl = _download()
    stream = SimpleNamespace(
        album_replay_gain=None, album_peak_amplitude=None, track_replay_gain=None, track_peak_amplitude=None
    )
    with patch("waves.download.Metadata", _RecMeta):
        dl.metadata_write(_track(_album(num_tracks)), pathlib.Path("t.flac"), True, stream)
    assert _RecMeta.last.kw["totaltrack"] == expected
    assert _RecMeta.last.kw["tracknumber"] == 7
    # The track call site really passes the artist ids: without this the whole
    # wiring half of the same-name-artist tag could be reverted and stay green.
    assert _RecMeta.last.kw["artist_ids"] == ["4676988", "77"]
    assert _RecMeta.last.kw["album_artist_ids"] == ["4676988"]


# --------------------------------------------------------------------------- #
# G-23: empty MP4 freeform atoms
# --------------------------------------------------------------------------- #
def test_an_m4a_carries_no_blank_custom_fields(tmp_path):
    """Most downloads have no initial key and no UPC, and lyrics are off by
    default: those atoms were written as b"" and shown as blank rows in every
    tag editor."""
    mp4 = _write(
        _mp4_stub(),
        tmp_path,
        "t.m4a",
        title="T",
        artists=["A"],
        albumartist=["A"],
        tracknumber=1,
        totaltrack=1,
        lyrics_unsynced="",
        initial_key="",
        upc="",
        release_type="",
    )
    for atom in (
        "----:com.apple.iTunes:UNSYNCEDLYRICS",
        "----:com.apple.iTunes:UPC",
        "----:com.apple.iTunes:initialkey",
        "----:com.apple.iTunes:MusicBrainz Album Type",
    ):
        assert atom not in mp4.tags, f"empty freeform atom survived the sweep: {atom!r}"
    # The sweep must not take real data with it.
    assert mp4.tags["\xa9nam"] == "T"
    assert mp4.tags["trkn"] == [[1, 1]]
    assert mp4.tags["rtng"] == [0]


def test_a_filled_custom_field_is_left_alone(tmp_path):
    mp4 = _write(
        _mp4_stub(),
        tmp_path,
        "t.m4a",
        title="T",
        artists=["A"],
        albumartist=["A"],
        initial_key="8A",
        upc="123",
        release_type="album",
    )
    assert mp4.tags["----:com.apple.iTunes:initialkey"] == b"8A"
    assert mp4.tags["----:com.apple.iTunes:UPC"] == b"123"


def test_the_empty_check_knows_both_spellings():
    # FLAC hands back [""], MP4 freeform hands back b"", plain MP4 atoms "".
    for empty in ("", [""], b"", [b""]):
        assert Metadata._is_empty_tag(empty) is True
    for kept in ("x", ["x"], b"x", [b"x"], [0], [[1, 2]], [1], []):
        assert Metadata._is_empty_tag(kept) is False


# --------------------------------------------------------------------------- #
# G-22: the dead MP3 branch, made correct rather than left as a landmine
# --------------------------------------------------------------------------- #
def _mp3_tags(tmp_path, **kw):
    stub = _write(_mp3_stub(), tmp_path, "t.mp3", title="T", artists=["A"], albumartist=["Band"], **kw)
    return stub.tags


def test_the_mp3_album_artist_goes_into_the_album_artist_frame(tmp_path):
    tags = _mp3_tags(tmp_path)
    assert [str(t) for t in tags.getall("TPE2")] == ["Band"]
    assert tags.getall("TOPE") == [], "TOPE is Original Artist, not the album artist"


def test_the_mp3_share_url_frame_carries_a_url(tmp_path):
    tags = _mp3_tags(tmp_path, url_share="https://tidal.com/browse/track/1", isrc="USRC12345678")
    assert tags.getall("WOAS")[0].url == "https://tidal.com/browse/track/1"
    assert [str(t) for t in tags.getall("TSRC")] == ["USRC12345678"]  # the ISRC's own frame


def test_synced_lyrics_do_not_abort_the_mp3_save(tmp_path):
    tags = _mp3_tags(tmp_path, lyrics="[00:12.00]a line")
    frame = tags.getall("SYLT")[0]
    assert frame.text == [("[00:12.00]a line", 0)]
    frame._writeData()  # the old plain-string shape raised here, killing the save


def test_an_mp3_with_no_synced_lyrics_writes_no_empty_frame(tmp_path):
    tags = _mp3_tags(tmp_path, lyrics="")
    assert tags.getall("SYLT") == []


def test_the_mp3_cover_names_its_mime_type(tmp_path):
    tags = _mp3_tags(tmp_path, cover_data=b"\xff\xd8\xff")
    apic = tags.getall("APIC")[0]
    assert apic.mime == "image/jpeg"
    assert apic.type == mutagen.id3.PictureType.COVER_FRONT
