import pathlib
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from tidaler.constants import DownsampleTarget
from tidaler.download import Download


@pytest.fixture
def download_instance() -> Download:
    """Create a Download instance for file operation tests.

    Returns:
        Download: Configured download instance.
    """
    downloader = Download.__new__(Download)
    downloader.fn_logger = MagicMock()
    downloader._FILE_OPERATION_RETRIES = 2
    downloader._FILE_OPERATION_RETRY_DELAY_SEC = 0
    downloader._dirs_ensured = set()

    return downloader


def test_move_file_retries_transient_replace_failure(download_instance: Download, tmp_path: pathlib.Path) -> None:
    """Verify overwrite moves are retried when the destination is temporarily locked.

    Args:
        download_instance (Download): Download instance under test.
        tmp_path (pathlib.Path): Temporary test directory.
    """
    source_path: pathlib.Path = tmp_path / "source.flac"
    destination_path: pathlib.Path = tmp_path / "destination.flac"
    source_path.write_text("new", encoding="utf-8")
    destination_path.write_text("old", encoding="utf-8")

    replace_original = pathlib.Path.replace
    replace_calls: int = 0

    def replace_once_locked(self: pathlib.Path, target: pathlib.Path) -> pathlib.Path:
        nonlocal replace_calls
        replace_calls += 1
        if replace_calls == 1:
            raise PermissionError(32, "The process cannot access the file because it is being used by another process")
        return replace_original(self, target)

    with patch.object(pathlib.Path, "replace", replace_once_locked):
        result: bool = download_instance._move_file(source_path, destination_path, overwrite=True)

    assert result is True
    assert destination_path.read_text(encoding="utf-8") == "new"
    assert not source_path.exists()
    assert replace_calls == 2


def test_move_file_skip_existing_keeps_destination(download_instance: Download, tmp_path: pathlib.Path) -> None:
    """Verify shared album extras are skipped when another track already wrote them.

    Args:
        download_instance (Download): Download instance under test.
        tmp_path (pathlib.Path): Temporary test directory.
    """
    source_path: pathlib.Path = tmp_path / "cover-source.jpg"
    destination_path: pathlib.Path = tmp_path / "cover.jpg"
    source_path.write_bytes(b"new-cover")
    destination_path.write_bytes(b"existing-cover")

    result: bool = download_instance._move_file(
        source_path,
        destination_path,
        overwrite=False,
        skip_if_exists=True,
    )

    assert result is True
    assert destination_path.read_bytes() == b"existing-cover"
    assert not source_path.exists()


def test_retry_file_operation_does_not_sleep_after_final_attempt(download_instance: Download) -> None:
    """Verify file operation retries do not delay after the final failed attempt.

    Args:
        download_instance (Download): Download instance under test.
    """

    def operation() -> bool:
        raise PermissionError(32, "The process cannot access the file because it is being used by another process")

    with patch("tidaler.download.time.sleep") as sleep_mock:
        result: bool = download_instance._retry_file_operation(operation, "locked operation")

    assert result is False
    assert sleep_mock.call_count == 1


def test_media_move_and_symlink_skips_symlink_when_unlink_fails(
    download_instance: Download,
    tmp_path: pathlib.Path,
) -> None:
    """Verify symlink creation is skipped when the original source cannot be removed.

    Args:
        download_instance (Download): Download instance under test.
        tmp_path (pathlib.Path): Temporary test directory.
    """
    source_path: pathlib.Path = tmp_path / "source.flac"
    source_path.write_text("audio", encoding="utf-8")

    download_instance.path_base = str(tmp_path / "library")
    download_instance.skip_existing = False
    download_instance.settings = SimpleNamespace(
        data=SimpleNamespace(
            filename_delimiter_artist=", ",
            filename_delimiter_album_artist=", ",
            format_track="Tracks/{artist_name} - {track_title}",
            use_primary_album_artist=False,
        ),
    )

    with (
        patch("tidaler.download.format_path_media", return_value="Tracks/Artist - Title"),
        patch.object(download_instance, "_move_file", return_value=True),
        patch.object(download_instance, "_unlink_with_retry", return_value=False),
        patch.object(pathlib.Path, "symlink_to") as symlink_to_mock,
    ):
        result_path: pathlib.Path = download_instance.media_move_and_symlink(
            MagicMock(),
            source_path,
            ".flac",
        )

    assert result_path == tmp_path / "library" / "Tracks" / "Artist - Title.flac"
    symlink_to_mock.assert_not_called()
    download_instance.fn_logger.error.assert_called_once()


