"""Written-file findings from the final audit of 2026-09-24.

* C26: an album m3u listed every copy in the folder, so a folder holding a
  stereo FLAC beside its Atmos .m4a (or an old .m4a beside its FLAC upgrade)
  played every song twice. Another copy of a file this run landed, told by
  its item id or by its stem, is dropped; a file this run cannot account
  for stays, as before.
* C47: a best-of-both member borrowed from another edition streams under its
  source track id, so the stream's album gain belongs to the source album.
  That file now carries no album gain or peak; track gain stays.
* C49: FLAC (and MP3) carry the explicit flag as ITUNESADVISORY, so a FLAC
  says what the same track's M4A says through rtng.
* C50: the MP4 ISRC lives in the freeform atom mainstream readers map, with
  the bare atom kept for old readers.
* L28: a tag block that exists but is empty is not created again (add_tags
  raised on it, which failed the track).
* L29: an empty artist list is an empty tag, not a bare atom with no data.
* L30: an unknown disc count is 0 and written nowhere, the way an unknown
  track count already was.
* L31: a mutagen read error on a complete video no longer fails the video.
"""

from __future__ import annotations

import pathlib
import struct
import threading
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import mutagen
import mutagen.flac
import mutagen.mp3
import mutagen.mp4
from mutagen import MutagenError
from tidalapi import Track

from waves.download import Download
from waves.metadata import ITEM_ID_TAG, Metadata, read_item_id

TARGET_UPC = {"FLAC": "UPC", "MP4": "UPC", "MP3": "UPC"}


# --------------------------------------------------------------------------- #
# Fixtures: real, tiny FLAC files and no-disk container stubs
# --------------------------------------------------------------------------- #
def _real_flac(path: pathlib.Path, item_id: str = "") -> pathlib.Path:
    """A real (silent, zero-length) FLAC mutagen can open, tag and save."""
    stream_info = (
        struct.pack(">HH", 4096, 4096)
        + b"\x00\x00\x00" * 2
        + bytes([0x0A, 0xC4, 0x42, 0xF0, 0x00, 0x00, 0x00, 0x00])
        + b"\x00" * 16
    )
    path.write_bytes(b"fLaC" + bytes([0x80, 0, 0, 34]) + stream_info)
    if item_id:
        m = mutagen.File(path)
        m.add_tags()
        m.tags[ITEM_ID_TAG] = item_id
        m.save()
    return path


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
        assert Metadata(path_file=file, target_upc=TARGET_UPC, **kw).save() is True
    return stub


def _download(tmp_path: pathlib.Path) -> Download:
    dl = Download(
        tidal_obj=MagicMock(),
        skip_existing=False,
        path_base=str(tmp_path),
        fn_logger=MagicMock(),
        progress=MagicMock(),
    )
    dl.settings = MagicMock()
    dl.settings.data.filename_illegal_replacement = ""
    dl.settings.data.filename_illegal_map = None
    dl.settings.data.lyrics_embed = False
    dl.settings.data.lyrics_file = False
    dl.settings.data.metadata_cover_embed = False
    dl.settings.data.cover_album_file = False
    dl.settings.data.cover_single_track_file = False
    dl.settings.data.metadata_write_url = False
    dl.settings.data.metadata_replay_gain = True
    dl.settings.data.mark_explicit = False
    dl.settings.data.metadata_target_upc = "UPC"
    dl.event_abort = threading.Event()
    dl.event_run = threading.Event()
    dl.event_run.set()
    return dl


def _album(num_volumes=None):
    return SimpleNamespace(
        name="Album",
        num_tracks=12,
        num_volumes=num_volumes,
        available_release_date=None,
        release_date=None,
        type="ALBUM",
        upc="",
        artists=[SimpleNamespace(id=4676988, name="A", roles=None)],
        image=lambda size: "",
    )


def _track(album, **over):
    track = Track.__new__(Track)
    track.name = "Song"
    track.album = album
    track.artists = [SimpleNamespace(id=4676988, name="A", roles=None)]
    track.artist = SimpleNamespace(id=4676988, name="A", roles=None)
    track.track_num = 7
    track.volume_num = 2
    track.explicit = False
    track.isrc = ""
    track.copyright = ""
    track.share_url = ""
    track.id = 1
    for key, value in over.items():
        setattr(track, key, value)
    return track


