"""Encrypted streams are refused, never written out as an unplayable file.

Waves does not process encrypted streams. TIDAL serves plain MPEG-DASH for
every quality Waves requests, so the guard is not reached in normal operation,
but if a stream ever arrives encrypted the download must fail cleanly instead
of leaving a scrambled file behind in the user's library.
"""

import pathlib
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from tidalapi.media import Track

from waves.download import Download


@pytest.fixture
def downloader() -> Download:
    """Create a bare Download instance for post-process tests.

    Returns:
        Download: Instance with only what ``_download_postprocess`` touches.
    """
    instance = Download.__new__(Download)
    instance.fn_logger = MagicMock()
    # The merge itself is not under test here; pin it to success so the guard
    # downstream of it is what decides the outcome.
    instance._segments_merge = lambda *args, **kwargs: True

    return instance


def _manifest(*, encrypted: bool) -> SimpleNamespace:
    """Build a stand-in stream manifest.

    Args:
        encrypted (bool): Value for the ``is_encrypted`` flag.

    Returns:
        SimpleNamespace: Object exposing the one attribute the guard reads.
    """
    return SimpleNamespace(is_encrypted=encrypted)


def test_encrypted_track_fails_the_download(downloader: Download, tmp_path: pathlib.Path) -> None:
    """An encrypted stream must report failure rather than success."""
    path_file = tmp_path / "song.flac"
    path_file.write_bytes(b"scrambled-payload")
    media = MagicMock(spec=Track)
    media.name = "Some Song"

    result, path_out = downloader._download_postprocess(True, path_file, [], media, _manifest(encrypted=True), None)

    assert result is False, "an encrypted stream must not be reported as a successful download"
    assert path_out == path_file, "the returned path must stay the merge target, no side file"
    downloader.fn_logger.error.assert_called_once()


def test_encrypted_track_writes_no_extra_file(downloader: Download, tmp_path: pathlib.Path) -> None:
    """The refusal must not leave a second, decrypted-looking artefact behind."""
    path_file = tmp_path / "song.flac"
    path_file.write_bytes(b"scrambled-payload")
    media = MagicMock(spec=Track)
    media.name = "Some Song"

    downloader._download_postprocess(True, path_file, [], media, _manifest(encrypted=True), None)

    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "song.flac"
    ], "refusing an encrypted stream must not create any additional file"


def test_plain_track_still_succeeds(downloader: Download, tmp_path: pathlib.Path) -> None:
    """The ordinary, unencrypted path is untouched by the guard."""
    path_file = tmp_path / "song.flac"
    path_file.write_bytes(b"audio")
    media = MagicMock(spec=Track)
    media.name = "Some Song"

    result, path_out = downloader._download_postprocess(True, path_file, [], media, _manifest(encrypted=False), None)

    assert result is True, "an unencrypted stream must still complete normally"
    assert path_out == path_file
    downloader.fn_logger.error.assert_not_called()


def test_failed_merge_still_reports_failure(downloader: Download, tmp_path: pathlib.Path) -> None:
    """A merge failure keeps its own error path, independent of the guard."""
    downloader._segments_merge = lambda *args, **kwargs: False
    path_file = tmp_path / "song.flac"
    media = MagicMock(spec=Track)
    media.name = "Some Song"

    result, _ = downloader._download_postprocess(True, path_file, [], media, _manifest(encrypted=False), None)

    assert result is False
    downloader.fn_logger.error.assert_called_once()
