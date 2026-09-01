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

Follow-up on the same issue: the unique name was picked seconds before the file
reached it, so two colliding tracks downloading side by side both picked it and
one overwrote or silently lost the other. Names are now claimed at the moment
they are picked, and the numbered-copy scan reads every variant that exists
(gaps included), an untagged one among them meaning identity unknown.
"""

import pathlib
import threading
from unittest.mock import MagicMock, patch

import mutagen.mp4
from tidalapi.media import AudioMode, Track

from waves.download import Download
from waves.helper.path import path_file_uniquify
from waves.metadata import ITEM_ID_TAG, Metadata, read_item_id


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


class TestAtmosOnlyTracksAlwaysDownload:
    """The toggle prefers stereo where there is a choice. An Atmos-only track
    has no choice, so it downloads under either setting (2026-08-18); the old
    behaviour skipped it and left a hole in the album."""

    def _run_item(self, dl, media):
        with (
            patch.object(dl, "_validate_and_prepare_media", return_value=media),
            patch.object(dl, "_prepare_file_paths_and_skip_logic") as prepare,
        ):
            prepare.return_value = (pathlib.Path("./tmp/x.flac"), ".flac", True, False)
            return dl.item(file_template="{track_title}", media=media)

    def test_atmos_only_track_downloads_when_atmos_is_off(self):
        dl = _make_download()
        dl.settings.data.download_dolby_atmos = False
        media = _track(123, [AudioMode.dolby_atmos.value])

        ok, path = self._run_item(dl, media)

        # Reaches the (stubbed) skip-existing path: no guard stands in its way.
        assert ok is True
        assert str(path) != ""

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

        assert dl._existing_same_item_at(dst, _track(456, [])) == dst

    def test_same_id_occupant_skips(self, tmp_path):
        dl = _make_download(skip_existing=True)
        dst = tmp_path / "Song.flac"
        dst.write_bytes(b"x")

        with patch("waves.download.read_item_id", return_value="123"):
            assert dl._existing_same_item_at(dst, _track(123, [])) == dst

    def test_different_id_occupant_downloads(self, tmp_path):
        dl = _make_download(skip_existing=True)
        dst = tmp_path / "Song.flac"
        dst.write_bytes(b"x")

        with patch("waves.download.read_item_id", return_value="123"):
            assert dl._existing_same_item_at(dst, _track(456, [])) is None

    def test_uniquified_sibling_with_the_id_skips(self, tmp_path):
        # Song.flac is id 123, Song_01.flac is id 456: re-downloading 456
        # must recognize its numbered copy instead of fetching a duplicate.
        dl = _make_download(skip_existing=True)
        (tmp_path / "Song.flac").write_bytes(b"x")
        (tmp_path / "Song_01.flac").write_bytes(b"x")
        ids = {"Song.flac": "123", "Song_01.flac": "456"}

        with patch("waves.download.read_item_id", side_effect=lambda p: ids[pathlib.Path(p).name]):
            # Not a bare yes: the answer is the numbered copy itself, so the
            # caller skips onto the file that IS this track, never the base.
            assert dl._existing_same_item_at(tmp_path / "Song.flac", _track(456, [])) == tmp_path / "Song_01.flac"
            assert dl._existing_same_item_at(tmp_path / "Song.flac", _track(789, [])) is None

    def test_a_gap_in_the_numbering_does_not_hide_the_copy(self, tmp_path):
        # The user removed Song_01 and kept Song_02. The scan used to stop at
        # the first gap, so id 456 looked absent and downloaded all over again.
        dl = _make_download(skip_existing=True)
        (tmp_path / "Song.flac").write_bytes(b"x")
        (tmp_path / "Song_02.flac").write_bytes(b"x")
        ids = {"Song.flac": "123", "Song_02.flac": "456"}

        with patch("waves.download.read_item_id", side_effect=lambda p: ids[pathlib.Path(p).name]):
            assert dl._existing_same_item_at(tmp_path / "Song.flac", _track(456, [])) == tmp_path / "Song_02.flac"

    def test_untagged_sibling_means_identity_unknown_and_skips(self, tmp_path):
        # A library part pre-0.1.17: Song_01.flac carries no id, so it may well
        # BE this track. An untagged base occupant already skips; a numbered one
        # has to answer the same, or the old copy gets duplicated as Song_02.
        dl = _make_download(skip_existing=True)
        (tmp_path / "Song.flac").write_bytes(b"x")
        (tmp_path / "Song_01.flac").write_bytes(b"x")
        ids = {"Song.flac": "123", "Song_01.flac": ""}

        with patch("waves.download.read_item_id", side_effect=lambda p: ids[pathlib.Path(p).name]):
            assert dl._existing_same_item_at(tmp_path / "Song.flac", _track(456, [])) == tmp_path / "Song_01.flac"

    def test_all_tagged_other_ids_still_download(self, tmp_path):
        # The conservative branches must not swallow the case they exist for: in
        # a fully tagged library a genuinely new colliding mix still downloads.
        dl = _make_download(skip_existing=True)
        (tmp_path / "Song.flac").write_bytes(b"x")
        (tmp_path / "Song_01.flac").write_bytes(b"x")
        (tmp_path / "Song_02.flac").write_bytes(b"x")
        ids = {"Song.flac": "123", "Song_01.flac": "456", "Song_02.flac": "789"}

        with patch("waves.download.read_item_id", side_effect=lambda p: ids[pathlib.Path(p).name]):
            assert dl._existing_same_item_at(tmp_path / "Song.flac", _track(999, [])) is None


class TestCollidingNamesUnderConcurrency:
    """Issue #15 follow-up: two same-name tracks downloading side by side.

    The unique name is picked seconds before the file reaches it (metadata, the
    lyrics fetch and the cover run in between), so a name is only safe once the
    picker also claims it. Without the claim both tracks picked the same name:
    one overwrote the other, or lost its own finished download without a word.
    """

    def test_reserved_names_are_treated_as_occupied(self, tmp_path):
        song = tmp_path / "Song.flac"

        # Nothing on disk, but the name is claimed: the next one gets _01.
        assert path_file_uniquify(song, names_taken={str(song)}).name == "Song_01.flac"

        # Disk and claims are read together, so this one has to skip both.
        song.write_bytes(b"x")
        taken = {str(tmp_path / "Song_01.flac")}
        assert path_file_uniquify(song, names_taken=taken).name == "Song_02.flac"

        # No claims at all keeps the historical answer.
        assert path_file_uniquify(song).name == "Song_01.flac"

    def _make_racing_download(self, tmp_path) -> Download:
        dl = _make_download(skip_existing=True)
        dl.settings.data.video_convert_mp4 = False
        dl.settings.data.extract_flac = False
        dl.settings.data.downsample_enabled = False
        dl.settings.data.path_binary_ffmpeg = ""

        def _download(media, stream_manifest, path_file, event_stop=None):
            path_file.write_bytes(b"audio bytes for " + str(media.id).encode())

            return True, path_file

        dl._download = _download

        return dl

    def test_two_colliding_tracks_both_land(self, tmp_path):
        dl = self._make_racing_download(tmp_path)
        dst = tmp_path / "Song.flac"

        # Both threads sit between picking the name and moving the file, which
        # is the window the bug lived in.
        barrier = threading.Barrier(2, timeout=10)

        def _extras(*args, **kwargs):
            barrier.wait()

        dl._handle_metadata_and_extras = _extras

        results: dict[int, tuple] = {}

        def _run(track_id: int) -> None:
            results[track_id] = dl._perform_actual_download(
                media=_track(track_id, []),
                path_media_dst=dst,
                stream_manifest=MagicMock(),
                do_flac_extract=False,
                is_parent_album=False,
                media_stream=None,
            )

        threads = [threading.Thread(target=_run, args=(track_id,)) for track_id in (111, 222)]

        for thread in threads:
            thread.start()

        for thread in threads:
            thread.join(timeout=20)

        assert all(ok for ok, _ in results.values()), "a finished download must never be dropped"

        paths = [path for _, path in results.values()]
        assert len({str(p) for p in paths}) == 2, "colliding tracks must not choose one name"

        for path in paths:
            assert path.is_file()
            assert path.stat().st_size > 0, "neither track may be left overwritten"

        assert {p.name for p in paths} == {"Song.flac", "Song_01.flac"}
        assert dl._names_reserved == {}, "claims are released once the file is in place"

    def test_a_failed_download_gives_its_name_back(self, tmp_path):
        dl = self._make_racing_download(tmp_path)
        dl._download = lambda media, stream_manifest, path_file, event_stop=None: (False, path_file)

        ok, _path = dl._perform_actual_download(
            media=_track(111, []),
            path_media_dst=tmp_path / "Song.flac",
            stream_manifest=MagicMock(),
            do_flac_extract=False,
            is_parent_album=False,
            media_stream=None,
        )

        assert ok is False
        assert dl._names_reserved == {}

    def test_an_occupied_destination_is_reported_not_swallowed(self, tmp_path):
        # Another writer got there first. Nothing raises, so the retry wrapper
        # has nothing to say: _move_file has to speak up itself.
        dl = _make_download(skip_existing=True)
        source = tmp_path / "source.flac"
        source.write_bytes(b"x")
        destination = tmp_path / "Song.flac"
        destination.write_bytes(b"someone else")

        assert dl._move_file(source, destination, overwrite=False) is False
        assert dl.fn_logger.error.called


class TestItemIdTagRoundTrip:
    def _mp4_stub(self):
        fake = mutagen.mp4.MP4.__new__(mutagen.mp4.MP4)
        fake.tags = {}
        return fake

    def test_mp4_write_and_read_back(self, tmp_path):
        fake = self._mp4_stub()
        file = tmp_path / "t.m4a"
        file.write_bytes(b"x")

        with patch("waves.metadata.mutagen.File", return_value=fake):
            m = Metadata(path_file=file, target_upc={"MP4": "UPC"}, title="Song", item_id="123")
            m.set_mp4()
            assert fake.tags[f"----:com.apple.iTunes:{ITEM_ID_TAG}"] == b"123"
            assert read_item_id(file) == "123"

    def test_flac_write_and_read_back(self, tmp_path):
        fake = MagicMock()
        fake.tags = {}
        file = tmp_path / "t.flac"
        file.write_bytes(b"x")

        with patch("waves.metadata.mutagen.File", return_value=fake):
            m = Metadata(path_file=file, target_upc={"FLAC": "UPC"}, title="Song", item_id="123")
            m.set_flac()
            assert fake.tags[ITEM_ID_TAG] == "123"
            assert read_item_id(file) == "123"

    def test_unreadable_file_reads_as_unknown(self, tmp_path):
        file = tmp_path / "t.flac"
        file.write_bytes(b"x")
        assert read_item_id(file) == ""
        assert read_item_id(tmp_path / "missing.flac") == ""
