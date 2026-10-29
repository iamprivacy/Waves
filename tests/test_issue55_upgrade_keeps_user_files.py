"""Issue #55: a bulk download replaced files the user already had.

"Download All" on My Tidal tracks, with Skip existing on, re-fetched tracks
already in the folder and wrote over them, losing the Genre the user had typed
in by hand. Two steps made that happen. A track the engine skipped because its
file was already there was recorded as owned with an UNKNOWN tier, and the gate
read unknown as stale, so the next run "upgraded" it: skipping off for that one
track, replacing the file in place. And the replace wrote a freshly tagged file
over the old one, so every tag Waves does not write was gone.

Pinned here, so that neither needs a hand from the user:

- an adopted file is MEASURED (bit depth and rate, bitrate, the Atmos codec),
  so it gets a real tier: at or above the setting it settles, below it the
  upgrade still comes; only a file that cannot be read stays tier-less, and
  that reads as current rather than as a reason to replace it;
- a replace in place carries the old file's extra tags (Genre, Comment, a
  rating) onto the new copy, while the tags Waves writes keep their fresh
  value and the ones that describe the old audio (ReplayGain) stay behind.
"""

from __future__ import annotations

import pathlib
from collections import defaultdict
from threading import Lock
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import mutagen.flac
import mutagen.mp4

import waves.download as download_mod
from waves import metadata as metadata_mod
from waves.download import Download
from waves.ownership import OwnershipStore
from waves.waves_ui import backend
from waves.waves_ui.backend import WavesBridge, _copy_is_current, _measure_file_quality


# ---- measuring an adopted file --------------------------------------------------
def _file(suffix: str, **info):
    """A mutagen stand-in: the object mutagen.File answers for a path."""
    return SimpleNamespace(info=SimpleNamespace(**info), suffix=suffix)


def _measure(suffix: str, stand_in) -> dict:
    with patch("mutagen.File", return_value=stand_in):
        return _measure_file_quality(f"/music/song{suffix}")


def test_a_hi_res_flac_measures_hi_res():
    got = _measure(".flac", _file(".flac", bits_per_sample=24, sample_rate=96000, bitrate=2_800_000))
    assert got["tier"] == "HI_RES_LOSSLESS" and got["bit_depth"] == 24 and got["sample_rate"] == 96000
    assert got["audio_mode"] is None


def test_a_cd_flac_measures_lossless():
    assert (
        _measure(".flac", _file(".flac", bits_per_sample=16, sample_rate=44100, bitrate=900_000))["tier"] == "LOSSLESS"
    )


def test_an_aac_file_measures_by_bitrate():
    assert _measure(".m4a", _file(".m4a", codec="mp4a.40.2", bitrate=320_000, sample_rate=44100))["tier"] == "HIGH"
    assert _measure(".m4a", _file(".m4a", codec="mp4a.40.2", bitrate=96_000, sample_rate=44100))["tier"] == "LOW"


def test_an_atmos_file_measures_as_atmos_at_the_fixed_tier():
    got = _measure(".m4a", _file(".m4a", codec="ec-3", bitrate=768_000, sample_rate=48000))
    assert got["audio_mode"] == backend._ATMOS_MODE and got["tier"] == "HIGH"


def test_an_unreadable_file_measures_nothing():
    with patch("mutagen.File", return_value=None):
        assert _measure_file_quality("/music/song.flac") == {"tier": None}
    with patch("mutagen.File", side_effect=OSError):
        assert _measure_file_quality("/music/song.flac") == {"tier": None}


# ---- the gate: a measured copy upgrades, an unmeasurable one never does ----------
def test_an_unmeasurable_copy_is_current_at_every_target():
    rec = {"path": "/music/song.flac", "quality_rank": -1, "quality_tier": None}
    for target in range(4):
        assert _copy_is_current(rec, target, False) is True


def test_a_measured_copy_below_the_setting_still_upgrades():
    rec = {"path": "/music/song.flac", "quality_tier": "LOSSLESS", "quality_rank": 2}
    assert _copy_is_current(rec, 3, False) is False
    assert _copy_is_current(rec, 2, False) is True


# ---- the store: adopt keeps what was measured, never over a fetched row --------
def test_adopt_records_the_measured_tier(tmp_path):
    store = OwnershipStore(str(tmp_path / "own.db"))
    song = tmp_path / "song.flac"
    song.write_bytes(b"\0")
    assert store.adopt("7", str(song), "LOSSLESS", bit_depth=16, sample_rate=44100, codecs="flac") is True
    rec = store.ownership_of("7")
    assert rec["quality_tier"] == "LOSSLESS" and rec["quality_rank"] == 2 and rec["bit_depth"] == 16
    assert store.adopt("7", str(song), "LOW") is False
    assert store.ownership_of("7")["quality_tier"] == "LOSSLESS"


