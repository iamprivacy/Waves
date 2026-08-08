"""Issue #15 regressions: Atmos-only tracks and colliding filenames.

Bug 1: with "Download Dolby Atmos" off, an Atmos-only track (an album's
separate Atmos edition, its own track id whose only audio mode is Dolby
Atmos) was still downloaded through the normal session, delivering an AC-4
file the user asked not to have. It is now skipped outright.

Bug 2: skip_existing was filename-keyed, so distinct tracks whose sanitized
names collide (several mixes sharing one title) were skipped after the first
download. Files now carry a WAVES_TIDAL_ID tag and the skip decision compares
ids: same id skips, a different id downloads under a uniquified name, an
untagged occupant (pre-tag library) keeps the historical skip so re-fetching
an old library cannot duplicate it.
"""

import pathlib
import threading
from unittest.mock import MagicMock, patch

import mutagen.mp4
from tidalapi.media import AudioMode, Track

from tidaler.download import Download
from tidaler.metadata import ITEM_ID_TAG, Metadata, read_item_id


def _make_download(skip_existing: bool = False) -> Download:
    dl = Download(
        tidal_obj=MagicMock(),
        skip_existing=skip_existing,
        path_base="./tmp",
        fn_logger=MagicMock(),
        progress=MagicMock(),
    )
    dl.settings = MagicMock()
    dl.event_abort = threading.Event()
    dl.event_run = threading.Event()
    dl.event_run.set()
    return dl


def _track(track_id: int, audio_modes) -> Track:
    t = Track.__new__(Track)
    t.id = track_id
    t.audio_modes = audio_modes
    t.artists = []
    t.name = "Song"
    t.version = None
    return t


class TestAtmosOnlyTracksHonorTheToggle:
    def _run_item(self, dl, media):
        with (
            patch.object(dl, "_validate_and_prepare_media", return_value=media),
            patch.object(dl, "_prepare_file_paths_and_skip_logic") as prepare,
        ):
            prepare.return_value = (pathlib.Path("./tmp/x.flac"), ".flac", True, False)
            return dl.item(file_template="{track_title}", media=media)

    def test_atmos_only_track_is_skipped_when_atmos_is_off(self):
        dl = _make_download()
        dl.settings.data.download_dolby_atmos = False
        media = _track(123, [AudioMode.dolby_atmos.value])

        ok, path = self._run_item(dl, media)

        assert ok is True
        assert path == ""
        assert dl.fn_logger.info.called  # the skip is told, not silent

    def test_atmos_only_track_proceeds_when_atmos_is_on(self):
        dl = _make_download()
        dl.settings.data.download_dolby_atmos = True
        media = _track(123, [AudioMode.dolby_atmos.value])

        ok, path = self._run_item(dl, media)

        # Reaches the (stubbed) skip-existing path instead of the Atmos guard.
        assert ok is True
        assert str(path) != ""

    def test_normal_track_is_untouched_by_the_guard(self):
        dl = _make_download()
        dl.settings.data.download_dolby_atmos = False
        media = _track(123, ["STEREO"])

        ok, path = self._run_item(dl, media)

        assert ok is True
        assert str(path) != ""

    def test_stereo_and_atmos_track_still_downloads_without_atmos(self):
        # A track offering BOTH modes has a normal stream to fall back to.
        dl = _make_download()
        dl.settings.data.download_dolby_atmos = False
        media = _track(123, ["STEREO", AudioMode.dolby_atmos.value])

        ok, path = self._run_item(dl, media)

        assert ok is True
        assert str(path) != ""


class TestSkipExistingComparesItemIds:
    def test_untagged_occupant_keeps_the_historical_skip(self, tmp_path):
        dl = _make_download(skip_existing=True)
        dst = tmp_path / "Song.flac"
        dst.write_bytes(b"not really flac")

        assert dl._existing_is_same_item(dst, _track(456, [])) is True

    def test_same_id_occupant_skips(self, tmp_path):
        dl = _make_download(skip_existing=True)
        dst = tmp_path / "Song.flac"
        dst.write_bytes(b"x")

        with patch("tidaler.download.read_item_id", return_value="123"):
            assert dl._existing_is_same_item(dst, _track(123, [])) is True

    def test_different_id_occupant_downloads(self, tmp_path):
        dl = _make_download(skip_existing=True)
        dst = tmp_path / "Song.flac"
        dst.write_bytes(b"x")

        with patch("tidaler.download.read_item_id", return_value="123"):
            assert dl._existing_is_same_item(dst, _track(456, [])) is False

    def test_uniquified_sibling_with_the_id_skips(self, tmp_path):
        # Song.flac is id 123, Song_01.flac is id 456: re-downloading 456
        # must recognize its numbered copy instead of fetching a duplicate.
        dl = _make_download(skip_existing=True)
        (tmp_path / "Song.flac").write_bytes(b"x")
        (tmp_path / "Song_01.flac").write_bytes(b"x")
        ids = {"Song.flac": "123", "Song_01.flac": "456"}

        with patch("tidaler.download.read_item_id", side_effect=lambda p: ids[pathlib.Path(p).name]):
            assert dl._existing_is_same_item(tmp_path / "Song.flac", _track(456, [])) is True
            assert dl._existing_is_same_item(tmp_path / "Song.flac", _track(789, [])) is False


class TestItemIdTagRoundTrip:
    def _mp4_stub(self):
        fake = mutagen.mp4.MP4.__new__(mutagen.mp4.MP4)
        fake.tags = {}
        return fake

    def test_mp4_write_and_read_back(self, tmp_path):
        fake = self._mp4_stub()
        file = tmp_path / "t.m4a"
        file.write_bytes(b"x")

        with patch("tidaler.metadata.mutagen.File", return_value=fake):
            m = Metadata(path_file=file, target_upc={"MP4": "UPC"}, title="Song", item_id="123")
            m.set_mp4()
            assert fake.tags[f"----:com.apple.iTunes:{ITEM_ID_TAG}"] == b"123"
            assert read_item_id(file) == "123"

    def test_flac_write_and_read_back(self, tmp_path):
        fake = MagicMock()
        fake.tags = {}
        file = tmp_path / "t.flac"
        file.write_bytes(b"x")

        with patch("tidaler.metadata.mutagen.File", return_value=fake):
            m = Metadata(path_file=file, target_upc={"FLAC": "UPC"}, title="Song", item_id="123")
            m.set_flac()
            assert fake.tags[ITEM_ID_TAG] == "123"
            assert read_item_id(file) == "123"

    def test_unreadable_file_reads_as_unknown(self, tmp_path):
        file = tmp_path / "t.flac"
        file.write_bytes(b"x")
        assert read_item_id(file) == ""
        assert read_item_id(tmp_path / "missing.flac") == ""
