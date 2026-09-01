"""Two names that are one file to the filesystem have to read as one to us.

The in-flight claim set, and the numbered-copy scan that reads it, compared
paths as exact strings. A filesystem does not: macOS and Windows fold case, and
a name typed as NFC (the API's spelling) is the same file as the NFD spelling a
tool that came from HFS+ wrote. Two tracks differing only that way therefore
each read the other's name as free, both claimed it, and the second finished
download was refused at the move with the occupied-destination error, so it
never reached the library.

Names on disk are untouched by all of this: only the comparison is folded. What
is written stays exactly what the template produced, or an existing library
would be spelled one way and looked up another (which is issue #16's mechanism).
"""

import pathlib
import threading
import unicodedata
from unittest.mock import MagicMock, patch

import pytest
from tidalapi.media import Track

from waves.download import Download
from waves.helper.path import name_comparison_key, path_file_uniquify


def _case_insensitive(tmp_path: pathlib.Path) -> bool:
    probe = tmp_path / "CaseProbe"
    probe.write_bytes(b"x")
    result = (tmp_path / "caseprobe").exists()
    probe.unlink()

    return result


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


def _run_pair(dl: Download, destinations: dict[int, pathlib.Path]) -> dict[int, tuple]:
    barrier = threading.Barrier(2, timeout=10)

    def _extras(*args, **kwargs):
        barrier.wait()

    dl._handle_metadata_and_extras = _extras

    results: dict[int, tuple] = {}

    def _run(track_id: int) -> None:
        results[track_id] = dl._perform_actual_download(
            media=_track(track_id),
            path_media_dst=destinations[track_id],
            stream_manifest=MagicMock(),
            do_flac_extract=False,
            is_parent_album=False,
            media_stream=None,
        )

    threads = [threading.Thread(target=_run, args=(track_id,)) for track_id in destinations]

    for thread in threads:
        thread.start()

    for thread in threads:
        thread.join(timeout=20)

    return results


class TestClaimsAreComparedTheWayAFilesystemCompares:
    def test_a_claim_covers_the_other_case(self, tmp_path):
        song = tmp_path / "Intro.flac"

        assert path_file_uniquify(song, names_taken={str(tmp_path / "intro.flac")}).name == "Intro_01.flac"

    def test_a_claim_covers_the_other_normalization(self, tmp_path):
        # "Café" written as e + combining acute, claimed as the precomposed é.
        decomposed = tmp_path / unicodedata.normalize("NFD", "Café.flac")
        precomposed = tmp_path / unicodedata.normalize("NFC", "Café.flac")

        picked = path_file_uniquify(decomposed, names_taken={str(precomposed)})

        assert unicodedata.normalize("NFC", picked.name) == "Café_01.flac"
        assert picked.name == unicodedata.normalize("NFD", picked.name), "the spelling asked for is the one written"

    def test_an_unrelated_claim_still_leaves_the_name_free(self, tmp_path):
        song = tmp_path / "Intro.flac"

        assert path_file_uniquify(song, names_taken={str(tmp_path / "Outro.flac")}) == song

    def test_the_comparison_key_does_not_change_the_name(self, tmp_path):
        # The key is for looking up, never for writing: a name written folded
        # or renormalized would orphan a library spelled the other way.
        song = tmp_path / "Café.flac"
        picked = path_file_uniquify(song, names_taken={str(song)})

        assert picked.name == "Café_01.flac"
        assert name_comparison_key(str(song)) != str(song)


class TestTheNumberedCopyScanMatchesTheSameWay:
    def test_a_copy_written_in_the_other_normalization_is_found(self, tmp_path):
        # The numbered copy on disk carries the decomposed spelling (a library
        # carried over from HFS+, or written by another tool); the template
        # produces the precomposed one. Missing it fetched the track again.
        dl = _make_download(tmp_path)
        base = tmp_path / unicodedata.normalize("NFC", "Café.flac")
        base.write_bytes(b"x")
        sibling = tmp_path / unicodedata.normalize("NFD", "Café_01.flac")
        sibling.write_bytes(b"x")

        ids = {"NFC": "123", "NFD": "456"}

        def _read_item_id(path_file) -> str:
            name = pathlib.Path(path_file).name

            return ids["NFD"] if "_01" in name else ids["NFC"]

        with patch("waves.download.read_item_id", _read_item_id):
            assert dl._existing_same_item_at(base, _track(456)) == sibling


class TestCaseTwinsUnderConcurrency:
    def test_two_tracks_differing_only_in_case_both_land(self, tmp_path):
        if not _case_insensitive(tmp_path):
            pytest.skip("filesystem is case-sensitive; the two names are genuinely two files there")

        dl = _make_download(tmp_path)
        results = _run_pair(dl, {111: tmp_path / "Intro.flac", 222: tmp_path / "intro.flac"})

        assert all(ok for ok, _ in results.values()), "a finished download must never be dropped"

        paths = [path for _, path in results.values()]
        assert len({name_comparison_key(str(p)) for p in paths}) == 2, "one file cannot hold two tracks"
        assert {p.read_bytes() for p in paths} == {b"id-111", b"id-222"}
        assert dl._names_reserved == {}
