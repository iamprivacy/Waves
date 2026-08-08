"""Downloaded music videos carry real metadata.

The tagging step used to return early for every ``Video``, so a downloaded
video's only metadata was its filename: no title, no artist, no release
year. Converted MP4s now get the music-video tag set (``set_mp4_video``):
title, artists, release date, explicit rating, thumbnail cover and the
iTunes media-kind atom (``stik`` = 6, music video) so players and library
managers file them correctly. Raw ``.ts`` files (conversion off) stay
untouched; MPEG-TS has no tag atoms mutagen can write.
"""

from __future__ import annotations

import pathlib
import threading
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

# mutagen resolves its per-format submodules lazily via mutagen.File; with
# File patched out, metadata.py's isinstance checks need them pre-imported.
import mutagen.mp3
import mutagen.mp4
from tidalapi import Video

from tidaler.download import Download
from tidaler.metadata import Metadata


def _mp4_stub():
    """A real mutagen MP4 instance that never touches disk: tags start empty
    (``save`` creates them) and saving is a no-op."""
    fake = mutagen.mp4.MP4.__new__(mutagen.mp4.MP4)
    fake.tags = None
    fake.save = lambda *a, **k: None
    return fake


def _make_download() -> Download:
    dl = Download(
        tidal_obj=MagicMock(),
        skip_existing=False,
        path_base="./tmp",
        fn_logger=MagicMock(),
        progress=MagicMock(),
    )
    dl.settings = MagicMock()
    dl.event_abort = threading.Event()
    dl.event_run = threading.Event()
    dl.event_run.set()
    return dl


def _video(**over) -> Video:
    v = Video.__new__(Video)
    v.id = 1
    v.name = "Let The Good Times Roll"
    v.artists = [SimpleNamespace(name="Electric Callboy"), SimpleNamespace(name="The Offspring")]
    v.artist = SimpleNamespace(name="Electric Callboy")
    v.album = None
    v.cover = None
    v.explicit = True
    v.release_date = datetime(2026, 6, 6)
    v.share_url = "https://tidal.com/browse/video/1"
    for key, value in over.items():
        setattr(v, key, value)
    return v


def test_video_tags_carry_title_artists_year_and_media_kind(tmp_path):
    fake = _mp4_stub()
    file = tmp_path / "v.mp4"
    file.write_bytes(b"x")
    with patch("tidaler.metadata.mutagen.File", return_value=fake):
        m = Metadata(
            path_file=file,
            target_upc={"MP4": "UPC"},
            title="Let The Good Times Roll 🅴",
            artists=["Electric Callboy", "The Offspring"],
            albumartist=["Electric Callboy"],
            date="2026-06-06",
            explicit=True,
            replay_gain_write=False,
            is_video=True,
        )
        assert m.save() is True
    assert fake.tags["\xa9nam"] == "Let The Good Times Roll 🅴"
    assert fake.tags["\xa9day"] == "2026-06-06"
    assert fake.tags["\xa9ART"] == ["Electric Callboy", "The Offspring"]
    assert fake.tags["aART"] == ["Electric Callboy"]
    assert fake.tags["rtng"] == [1]
    assert fake.tags["stik"] == [6], "players file the download as a music video via stik"


def test_video_tags_skip_the_album_structure_atoms(tmp_path):
    """No track/disc numbers, lyrics or empty leftovers: zeroed atoms are
    noise readers take literally ("track 0 of 0")."""
    fake = _mp4_stub()
    file = tmp_path / "v.mp4"
    file.write_bytes(b"x")
    with patch("tidaler.metadata.mutagen.File", return_value=fake):
        Metadata(
            path_file=file,
            target_upc={"MP4": "UPC"},
            title="T",
            artists=["A"],
            albumartist=["A"],
            is_video=True,
        ).save()
    for atom in ("trkn", "disk", "\xa9lyr", "isrc", "\xa9alb", "\xa9day", "\xa9url"):
        assert atom not in fake.tags, f"unexpected atom on a video: {atom!r}"


def test_audio_tagging_is_unchanged_by_the_video_mode(tmp_path):
    fake = _mp4_stub()
    file = tmp_path / "t.m4a"
    file.write_bytes(b"x")
    with patch("tidaler.metadata.mutagen.File", return_value=fake):
        Metadata(
            path_file=file,
            target_upc={"MP4": "UPC"},
            title="T",
            artists=["A"],
            albumartist=["A"],
            tracknumber=3,
            totaltrack=12,
        ).save()
    assert fake.tags["trkn"] == [[3, 12]]
    assert "stik" not in fake.tags


class _RecMeta:
    """Records the Metadata construction the video writer performs."""

    last = None

    def __init__(self, **kw):
        type(self).last = self
        self.kw = kw
        self.saved = False

    def save(self):
        self.saved = True
        return True


def test_metadata_write_video_maps_the_video_fields():
    dl = _make_download()
    dl.settings.data.mark_explicit = True
    dl.settings.data.metadata_cover_embed = False
    dl.settings.data.metadata_write_url = False
    dl.settings.data.metadata_target_upc = "UPC"
    with patch("tidaler.download.Metadata", _RecMeta):
        assert dl.metadata_write_video(_video(), pathlib.Path("v.mp4")) is True
    kw = _RecMeta.last.kw
    assert _RecMeta.last.saved is True
    assert kw["title"] == "Let The Good Times Roll 🅴"
    assert kw["date"] == "2026-06-06"
    assert kw["artists"] == ["Electric Callboy", "The Offspring"]
    assert kw["albumartist"] == ["Electric Callboy"]
    assert kw["explicit"] is True
    assert kw["is_video"] is True
    assert kw["replay_gain_write"] is False
    assert kw["url_share"] == "", "share URL only with the URL tag enabled"


def test_metadata_write_video_survives_missing_fields():
    """A video with no date, no credits and no thumbnail still tags cleanly."""
    dl = _make_download()
    dl.settings.data.mark_explicit = False
    dl.settings.data.metadata_cover_embed = True
    dl.settings.data.metadata_write_url = False
    dl.settings.data.metadata_target_upc = "UPC"
    bare = _video(release_date=None, artists=[], artist=None, explicit=False, cover=None)
    with patch("tidaler.download.Metadata", _RecMeta):
        assert dl.metadata_write_video(bare, pathlib.Path("v.mp4")) is True
    kw = _RecMeta.last.kw
    assert kw["date"] == "" and kw["artists"] == [] and kw["albumartist"] == []
    assert kw["cover_data"] is None


def test_converted_videos_are_tagged_and_raw_ts_is_left_alone(tmp_path):
    dl = _make_download()
    dl.metadata_write_video = MagicMock()
    video = _video()

    dl._handle_metadata_and_extras(video, tmp_path / "a.mp4", tmp_path / "out.mp4", False, None)
    dl.metadata_write_video.assert_called_once_with(video, tmp_path / "a.mp4")

    dl.metadata_write_video.reset_mock()
    # Conversion off: production hands the raw temp file, which is a bare
    # uuid with NO extension at all (download.py names it str(uuid4()); only
    # _video_convert ever adds ".mp4"), so the not-mp4 branch must hold for
    # an extensionless name, not just a ".ts" one.
    raw = tmp_path / "0d1f4c9a2b634a1c9d2e8f7a6b5c4d3e"
    dl._handle_metadata_and_extras(video, raw, tmp_path / "out.ts", False, None)
    dl.metadata_write_video.assert_not_called()