class _RecMeta:
    last = None

    def __init__(self, **kw):
        type(self).last = self
        self.kw = kw

    def save(self):
        return True


def _stream():
    return SimpleNamespace(
        album_replay_gain=-7.36, album_peak_amplitude=0.98, track_replay_gain=-5.1, track_peak_amplitude=0.9
    )


def _m3u_lines(dl: Download, folder: pathlib.Path, paths_ordered) -> list[str]:
    written = dl.playlist_populate(
        {folder}, "Album", is_album=True, sort_alphabetically=True, paths_ordered=paths_ordered
    )
    return written[0].read_text(encoding="utf-8").splitlines()


# --------------------------------------------------------------------------- #
# C26: one file per track in the album m3u
# --------------------------------------------------------------------------- #
def test_the_old_format_copy_of_a_landed_track_is_not_listed_twice(tmp_path):
    """A 320k .m4a album upgraded to FLAC: the old files are never deleted
    (they carry no item id, the release predates the tag), so only the stem
    can tell they are the same songs."""
    dl = _download(tmp_path)
    landed = [_real_flac(tmp_path / "01 Intro.flac", "11"), _real_flac(tmp_path / "02 Song.flac", "12")]
    (tmp_path / "01 Intro.m4a").write_bytes(b"old")
    (tmp_path / "02 Song.m4a").write_bytes(b"old")

    assert _m3u_lines(dl, tmp_path, landed) == ["01 Intro.flac", "02 Song.flac"]


def test_a_second_copy_carrying_the_same_item_id_is_not_listed_twice(tmp_path):
    """The same track under another name (a numbered sibling) with the same
    item id is the same song, whatever it is called."""
    dl = _download(tmp_path)
    landed = [_real_flac(tmp_path / "01 Intro.flac", "11"), _real_flac(tmp_path / "02 Song.flac", "12")]
    _real_flac(tmp_path / "02 Song (1).flac", "12")

    assert _m3u_lines(dl, tmp_path, landed) == ["01 Intro.flac", "02 Song.flac"]


def test_a_file_this_run_cannot_account_for_still_stays_in_the_list(tmp_path):
    """A skipped or failed track is not a copy of anything landed: the folder
    listing stands, minus the real duplicate only."""
    dl = _download(tmp_path)
    landed = [_real_flac(tmp_path / "01 Intro.flac", "11")]
    _real_flac(tmp_path / "02 Song.flac", "12")
    _real_flac(tmp_path / "03 Outro.flac", "13")
    (tmp_path / "01 Intro.m4a").write_bytes(b"old")

    assert _m3u_lines(dl, tmp_path, landed) == ["01 Intro.flac", "02 Song.flac", "03 Outro.flac"]


def test_a_different_track_with_its_own_id_and_name_is_kept(tmp_path):
    dl = _download(tmp_path)
    landed = [_real_flac(tmp_path / "01 Intro.flac", "11")]
    _real_flac(tmp_path / "02 Song.flac", "12")

    assert _m3u_lines(dl, tmp_path, landed) == ["01 Intro.flac", "02 Song.flac"]


def test_without_a_landed_list_the_folder_listing_is_untouched(tmp_path):
    """The dedup is tied to this run's landed files: with no order handed
    over, the m3u is the folder, as it always was."""
    dl = _download(tmp_path)
    _real_flac(tmp_path / "01 Intro.flac", "11")
    (tmp_path / "01 Intro.m4a").write_bytes(b"old")

    assert _m3u_lines(dl, tmp_path, None) == ["01 Intro.flac", "01 Intro.m4a"]


def test_the_real_flac_fixture_carries_its_item_id(tmp_path):
    assert read_item_id(_real_flac(tmp_path / "t.flac", "42")) == "42"
    assert read_item_id(_real_flac(tmp_path / "u.flac")) == ""


# --------------------------------------------------------------------------- #
# C47: a borrowed best-of-both slot carries no foreign album gain
# --------------------------------------------------------------------------- #
def test_a_borrowed_slot_leaves_album_gain_and_peak_unwritten(tmp_path):
    dl = _download(tmp_path)
    member = _track(_album(2), waves_identity_id="900")
    with patch("waves.download.Metadata", _RecMeta):
        dl.metadata_write(member, pathlib.Path("t.flac"), True, _stream())
    kw = _RecMeta.last.kw
    assert kw["album_replay_gain"] is None and kw["album_peak_amplitude"] is None
    assert kw["track_replay_gain"] == -5.1 and kw["track_peak_amplitude"] == 0.9


