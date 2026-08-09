"""The playlist m3u is the one file written straight into the library.

Every track, cover and lyric reaches the library through _move_file, which
stages a hidden temp sibling and swaps it into place, so an interrupted write
can only ever leave a throwaway file behind. The m3u opened the real name in
truncating mode instead: a crash, a full disk or a dropped share mid-write left
the user with an emptied or half-written playlist where a complete one had been.

Its name also skipped the illegal-character stand-ins that every other name in
the library goes through, so a playlist called "?" lost its name entirely while
an album called "?" kept one (issue #16).
"""

import contextlib
import pathlib
import threading
from unittest.mock import MagicMock, patch

import pytest

from tidaler.download import Download


def _make_download(tmp_path: pathlib.Path, illegal_map: dict[str, str] | None = None) -> Download:
    dl = Download(
        tidal_obj=MagicMock(),
        skip_existing=True,
        path_base=str(tmp_path),
        fn_logger=MagicMock(),
        progress=MagicMock(),
    )
    dl.settings = MagicMock()
    dl.settings.data.filename_illegal_replacement = ""
    dl.settings.data.filename_illegal_map = illegal_map
    dl.event_abort = threading.Event()
    dl.event_run = threading.Event()
    dl.event_run.set()

    return dl


def _album_dir(tmp_path: pathlib.Path) -> pathlib.Path:
    directory = tmp_path / "Artist" / "Album"
    directory.mkdir(parents=True)
    (directory / "01 One.flac").write_bytes(b"a")
    (directory / "02 Two.flac").write_bytes(b"b")

    return directory


class TestTheM3uIsWrittenWhole:
    def test_a_complete_playlist_lands(self, tmp_path):
        dl = _make_download(tmp_path)
        directory = _album_dir(tmp_path)

        written = dl.playlist_populate({directory}, "My List", is_album=True, sort_alphabetically=True)

        assert [p.name for p in written] == ["_My List.m3u"]
        assert written[0].read_text(encoding="utf-8").splitlines() == ["01 One.flac", "02 Two.flac"]

    def test_a_failure_mid_write_leaves_the_previous_playlist_intact(self, tmp_path):
        # A full disk (or a share going away) while the entries are being
        # written. The playlist already in the folder has to survive it whole.
        dl = _make_download(tmp_path)
        directory = _album_dir(tmp_path)
        existing = directory / "_My List.m3u"
        existing.write_text("the playlist that was already there\n", encoding="utf-8")

        with _writes_failing(), pytest.raises(OSError, match="No space left on device"):
            dl.playlist_populate({directory}, "My List", is_album=True, sort_alphabetically=True)

        assert existing.read_text(encoding="utf-8") == "the playlist that was already there\n"
        assert [p.name for p in directory.iterdir() if p.name.endswith(".tmp")] == [], "no temp file is left behind"


@contextlib.contextmanager
def _writes_failing():
    """Every file opened for writing refuses to take content."""
    open_real = pathlib.Path.open

    class _FullDisk:
        def __init__(self, handle) -> None:
            self._handle = handle

        def __enter__(self):
            return self

        def __exit__(self, *_exc) -> bool:
            self._handle.close()

            return False

        def write(self, _data) -> int:
            raise OSError(28, "No space left on device")

    def _open(self, *args, **kwargs):
        handle = open_real(self, *args, **kwargs)

        return _FullDisk(handle) if "w" in str(kwargs.get("mode", args[0] if args else "r")) else handle

    with patch.object(pathlib.Path, "open", _open):
        yield


class TestTheM3uNameFollowsTheStandIns:
    def test_a_playlist_named_only_of_rejected_characters_keeps_a_name(self, tmp_path):
        # The same shape as issue #16's album "?": with a stand-in configured,
        # the name survives instead of emptying out to the bare prefix.
        dl = _make_download(tmp_path, illegal_map={"?": "？"})
        directory = _album_dir(tmp_path)

        written = dl.playlist_populate({directory}, "?", is_album=False, sort_alphabetically=True)

        assert [p.name for p in written] == ["_？.m3u"]

    def test_a_rejected_character_inside_the_name_uses_its_stand_in(self, tmp_path):
        dl = _make_download(tmp_path, illegal_map={":": " · "})
        directory = _album_dir(tmp_path)

        written = dl.playlist_populate({directory}, "Rarities: Live", is_album=False, sort_alphabetically=True)

        assert [p.name for p in written] == ["_Rarities · Live.m3u"]

    def test_the_universal_rules_still_apply(self, tmp_path):
        # A library often sits on a share other machines mount too, so a name
        # is written to rules every platform accepts, not the running one's.
        dl = _make_download(tmp_path)
        directory = _album_dir(tmp_path)

        written = dl.playlist_populate({directory}, "Live. ", is_album=False, sort_alphabetically=True)

        assert [p.name for p in written] == ["_Live.m3u"]
