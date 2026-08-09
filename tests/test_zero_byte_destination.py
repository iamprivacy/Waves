"""A truncated leftover must not eat a track's name forever.

A crash or a share drop between creating a file and writing it leaves a 0-byte
file under the final name. The skip gate reads that correctly, as nothing (a
0-byte file is never a finished download), so the track downloads again. The
move then read the very same file as an occupant and refused to land on it,
saying the destination belongs to another writer. Every retry, and every run
after it, repeated exactly that: the track could never be downloaded again
until the user found and removed the empty file by hand.

Related, and the same shape of silent loss: when all 99 numbered variants of a
name are taken, file_unique_suffix used to hand back the last one anyway, so
the move was refused with the same wordless failure.
"""

import pathlib
import threading
from unittest.mock import MagicMock

from tidalapi.media import Track

from tidaler.constants import UNIQUIFY_THRESHOLD
from tidaler.download import Download
from tidaler.helper.path import file_unique_suffix


def _make_download(tmp_path: pathlib.Path) -> Download:
    dl = Download(
        tidal_obj=MagicMock(),
        skip_existing=True,
        path_base=str(tmp_path),
        fn_logger=MagicMock(),
        progress=MagicMock(),
    )
    dl.settings = MagicMock()
    dl.settings.data.video_convert_mp4 = False
    dl.settings.data.extract_flac = False
    dl.settings.data.downsample_enabled = False
    dl.settings.data.path_binary_ffmpeg = ""
    dl.event_abort = threading.Event()
    dl.event_run = threading.Event()
    dl.event_run.set()
    dl._handle_metadata_and_extras = lambda *args, **kwargs: None

    def _download(media, stream_manifest, path_file, event_stop=None):
        path_file.write_bytes(b"id-" + str(media.id).encode())

        return True, path_file

    dl._download = _download

    return dl


def _track(track_id: int) -> Track:
    t = Track.__new__(Track)
    t.id = track_id
    t.audio_modes = []
    t.artists = []
    t.name = "Song"
    t.version = None

    return t


def _run(dl: Download, destination: pathlib.Path, track_id: int = 111) -> tuple:
    return dl._perform_actual_download(
        media=_track(track_id),
        path_media_dst=destination,
        stream_manifest=MagicMock(),
        do_flac_extract=False,
        is_parent_album=False,
        media_stream=None,
    )


class TestATruncatedLeftoverIsFinished:
    def test_the_download_lands_over_an_empty_file(self, tmp_path):
        dl = _make_download(tmp_path)
        destination = tmp_path / "Song.flac"
        destination.touch()

        ok, path = _run(dl, destination)

        assert ok is True
        assert path == destination
        assert destination.read_bytes() == b"id-111"
        assert list(tmp_path.iterdir()) == [destination], "no numbered copy is made for an empty leftover"

    def test_an_empty_lyrics_leftover_is_finished_too(self, tmp_path):
        # Every sidecar goes through the same move, so the same leftover would
        # keep a re-fetched .lrc out of the library.
        dl = _make_download(tmp_path)
        source = tmp_path / "tmp.lrc"
        source.write_bytes(b"[00:01.00] words")
        destination = tmp_path / "Song.lrc"
        destination.touch()

        assert dl._move_file(source, destination, overwrite=False) is True
        assert destination.read_bytes() == b"[00:01.00] words"

    def test_a_real_occupant_is_still_refused_and_said_out_loud(self, tmp_path):
        # The issue-15 guard stays exactly as it was for a file with content.
        dl = _make_download(tmp_path)
        source = tmp_path / "tmp.flac"
        source.write_bytes(b"new")
        destination = tmp_path / "Song.flac"
        destination.write_bytes(b"somebody else")

        assert dl._move_file(source, destination, overwrite=False) is False
        assert destination.read_bytes() == b"somebody else"
        assert dl.fn_logger.error.called


class TestRunningOutOfNumberedCopies:
    def test_an_exhausted_name_fails_loudly(self, tmp_path):
        # All 99 variants taken. The suffix helper used to hand back "_99"
        # regardless, and the move then refused it with the occupied-destination
        # error: a finished download dropped over a naming limit, not a real
        # collision. It has to fail as a download failure instead.
        dl = _make_download(tmp_path)
        destination = tmp_path / "Song.flac"
        destination.write_bytes(b"taken")

        for count in range(1, UNIQUIFY_THRESHOLD + 1):
            (tmp_path / f"Song_{count:02d}.flac").write_bytes(b"taken")

        ok, _path = _run(dl, destination)

        assert ok is False
        assert dl.fn_logger.error.called
        assert destination.read_bytes() == b"taken", "nothing already in the library is touched"

    def test_the_suffix_helper_reports_exhaustion(self, tmp_path):
        destination = tmp_path / "Song.flac"
        destination.write_bytes(b"taken")

        for count in range(1, UNIQUIFY_THRESHOLD + 1):
            (tmp_path / f"Song_{count:02d}.flac").write_bytes(b"taken")

        assert file_unique_suffix(destination) is None

    def test_a_free_variant_still_answers_normally(self, tmp_path):
        destination = tmp_path / "Song.flac"
        destination.write_bytes(b"taken")

        assert file_unique_suffix(destination) == "_01"
        assert file_unique_suffix(tmp_path / "Free.flac") == ""