def test_an_ordinary_track_still_gets_the_album_gain(tmp_path):
    dl = _download(tmp_path)
    with patch("waves.download.Metadata", _RecMeta):
        dl.metadata_write(_track(_album(2)), pathlib.Path("t.flac"), True, _stream())
    kw = _RecMeta.last.kw
    assert kw["album_replay_gain"] == -7.36 and kw["album_peak_amplitude"] == 0.98


def test_a_missing_album_gain_writes_no_album_tag_but_keeps_the_track_tag(tmp_path):
    flac = _write(
        _flac_stub(),
        tmp_path,
        "t.flac",
        title="T",
        artists=["A"],
        albumartist=["A"],
        replay_gain_write=True,
        album_replay_gain=None,
        album_peak_amplitude=None,
        track_replay_gain=-5.1,
        track_peak_amplitude=0.9,
    )
    assert "REPLAYGAIN_ALBUM_GAIN" not in flac.tags and "REPLAYGAIN_ALBUM_PEAK" not in flac.tags
    assert flac.tags["REPLAYGAIN_TRACK_GAIN"] == ["-5.10 dB"]


# --------------------------------------------------------------------------- #
# C49: the explicit flag in every container
# --------------------------------------------------------------------------- #
def test_an_explicit_flac_carries_itunesadvisory(tmp_path):
    flac = _write(_flac_stub(), tmp_path, "t.flac", title="T", artists=["A"], albumartist=["A"], explicit=True)
    assert flac.tags["ITUNESADVISORY"] == ["1"]


def test_a_clean_flac_says_so_too(tmp_path):
    flac = _write(_flac_stub(), tmp_path, "t.flac", title="T", artists=["A"], albumartist=["A"], explicit=False)
    assert flac.tags["ITUNESADVISORY"] == ["0"]


def test_an_explicit_mp3_carries_itunesadvisory(tmp_path):
    mp3 = _write(_mp3_stub(), tmp_path, "t.mp3", title="T", artists=["A"], albumartist=["A"], explicit=True)
    assert mp3.tags["TXXX:ITUNESADVISORY"].text == ["1"]


def test_the_m4a_rating_atom_is_unchanged(tmp_path):
    mp4 = _write(_mp4_stub(), tmp_path, "t.m4a", title="T", artists=["A"], albumartist=["A"], explicit=True)
    assert mp4.tags["rtng"] == [1]


# --------------------------------------------------------------------------- #
# C50: the MP4 ISRC in the atom readers map
# --------------------------------------------------------------------------- #
def test_the_m4a_isrc_lives_in_the_freeform_atom(tmp_path):
    mp4 = _write(_mp4_stub(), tmp_path, "t.m4a", title="T", artists=["A"], albumartist=["A"], isrc="GBUM71029604")
    assert mp4.tags["----:com.apple.iTunes:ISRC"] == b"GBUM71029604"
    assert mp4.tags["isrc"] == "GBUM71029604", "the old spelling stays for readers that learnt it"


def test_a_missing_isrc_writes_neither_atom(tmp_path):
    mp4 = _write(_mp4_stub(), tmp_path, "t.m4a", title="T", artists=["A"], albumartist=["A"], isrc="")
    assert "----:com.apple.iTunes:ISRC" not in mp4.tags and "isrc" not in mp4.tags


# --------------------------------------------------------------------------- #
# L28: an existing, empty tag block
# --------------------------------------------------------------------------- #
def test_a_flac_whose_comment_block_is_empty_is_tagged_not_failed(tmp_path):
    file = _real_flac(tmp_path / "t.flac")
    m = mutagen.File(file)
    m.add_tags()
    m.save()
    assert mutagen.File(file).tags is not None and len(mutagen.File(file).tags) == 0

    assert Metadata(path_file=file, target_upc=TARGET_UPC, title="T", artists=["A"], albumartist=["A"]).save() is True
    assert mutagen.File(file).tags["TITLE"] == ["T"]


def test_an_m4a_whose_tag_block_is_empty_is_tagged_not_failed(tmp_path):
    stub = _mp4_stub()
    stub.tags = mutagen.mp4.MP4Tags()
    mp4 = _write(stub, tmp_path, "t.m4a", title="T", artists=["A"], albumartist=["A"])
    assert mp4.tags["\xa9nam"] == "T"