def test_downsample_audio_raises_when_output_move_fails(
    download_instance: Download,
    tmp_path: pathlib.Path,
) -> None:
    """Verify downsample replacement failures are propagated.

    Args:
        download_instance (Download): Download instance under test.
        tmp_path (pathlib.Path): Temporary test directory.
    """
    source_path: pathlib.Path = tmp_path / "source.flac"
    source_path.write_text("audio", encoding="utf-8")
    download_instance.settings = SimpleNamespace(
        data=SimpleNamespace(
            downsample_target=DownsampleTarget.BIT16_48,
            path_binary_ffmpeg="ffmpeg",
        ),
    )
    flac_mock: MagicMock = MagicMock()
    flac_mock.info.sample_rate = 96000
    flac_mock.info.bits_per_sample = 24
    ffmpeg_mock: MagicMock = MagicMock()
    ffmpeg_mock.option.return_value = ffmpeg_mock
    ffmpeg_mock.input.return_value = ffmpeg_mock
    ffmpeg_mock.output.return_value = ffmpeg_mock

    with (
        patch("tidaler.download.FLAC", return_value=flac_mock),
        patch("tidaler.download.FFmpeg", return_value=ffmpeg_mock),
        patch.object(download_instance, "_move_file", return_value=False),
        pytest.raises(OSError),
    ):
        download_instance._downsample_audio(source_path)


def test_move_file_fresh_destination_is_atomic_and_clean(
    download_instance: Download,
    tmp_path: pathlib.Path,
) -> None:
    """Verify a fresh move places the complete file and leaves no temp artifacts.

    Args:
        download_instance (Download): Download instance under test.
        tmp_path (pathlib.Path): Temporary test directory.
    """
    source_path: pathlib.Path = tmp_path / "source.flac"
    destination_path: pathlib.Path = tmp_path / "dest.flac"
    source_path.write_bytes(b"audio-data")

    result: bool = download_instance._move_file(source_path, destination_path, overwrite=True)

    assert result is True
    assert destination_path.read_bytes() == b"audio-data"
    assert not source_path.exists()
    assert list(tmp_path.glob(".*.tmp")) == []


def test_move_file_cross_filesystem_completes_via_staged_copy(
    download_instance: Download,
    tmp_path: pathlib.Path,
) -> None:
    """Verify a fresh move still completes when a same-filesystem rename is impossible.

    Args:
        download_instance (Download): Download instance under test.
        tmp_path (pathlib.Path): Temporary test directory.
    """
    source_path: pathlib.Path = tmp_path / "source.flac"
    destination_path: pathlib.Path = tmp_path / "dest.flac"
    source_path.write_bytes(b"hi-res")

    replace_original = pathlib.Path.replace

    def replace_no_cross_device(self: pathlib.Path, target: pathlib.Path) -> pathlib.Path:
        # Force the direct source -> destination rename to fail like a cross-device
        # move, but allow the temp-sibling -> destination swap (same filesystem).
        if self == source_path:
            raise OSError("Invalid cross-device link")
        return replace_original(self, target)

    with patch.object(pathlib.Path, "replace", replace_no_cross_device):
        result: bool = download_instance._move_file(source_path, destination_path, overwrite=True)

    assert result is True
    assert destination_path.read_bytes() == b"hi-res"
    assert not source_path.exists()
    assert list(tmp_path.glob(".*.tmp")) == []


def test_move_file_interrupted_cross_filesystem_copy_leaves_no_partial(
    download_instance: Download,
    tmp_path: pathlib.Path,
) -> None:
    """Verify a crash mid-copy never leaves a half-written file under the real name.

    This is the guarantee that stops an interrupted download from blocking its own
    re-download: the destination must not exist, the source must survive for the
    retry, and the staged temp file must be cleaned up.

    Args:
        download_instance (Download): Download instance under test.
        tmp_path (pathlib.Path): Temporary test directory.
    """
    source_path: pathlib.Path = tmp_path / "source.flac"
    destination_path: pathlib.Path = tmp_path / "dest.flac"
    source_path.write_bytes(b"hi-res-audio")

    replace_original = pathlib.Path.replace

    def replace_no_cross_device(self: pathlib.Path, target: pathlib.Path) -> pathlib.Path:
        if self == source_path:
            raise OSError("Invalid cross-device link")
        return replace_original(self, target)

    def copy_crash(src: pathlib.Path, dst: pathlib.Path) -> None:
        # Write a partial temp file, then simulate a crash / power loss mid-copy.
        dst.write_bytes(b"hi-r")
        raise OSError("simulated interruption during copy")

    with (
        patch.object(pathlib.Path, "replace", replace_no_cross_device),
        patch.object(download_instance, "_copy_file_contents", copy_crash),
    ):
        result: bool = download_instance._move_file(source_path, destination_path, overwrite=True)

    assert result is False
    assert not destination_path.exists()
    assert source_path.read_bytes() == b"hi-res-audio"
    assert list(tmp_path.glob(".*.tmp")) == []