class _Signal:
    def __init__(self):
        self.calls: list = []

    def emit(self, *a):
        self.calls.append(a)


def _recorder(store):
    stub = SimpleNamespace(
        settings=SimpleNamespace(data=SimpleNamespace(symlink_to_track=False)),
        _ownership=store,
        _own_lock=Lock(),
        _own_cache={},
        ownershipChanged=_Signal(),
        _downloadRecorded=_Signal(),
    )
    stub._evict_own_cache_locked = lambda: None
    stub._record_ownership = WavesBridge._record_ownership.__get__(stub, type(stub))
    return stub


def test_the_recorder_measures_an_adopted_file(tmp_path, monkeypatch):
    store = OwnershipStore(str(tmp_path / "own.db"))
    song = tmp_path / "song.flac"
    song.write_bytes(b"\0")
    measured = {"tier": "HI_RES_LOSSLESS", "audio_mode": None, "bit_depth": 24, "sample_rate": 96000, "codecs": "flac"}
    seen: list[str] = []

    def _measure_at(path):
        seen.append(path)
        return dict(measured)

    monkeypatch.setattr(backend, "_measure_file_quality", _measure_at)
    stub = _recorder(store)
    stub._record_ownership(
        {
            "id": "8",
            "path": str(song),
            "quality": {"tier": None, "requested_rank": -1, "ceiling_rank": -1, "adopted": True},
        }
    )
    assert seen == [str(song)]
    rec = store.ownership_of("8")
    assert rec["quality_tier"] == "HI_RES_LOSSLESS" and rec["quality_rank"] == 3 and rec["sample_rate"] == 96000
    assert stub._own_cache["8"][1]["quality_tier"] == "HI_RES_LOSSLESS", "the button learns the measured tier at once"


# ---- carrying the old file's tags onto the replacement ---------------------------
def _flac_with(**tags):
    file = mutagen.flac.FLAC.__new__(mutagen.flac.FLAC)
    file.tags = None
    file.metadata_blocks = []
    file.add_tags()
    for key, value in tags.items():
        file.tags[key] = value
    file.save = MagicMock()
    return file


def _mp4_with(**tags):
    file = mutagen.mp4.MP4.__new__(mutagen.mp4.MP4)
    file.tags = mutagen.mp4.MP4Tags()
    for key, value in tags.items():
        file.tags[key] = value
    file.save = MagicMock()
    return file


def _carry(old, new) -> int:
    with patch("waves.metadata.mutagen.File", side_effect=[old, new]):
        return metadata_mod.carry_over_tags("/music/old.flac", "/tmp/new.flac")


def test_the_genre_the_user_typed_rides_over_and_the_fresh_title_stays():
    old = _flac_with(TITLE="Song (old spelling)", GENRE="Jazz", COMMENT="bought 2019", REPLAYGAIN_TRACK_GAIN="-3.1 dB")
    new = _flac_with(TITLE="Song", ARTIST="Artist")

    assert _carry(old, new) == 2
    assert new.tags["GENRE"] == ["Jazz"] and new.tags["COMMENT"] == ["bought 2019"]
    assert new.tags["TITLE"] == ["Song"], "a tag Waves writes keeps its fresh value"
    assert "REPLAYGAIN_TRACK_GAIN" not in new.tags, "loudness describes the old bytes, not the new"
    new.save.assert_called_once()


def test_the_users_genre_outranks_a_looked_up_one():
    # Genre from MusicBrainz is on, so the fresh copy has a Genre of its own;
    # the one the user typed (or their tagger wrote) still wins.
    old = _flac_with(TITLE="Song", GENRE="Jazz")
    new = _flac_with(TITLE="Song", GENRE="Electronic; House")

    assert _carry(old, new) == 1
    assert new.tags["GENRE"] == ["Jazz"]


def test_an_mp4_genre_outranks_a_looked_up_one_too():
    old = _mp4_with(**{"\xa9gen": ["Jazz"]})
    new = _mp4_with(**{"\xa9gen": ["Electronic"]})

    assert _carry(old, new) == 1
    assert new.tags["\xa9gen"] == ["Jazz"]


def test_the_same_genre_on_both_saves_nothing():
    old = _flac_with(TITLE="Song", GENRE="Jazz")
    new = _flac_with(TITLE="Song", GENRE="Jazz")

    assert _carry(old, new) == 0
    new.save.assert_not_called()