# --------------------------------------------------------------------------- #
# L29: an empty artist list is an empty tag
# --------------------------------------------------------------------------- #
def test_an_empty_album_artist_list_writes_no_atom(tmp_path):
    mp4 = _write(_mp4_stub(), tmp_path, "t.m4a", title="T", artists=["A"], albumartist=[])
    assert "aART" not in mp4.tags
    assert mp4.tags["\xa9ART"] == ["A"]


def test_a_video_with_no_credits_writes_no_artist_atoms(tmp_path):
    mp4 = _write(_mp4_stub(), tmp_path, "v.mp4", title="T", artists=[], albumartist=[], is_video=True)
    assert "aART" not in mp4.tags and "\xa9ART" not in mp4.tags
    assert mp4.tags["stik"] == [6]


def test_the_sweep_treats_an_empty_list_as_empty():
    assert Metadata._is_empty_tag([]) is True
    assert Metadata._is_empty_tag([""]) is True
    assert Metadata._is_empty_tag(["A"]) is False
    assert Metadata._is_empty_tag([[7, 12]]) is False, "a number pair is data"


# --------------------------------------------------------------------------- #
# L30: an unknown disc count
# --------------------------------------------------------------------------- #
def test_an_unknown_disc_count_reaches_the_writer_as_zero(tmp_path):
    dl = _download(tmp_path)
    with patch("waves.download.Metadata", _RecMeta):
        dl.metadata_write(_track(_album(num_volumes=None)), pathlib.Path("t.flac"), True, _stream())
    assert _RecMeta.last.kw["totaldisc"] == 0
    assert _RecMeta.last.kw["discnumber"] == 2


def test_a_known_disc_count_still_reaches_the_writer(tmp_path):
    dl = _download(tmp_path)
    with patch("waves.download.Metadata", _RecMeta):
        dl.metadata_write(_track(_album(num_volumes=3)), pathlib.Path("t.flac"), True, _stream())
    assert _RecMeta.last.kw["totaldisc"] == 3


def test_an_unknown_disc_count_is_not_written_as_one(tmp_path):
    flac = _write(
        _flac_stub(), tmp_path, "t.flac", title="T", artists=["A"], albumartist=["A"], discnumber=2, totaldisc=0
    )
    assert flac.tags["DISCNUMBER"] == ["2"]
    assert "DISCTOTAL" not in flac.tags, "'2 of 1' is a claim the album summary never made"


def test_a_known_disc_count_is_still_written(tmp_path):
    flac = _write(
        _flac_stub(), tmp_path, "t.flac", title="T", artists=["A"], albumartist=["A"], discnumber=2, totaldisc=3
    )
    assert flac.tags["DISCTOTAL"] == ["3"]


def test_the_mp4_disc_pair_spells_an_unknown_total_as_zero(tmp_path):
    mp4 = _write(_mp4_stub(), tmp_path, "t.m4a", title="T", artists=["A"], albumartist=["A"], discnumber=2, totaldisc=0)
    assert mp4.tags["disk"] == [[2, 0]]


# --------------------------------------------------------------------------- #
# L31: a video the tagger cannot open is still a finished video
# --------------------------------------------------------------------------- #
def _video():
    return SimpleNamespace(
        id=1,
        name="Clip",
        artists=[SimpleNamespace(id=11, name="A")],
        artist=SimpleNamespace(id=11, name="A"),
        album=None,
        cover=None,
        explicit=False,
        release_date=None,
        share_url="",
    )


def test_a_mutagen_read_error_on_a_video_is_logged_not_raised(tmp_path):
    dl = _download(tmp_path)

    def _boom(**kw):
        raise MutagenError("malformed atom")

    with patch("waves.download.Metadata", _boom):
        assert dl.metadata_write_video(_video(), tmp_path / "v.mp4") is False
    assert dl.fn_logger.exception.called


def test_an_os_read_error_on_a_video_is_logged_not_raised(tmp_path):
    dl = _download(tmp_path)

    def _boom(**kw):
        raise OSError("transient read error")

    with patch("waves.download.Metadata", _boom):
        assert dl.metadata_write_video(_video(), tmp_path / "v.mp4") is False
