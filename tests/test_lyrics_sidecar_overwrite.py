"""A hand-timed .lrc beside a track is the user's own work, not ours to replace.

The lyrics sidecar landed with overwrite on, so any download writing to that
stem replaced whatever .lrc was there. The audio itself never behaves that way:
with "skip existing files" on nothing in the library is written over, and with
it off replacing is exactly what was asked for. The sidecar now follows the same
rule, and an existing file is left alone quietly rather than reported as a
collision (the cover sidecar has always worked that way).
"""

import pathlib
import threading
from unittest.mock import MagicMock

from tidaler.download import Download

HAND_TIMED = "[00:12.30] a line somebody timed by hand\n"


def _make_download(tmp_path: pathlib.Path, skip_existing: bool) -> Download:
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


def _fetched_lyrics(tmp_path: pathlib.Path) -> pathlib.Path:
    source = tmp_path / "fetched.lrc"
    source.write_text("[00:12.30] the line as fetched\n", encoding="utf-8")

    return source


class TestLyricsFollowTheSameRuleAsTheAudio:
    def test_an_existing_lrc_is_kept_when_skipping_is_on(self, tmp_path):
        dl = _make_download(tmp_path, skip_existing=True)
        destination = tmp_path / "Song.flac"
        sidecar = tmp_path / "Song.lrc"
        sidecar.write_text(HAND_TIMED, encoding="utf-8")

        assert dl._move_lyrics(_fetched_lyrics(tmp_path), destination) is True
        assert sidecar.read_text(encoding="utf-8") == HAND_TIMED
        assert not dl.fn_logger.error.called, "keeping a sidecar is normal, not a collision"

    def test_an_existing_lrc_is_replaced_when_skipping_is_off(self, tmp_path):
        # Skipping off means "write what you fetch over what I have".
        dl = _make_download(tmp_path, skip_existing=False)
        destination = tmp_path / "Song.flac"
        sidecar = tmp_path / "Song.lrc"
        sidecar.write_text(HAND_TIMED, encoding="utf-8")

        assert dl._move_lyrics(_fetched_lyrics(tmp_path), destination) is True
        assert sidecar.read_text(encoding="utf-8") == "[00:12.30] the line as fetched\n"

    def test_a_fresh_sidecar_still_lands(self, tmp_path):
        dl = _make_download(tmp_path, skip_existing=True)
        destination = tmp_path / "Song.flac"

        assert dl._move_lyrics(_fetched_lyrics(tmp_path), destination) is True
        assert (tmp_path / "Song.lrc").read_text(encoding="utf-8") == "[00:12.30] the line as fetched\n"

    def test_an_empty_leftover_sidecar_is_finished(self, tmp_path):
        # An interrupted write left nothing behind: completing it is not
        # replacing anybody's work.
        dl = _make_download(tmp_path, skip_existing=True)
        destination = tmp_path / "Song.flac"
        (tmp_path / "Song.lrc").touch()

        assert dl._move_lyrics(_fetched_lyrics(tmp_path), destination) is True
        assert (tmp_path / "Song.lrc").read_text(encoding="utf-8") == "[00:12.30] the line as fetched\n"

    def test_a_txt_sidecar_follows_the_same_rule(self, tmp_path):
        dl = _make_download(tmp_path, skip_existing=True)
        destination = tmp_path / "Song.flac"
        sidecar = tmp_path / "Song.txt"
        sidecar.write_text("words the user wrote\n", encoding="utf-8")

        assert dl._move_lyrics(_fetched_lyrics(tmp_path), destination, suffix=".txt") is True
        assert sidecar.read_text(encoding="utf-8") == "words the user wrote\n"
