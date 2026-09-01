"""The cover and the lyrics follow the audio into the library, never lead it.

The sidecars were moved into place before the audio file they belong to, so a
move that then failed (a share going away, a full disk, an occupied name) left a
cover.jpg and a .lrc sitting in the library for a track that never arrived.
Nothing removes them afterwards either: the app never deletes a user-visible
file, by design. They now move only once the audio has really landed.
"""

import pathlib
import threading
from unittest.mock import MagicMock

from tidalapi.media import Track

from waves.download import Download


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
    dl.settings.data.lyrics_file = True
    dl.settings.data.cover_album_file = True
    dl.event_abort = threading.Event()
    dl.event_run = threading.Event()
    dl.event_run.set()

    def _download(media, stream_manifest, path_file, event_stop=None):
        path_file.write_bytes(b"audio")

        return True, path_file

    dl._download = _download

    return dl


def _track(track_id: int = 111) -> Track:
    t = Track.__new__(Track)
    t.id = track_id
    t.audio_modes = []
    t.artists = []
    t.name = "Song"
    t.version = None

    return t


def _with_sidecars(dl: Download, tmp_path: pathlib.Path) -> None:
    """Stand in for tagging only, so the real sidecar handling is exercised."""

    def _metadata_write(media, tmp_path_file, is_parent_album, media_stream):
        lyrics = tmp_path / "tmp.lrc"
        lyrics.write_text("[00:01.00] words\n", encoding="utf-8")
        cover = tmp_path / "tmp.jpg"
        cover.write_bytes(b"cover bytes")

        return True, lyrics, ".lrc", cover

    dl.metadata_write = _metadata_write


def _run(dl: Download, destination: pathlib.Path) -> tuple:
    return dl._perform_actual_download(
        media=_track(),
        path_media_dst=destination,
        stream_manifest=MagicMock(),
        do_flac_extract=False,
        is_parent_album=False,
        media_stream=MagicMock(),
    )


class TestSidecarsWaitForTheAudio:
    def test_a_failed_move_leaves_no_orphaned_sidecars(self, tmp_path):
        library = tmp_path / "library"
        library.mkdir()
        dl = _make_download(tmp_path)
        _with_sidecars(dl, tmp_path)

        move_real = dl._move_file

        def _audio_cannot_land(source, destination, *args, **kwargs):
            # The audio's own move fails (a share gone, a full disk); the
            # sidecars would land perfectly well, which is the whole problem.
            if pathlib.Path(destination).suffix == ".flac":
                return False

            return move_real(source, destination, *args, **kwargs)

        dl._move_file = _audio_cannot_land

        ok, _path = _run(dl, library / "Song.flac")

        assert ok is False
        assert list(library.iterdir()) == [], "no cover or lyrics for a track that never arrived"
        # And the folder is not this run's to write a playlist into: nothing
        # of ours reached it.
        assert dl._dirs_filled == set()

    def test_a_successful_move_brings_the_sidecars_with_it(self, tmp_path):
        library = tmp_path / "library"
        library.mkdir()
        dl = _make_download(tmp_path)
        _with_sidecars(dl, tmp_path)

        ok, path = _run(dl, library / "Song.flac")

        assert ok is True
        assert path.read_bytes() == b"audio"
        assert (library / "Song.lrc").read_text(encoding="utf-8") == "[00:01.00] words\n"
        assert (library / "cover.jpg").read_bytes() == b"cover bytes"
        # A file landed here, so this folder is one the m3u writer may write in
        # (see tests/test_playlist_scope_only_what_landed.py).
        assert dl._dirs_filled == {library}

    def test_the_sidecars_follow_the_name_the_audio_actually_took(self, tmp_path):
        # The audio uniquifies away from an occupied name; the lyrics have to
        # end up beside the file, not beside the name that was asked for.
        library = tmp_path / "library"
        library.mkdir()
        (library / "Song.flac").write_bytes(b"a different track")
        dl = _make_download(tmp_path)
        _with_sidecars(dl, tmp_path)

        ok, path = _run(dl, library / "Song.flac")

        assert ok is True
        assert path.name == "Song_01.flac"
        assert (library / "Song_01.lrc").exists()
        assert not (library / "Song.lrc").exists()
