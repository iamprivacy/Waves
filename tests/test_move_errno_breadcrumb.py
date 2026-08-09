"""The rename fast path leaves a breadcrumb when it falls back.

_move_file tries a plain rename first and drops to the copy-and-swap fallback on
any OSError. That catch is deliberately wide: it is what makes a library on a
network mount or an external drive work at all, and narrowing it to EXDEV would
undo hard-won behavior there. But it swallowed the errno, so a destination that
is read-only or out of space looked exactly like an ordinary cross-filesystem
move right up to the point the copy failed too. The errno now reaches the
breadcrumb ring, with the file named only by its name, never its path.
"""

import errno
import logging
import pathlib
from unittest.mock import MagicMock, patch

from tidaler.download import Download


def _download_instance() -> Download:
    downloader = Download.__new__(Download)
    downloader.fn_logger = MagicMock()
    downloader._FILE_OPERATION_RETRIES = 1
    downloader._FILE_OPERATION_RETRY_DELAY_SEC = 0
    downloader._dirs_ensured = set()

    return downloader


def test_a_cross_filesystem_rename_says_which_errno(tmp_path, caplog):
    dl = _download_instance()
    source = tmp_path / "source.flac"
    source.write_bytes(b"audio")
    destination = tmp_path / "landed.flac"

    replace_real = pathlib.Path.replace

    def _cross_device(self, target):
        # Only the temp-to-destination rename crosses the boundary; the copy
        # fallback's own swap happens inside the destination directory.
        if self == source:
            raise OSError(errno.EXDEV, "Cross-device link")

        return replace_real(self, target)

    with caplog.at_level(logging.INFO, logger="waves.download"), patch.object(pathlib.Path, "replace", _cross_device):
        assert dl._move_file(source, destination, overwrite=True) is True

    assert destination.read_bytes() == b"audio"

    breadcrumbs = [r.getMessage() for r in caplog.records if r.name == "waves.download"]
    assert any(str(errno.EXDEV) in message for message in breadcrumbs), breadcrumbs
    assert any("landed.flac" in message for message in breadcrumbs), breadcrumbs
    assert not any(str(tmp_path) in message for message in breadcrumbs), "the path never reaches the log"


def test_a_successful_rename_says_nothing(tmp_path, caplog):
    dl = _download_instance()
    source = tmp_path / "source.flac"
    source.write_bytes(b"audio")

    with caplog.at_level(logging.INFO, logger="waves.download"):
        assert dl._move_file(source, tmp_path / "landed.flac", overwrite=True) is True

    assert [r for r in caplog.records if r.name == "waves.download"] == []