def test_mp4_freeform_tags_ride_over_but_the_itunes_loudness_stays():
    mood, mbid = "----:com.apple.iTunes:MOOD", "----:com.apple.iTunes:MusicBrainz Album Id"
    norm = "----:com.apple.iTunes:iTunNORM"
    old = _mp4_with(**{mood: [b"Calm"], mbid: [b"aa997ea0"], norm: [b" 000003E8"]})
    new = _mp4_with(**{"\xa9nam": ["Song"]})

    assert _carry(old, new) == 2
    assert new.tags[mood] == [b"Calm"] and new.tags[mbid] == [b"aa997ea0"]
    assert norm not in new.tags, "iTunNORM describes the old bytes"


def test_nothing_to_carry_saves_nothing():
    old = _flac_with(TITLE="Song")
    new = _flac_with(TITLE="Song")

    assert _carry(old, new) == 0
    new.save.assert_not_called()


def test_an_mp4_genre_rides_over_too():
    old = _mp4_with(**{"\xa9nam": ["Song"], "\xa9gen": ["House"], "\xa9too": ["Lavf"]})
    new = _mp4_with(**{"\xa9nam": ["Song"]})

    assert _carry(old, new) == 1
    assert new.tags["\xa9gen"] == ["House"] and "\xa9too" not in new.tags


def test_different_containers_carry_nothing():
    assert _carry(_mp4_with(**{"\xa9gen": ["House"]}), _flac_with(TITLE="Song")) == 0


def test_an_unreadable_old_copy_carries_nothing():
    with patch("waves.metadata.mutagen.File", side_effect=[None, _flac_with()]):
        assert metadata_mod.carry_over_tags("/music/old.flac", "/tmp/new.flac") == 0


# ---- the engine asks for the carry-over exactly when it lands on a copy ----------
def _make_download(tmp_path: pathlib.Path, *, skip_existing: bool) -> Download:
    dl = Download(
        tidal_obj=MagicMock(),
        skip_existing=skip_existing,
        path_base=str(tmp_path),
        fn_logger=MagicMock(),
        progress=MagicMock(),
    )
    dl.settings = MagicMock()
    dl.settings.data.extract_flac = False
    dl.settings.data.downsample_enabled = False
    dl.settings.data.path_binary_ffmpeg = ""
    dl.event_abort = __import__("threading").Event()
    dl.event_run = __import__("threading").Event()
    dl.event_run.set()
    return dl


def _run(dl: Download, dst: pathlib.Path, carried: list) -> tuple[bool, pathlib.Path]:
    media = download_mod.Track.__new__(download_mod.Track)
    media.id = "123"
    media.name = "Song"
    media.artist = SimpleNamespace(name="Artist")
    media.artists = []
    media.duration = 200
    media.audio_modes = []
    media.media_metadata_tags = []

    def _fake_download(self, *, media, stream_manifest, path_file, event_stop=None, **kw):
        path_file.write_bytes(b"fresh bytes")
        return True, path_file

    def _carry(path_old, path_new):
        carried.append((pathlib.Path(path_old), pathlib.Path(path_new).read_bytes()))
        return 1

    with (
        patch.object(Download, "_download", _fake_download),
        patch.object(Download, "_finalize_plan", lambda *a, **k: defaultdict(float)),
        patch.object(Download, "_note_stage", lambda *a, **k: None),
        patch.object(Download, "_handle_metadata_and_extras", lambda *a, **k: None),
        patch.object(download_mod, "carry_over_tags", _carry),
        patch.object(download_mod, "read_item_id", lambda p: ""),
        patch.object(download_mod, "_file_audio_mode_is_atmos", lambda p: None),
    ):
        return dl._perform_actual_download(
            media=media,
            path_media_dst=dst,
            stream_manifest=None,
            do_flac_extract=False,
            is_parent_album=False,
            media_stream=None,
        )


def test_a_replace_in_place_carries_the_old_tags_before_the_move(tmp_path):
    dst = tmp_path / "Artist - Song.flac"
    dst.write_bytes(b"the user's hand-tagged copy")
    carried: list = []

    ok, path = _run(_make_download(tmp_path, skip_existing=False), dst, carried)

    assert ok and path == dst
    assert carried == [(dst, b"fresh bytes")], "asked once, on the old file and the tagged temp, before the move"
    assert dst.read_bytes() == b"fresh bytes"


def test_a_fresh_destination_asks_for_no_carry_over(tmp_path):
    dst = tmp_path / "Artist - Song.flac"
    carried: list = []

    ok, path = _run(_make_download(tmp_path, skip_existing=False), dst, carried)

    assert ok and path == dst and carried == []
