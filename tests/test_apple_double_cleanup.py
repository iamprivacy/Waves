"""AppleDouble (._*) hygiene: strip xattrs after moves, keep ghosts out of playlists.

On WebDAV-backed destinations macOS materialises every xattr as a hidden 4 KB
``._<name>`` sibling. These tests pin the two defenses: the post-move cleanup
hook and the dotfile filter in playlist generation.
"""

import pathlib
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from tidaler.download import Download
from tidaler.helper.path import strip_apple_double


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
    # The playlist name goes through the illegal-character stand-ins now, the
    # same ones every other name in the library follows.
    downloader.settings = SimpleNamespace(
        data=SimpleNamespace(filename_illegal_replacement="", filename_illegal_map=None)
    )

    return downloader


def test_strip_apple_double_never_deletes_files(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify the helper is prevention-only: no file, not even a ._ sibling, is ever deleted.

    Args:
        tmp_path (pathlib.Path): Temporary test directory.
        monkeypatch (pytest.MonkeyPatch): Pytest monkeypatch fixture.
    """
    monkeypatch.setattr(sys, "platform", "darwin")

    track_path: pathlib.Path = tmp_path / "01 Track.flac"
    ghost_path: pathlib.Path = tmp_path / "._01 Track.flac"
    other_path: pathlib.Path = tmp_path / "._user_file.dat"
    track_path.write_bytes(b"audio")
    ghost_path.write_bytes(b"\x00" * 32)
    other_path.write_bytes(b"\x00" * 32)

    strip_apple_double(track_path)

    assert track_path.exists()
    assert ghost_path.exists()
    assert other_path.exists()


def test_strip_apple_double_noop_off_macos(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify nothing is touched on non-macOS platforms.

    Args:
        tmp_path (pathlib.Path): Temporary test directory.
        monkeypatch (pytest.MonkeyPatch): Pytest monkeypatch fixture.
    """
    monkeypatch.setattr(sys, "platform", "win32")

    track_path: pathlib.Path = tmp_path / "01 Track.flac"
    ghost_path: pathlib.Path = tmp_path / "._01 Track.flac"
    track_path.write_bytes(b"audio")
    ghost_path.write_bytes(b"\x00" * 32)

    strip_apple_double(track_path)

    assert ghost_path.exists()


@pytest.mark.skipif(sys.platform != "darwin", reason="xattr APIs exercised for real on macOS only")
def test_strip_apple_double_removes_xattrs(tmp_path: pathlib.Path) -> None:
    """Verify extended attributes are dropped from the destination file.

    Args:
        tmp_path (pathlib.Path): Temporary test directory.
    """
    track_path: pathlib.Path = tmp_path / "01 Track.flac"
    track_path.write_bytes(b"audio")
    subprocess.run(["/usr/bin/xattr", "-w", "com.example.test", "value", str(track_path)], check=True)

    strip_apple_double(track_path)

    listed = subprocess.run(["/usr/bin/xattr", str(track_path)], check=True, capture_output=True, text=True)
    assert "com.example.test" not in listed.stdout


def test_strip_apple_double_survives_missing_file(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """Verify a vanished destination never raises: cleanup is best-effort.

    Args:
        monkeypatch (pytest.MonkeyPatch): Pytest monkeypatch fixture.
        tmp_path (pathlib.Path): Temporary test directory.
    """
    monkeypatch.setattr(sys, "platform", "darwin")

    strip_apple_double(tmp_path / "never-existed.flac")


def test_move_file_strips_apple_double_after_move(
    download_instance: Download, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify a successful move triggers the xattr cleanup, without deleting any file.

    Args:
        download_instance (Download): Download instance under test.
        tmp_path (pathlib.Path): Temporary test directory.
        monkeypatch (pytest.MonkeyPatch): Pytest monkeypatch fixture.
    """
    monkeypatch.setattr(sys, "platform", "darwin")

    source_path: pathlib.Path = tmp_path / "source.flac"
    destination_path: pathlib.Path = tmp_path / "01 Track.flac"
    ghost_path: pathlib.Path = tmp_path / "._01 Track.flac"
    source_path.write_bytes(b"audio")
    ghost_path.write_bytes(b"\x00" * 32)

    with patch("tidaler.download.strip_apple_double") as strip_mock:
        result: bool = download_instance._move_file(source_path, destination_path, overwrite=True)

    assert result is True
    assert destination_path.exists()
    strip_mock.assert_called_once_with(destination_path)

    # And the real helper against the same layout: the ghost stays untouched.
    strip_apple_double(destination_path)
    assert ghost_path.exists()


def test_move_file_failure_skips_cleanup(download_instance: Download, tmp_path: pathlib.Path) -> None:
    """Verify a failed move does not attempt cleanup (and still returns False).

    Args:
        download_instance (Download): Download instance under test.
        tmp_path (pathlib.Path): Temporary test directory.
    """
    with patch("tidaler.download.strip_apple_double") as strip_mock:
        result: bool = download_instance._move_file(
            tmp_path / "missing-source.flac", tmp_path / "destination.flac", overwrite=True
        )

    assert result is False
    strip_mock.assert_not_called()


def test_playlist_populate_ignores_apple_double_ghosts(download_instance: Download, tmp_path: pathlib.Path) -> None:
    """Verify ._ ghosts and other dotfiles never become m3u entries.

    Args:
        download_instance (Download): Download instance under test.
        tmp_path (pathlib.Path): Temporary test directory.
    """
    (tmp_path / "01 Track.flac").write_bytes(b"audio")
    (tmp_path / "02 Track.flac").write_bytes(b"audio")
    (tmp_path / "._01 Track.flac").write_bytes(b"\x00" * 32)
    (tmp_path / "._02 Track.flac").write_bytes(b"\x00" * 32)
    (tmp_path / ".hidden.flac").write_bytes(b"\x00")

    playlists: list[pathlib.Path] = download_instance.playlist_populate(
        {tmp_path}, "Test Album", is_album=True, sort_alphabetically=True
    )

    assert len(playlists) == 1
    entries: list[str] = playlists[0].read_text(encoding="utf-8").splitlines()
    assert entries == ["01 Track.flac", "02 Track.flac"]
