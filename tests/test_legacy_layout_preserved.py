"""An existing library keeps its folder and file names.

0.1.17 stopped leaving a doubled space where an illegal character was
stripped (issue #15), which changes the name a download computes. Anything
already on disk must keep the name it has: the alternative is an album that
looks missing, downloads again, and leaves the user owning two folders for
one album.

So the old spelling wins wherever it already exists, decided separately for
the folder and the file, and the tidy spelling applies only where nothing is
there yet.
"""

from __future__ import annotations

import pathlib
import threading
from unittest.mock import MagicMock

from tidaler.download import Download

_LEGACY_DIR = "The Better Life  Dead Love"  # doubled space, pre-0.1.17
_TIDY_DIR = "The Better Life Dead Love"


def _make_download(base: pathlib.Path) -> Download:
    dl = Download(
        tidal_obj=MagicMock(),
        skip_existing=True,
        path_base=str(base),
        fn_logger=MagicMock(),
        progress=MagicMock(),
    )
    dl.settings = MagicMock()
    dl.event_abort = threading.Event()
    dl.event_run = threading.Event()
    dl.event_run.set()
    return dl


class TestFolderNamesAreNeverRestructured:
    def test_an_existing_legacy_folder_keeps_receiving_downloads(self, tmp_path):
        dl = _make_download(tmp_path)
        (tmp_path / _LEGACY_DIR).mkdir()

        chosen = dl._keep_existing_layout(
            tmp_path / _TIDY_DIR / "Song.flac",
            tmp_path / _LEGACY_DIR / "Song.flac",
        )

        assert chosen.parent.name == _LEGACY_DIR

    def test_a_fresh_library_gets_the_tidy_folder(self, tmp_path):
        dl = _make_download(tmp_path)

        chosen = dl._keep_existing_layout(
            tmp_path / _TIDY_DIR / "Song.flac",
            tmp_path / _LEGACY_DIR / "Song.flac",
        )

        assert chosen.parent.name == _TIDY_DIR

    def test_an_already_tidy_folder_is_not_disturbed(self, tmp_path):
        dl = _make_download(tmp_path)
        (tmp_path / _TIDY_DIR).mkdir()

        chosen = dl._keep_existing_layout(
            tmp_path / _TIDY_DIR / "Song.flac",
            tmp_path / _LEGACY_DIR / "Song.flac",
        )

        assert chosen.parent.name == _TIDY_DIR


class TestFileNamesAreNeverDuplicated:
    def test_an_existing_legacy_file_is_reused_not_re_downloaded(self, tmp_path):
        dl = _make_download(tmp_path)
        (tmp_path / _LEGACY_DIR).mkdir()
        (tmp_path / _LEGACY_DIR / "A  B.flac").write_bytes(b"x")

        chosen = dl._keep_existing_layout(
            tmp_path / _TIDY_DIR / "A B.flac",
            tmp_path / _LEGACY_DIR / "A  B.flac",
        )

        assert chosen == tmp_path / _LEGACY_DIR / "A  B.flac"

    def test_a_new_track_in_a_legacy_folder_still_gets_the_tidy_name(self, tmp_path):
        # The folder is theirs and stays; only the never-seen file is tidied.
        dl = _make_download(tmp_path)
        (tmp_path / _LEGACY_DIR).mkdir()

        chosen = dl._keep_existing_layout(
            tmp_path / _TIDY_DIR / "A B.flac",
            tmp_path / _LEGACY_DIR / "A  B.flac",
        )

        assert chosen == tmp_path / _LEGACY_DIR / "A B.flac"

    def test_identical_names_short_circuit(self, tmp_path):
        dl = _make_download(tmp_path)
        same = tmp_path / "Album" / "Song.flac"

        assert dl._keep_existing_layout(same, same) == same
