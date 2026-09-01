"""Symlink-to-track mode must obey the same file-safety rules as a plain download.

With "symlink to track" on, a playlist download lands in the playlist folder and
is then moved into the artist/album track folder, leaving a symlink behind. That
second move used to be the one place in the engine that ignored every protection
the main path grew for issue #15: it overwrote unconditionally, made no in-flight
name claim, and decided "already there" by filename alone. So a different track
whose name collides could be overwritten, or, worse, the freshly downloaded audio
was unlinked and replaced with a symlink pointing at a stranger's file.
"""

import pathlib
import threading
from unittest.mock import MagicMock, patch

from tidalapi.media import Track

from waves.download import Download
from waves.helper.path import check_file_exists

TRACK_DIR_RELATIVE = "Tracks/Song"


def _make_download(tmp_path: pathlib.Path, skip_existing: bool = True) -> Download:
    dl = Download(
        tidal_obj=MagicMock(),
        skip_existing=skip_existing,
        path_base=str(tmp_path),
        fn_logger=MagicMock(),
        progress=MagicMock(),
    )
    dl.settings = MagicMock()
    dl.event_abort = threading.Event()
    dl.event_run = threading.Event()
    dl.event_run.set()

    return dl


def _track(track_id: int) -> Track:
    t = Track.__new__(Track)
    t.id = track_id
    t.audio_modes = []
    t.artists = []
    t.name = "Song"
    t.version = None

    return t


def _plant(path_file: pathlib.Path, payload: bytes) -> pathlib.Path:
    path_file.parent.mkdir(parents=True, exist_ok=True)
    path_file.write_bytes(payload)

    return path_file


def _ids_by_payload(payloads: dict[bytes, str]):
    """read_item_id stand-in: the id a planted file carries is its own payload."""

    def _read(path_file) -> str:
        try:
            return payloads.get(pathlib.Path(path_file).read_bytes(), "")
        except OSError:
            return ""

    return _read


class TestSymlinkMoveDoesNotEatACollidingTrack:
    def test_a_stranger_in_the_track_folder_does_not_swallow_the_download(self, tmp_path):
        # The track folder already holds a DIFFERENT track under the same
        # sanitized name. Deciding by filename alone made the engine call the
        # download "already there", delete the audio it had just written and
        # point the playlist entry at the stranger.
        dl = _make_download(tmp_path)
        occupant = _plant(tmp_path / "Tracks" / "Song.flac", b"id-999")
        source = _plant(tmp_path / "Playlists" / "Party" / "Song.flac", b"id-111")

        with (
            patch("waves.download.format_path_media", return_value=TRACK_DIR_RELATIVE),
            patch("waves.download.read_item_id", _ids_by_payload({b"id-999": "999", b"id-111": "111"})),
        ):
            destination = dl.media_move_and_symlink(_track(111), source, ".flac")

        assert occupant.read_bytes() == b"id-999", "a stranger's track may not be overwritten"
        assert destination.read_bytes() == b"id-111", "the downloaded audio has to reach the track folder"
        assert destination != occupant
        assert source.is_symlink()
        assert source.read_bytes() == b"id-111", "the playlist entry has to point at its own track"

    def test_the_same_track_already_in_place_still_gets_its_symlink(self, tmp_path):
        # The historical, wanted behavior: this very track is already in the
        # track folder, so the playlist copy becomes a symlink to it and no
        # second copy is made.
        dl = _make_download(tmp_path)
        occupant = _plant(tmp_path / "Tracks" / "Song.flac", b"id-111")
        source = _plant(tmp_path / "Playlists" / "Party" / "Song.flac", b"id-111")

        with (
            patch("waves.download.format_path_media", return_value=TRACK_DIR_RELATIVE),
            patch("waves.download.read_item_id", _ids_by_payload({b"id-111": "111"})),
        ):
            destination = dl.media_move_and_symlink(_track(111), source, ".flac")

        assert destination == occupant
        assert sorted(p.name for p in (tmp_path / "Tracks").iterdir()) == ["Song.flac"]
        assert source.is_symlink()

    def test_two_colliding_tracks_symlinked_side_by_side_both_land(self, tmp_path):
        # Both threads read the destination as free, then moved onto it: the
        # second move overwrote the first track's audio, and the first playlist
        # entry silently became a pointer to the second track.
        dl = _make_download(tmp_path)
        sources = {
            111: _plant(tmp_path / "Playlists" / "Party" / "Song.flac", b"id-111"),
            222: _plant(tmp_path / "Playlists" / "Party" / "Song_01.flac", b"id-222"),
        }
        payload_ids = {b"id-111": "111", b"id-222": "222"}

        # Hold both threads in the window between "is the destination free?"
        # and the move, which is the window the bug lived in.
        barrier = threading.Barrier(2, timeout=10)

        def _check_synced(path_file, extension_ignore=False) -> bool:
            result = check_file_exists(path_file, extension_ignore=extension_ignore)
            barrier.wait()

            return result

        results: dict[int, pathlib.Path] = {}

        def _run(track_id: int) -> None:
            results[track_id] = dl.media_move_and_symlink(_track(track_id), sources[track_id], ".flac")

        with (
            patch("waves.download.format_path_media", return_value=TRACK_DIR_RELATIVE),
            patch("waves.download.read_item_id", _ids_by_payload(payload_ids)),
            patch("waves.download.check_file_exists", _check_synced),
        ):
            threads = [threading.Thread(target=_run, args=(track_id,)) for track_id in sources]

            for thread in threads:
                thread.start()

            for thread in threads:
                thread.join(timeout=20)

        assert len(results) == 2
        assert len({str(p) for p in results.values()}) == 2, "colliding tracks must not choose one name"

        landed = {p.read_bytes() for p in results.values()}
        assert landed == {b"id-111", b"id-222"}, "neither track may be overwritten by the other"
        assert dl._names_reserved == {}, "claims are released once the files are in place"


class TestSkipLogicAgreesWithTheMove:
    def test_a_stranger_in_the_track_folder_does_not_skip_the_download(self, tmp_path):
        # The skip gate and the move have to answer the same question. The gate
        # decided by filename alone, so a colliding stranger made the track look
        # downloaded: nothing was fetched and the playlist pointed at the stranger.
        dl = _make_download(tmp_path)
        dl.settings.data.symlink_to_track = True
        dl.settings.data.album_track_num_pad_min = 2
        _plant(tmp_path / "Tracks" / "Song.flac", b"id-999")

        media = _track(111)
        media.media_metadata_tags = []

        with (
            patch("waves.download.format_path_media", return_value=TRACK_DIR_RELATIVE),
            patch("waves.download.read_item_id", _ids_by_payload({b"id-999": "999"})),
            patch.object(dl, "extension_guess", return_value=".flac"),
        ):
            _path, _extension, _skip_file, skip_download = dl._prepare_file_paths_and_skip_logic(
                media=media,
                file_template="{track_title}",
                quality_audio=None,
                list_position=1,
                list_total=1,
            )

        assert skip_download is False, "a stranger's file may not stand in for this track"
