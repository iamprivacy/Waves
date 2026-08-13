"""The ISRC duplicate scan matches recordings, never file names.

WHAT THIS FENCES OFF
--------------------
"Skip songs you already have" (issue #18) has one dangerous failure mode: a
wrong match silently loses a song, because the download is skipped and nothing
says so. So the matching must be identity-based (ISRC, the code for a specific
recording) and never inferred from how a file is named or numbered. These tests
pin that:

* a differently named, differently numbered copy of the same recording matches;
* a same-named different recording (a live take, an alternate cut) does NOT;
* an untagged file never matches, so an untagged library produces no skips;
* a deleted copy stops counting the moment it is gone;
* the cache is invalidated when the folder changes, so a file added after a
  scan is seen (the always-on freshness rule: no cache may need a restart).

The tag reader is injected, so no real audio files are needed.
"""

from __future__ import annotations

import os

from tidaler.recording_scan import RecordingScan, is_audio, normalise_isrc

ISRC_A = "GBAYE1234567"
ISRC_B = "USRC17607839"


def _scan(tags: dict[str, str | None]) -> RecordingScan:
    """A scan whose tag reader answers from ``tags`` (path -> ISRC)."""
    return RecordingScan(read_isrc=lambda path: tags.get(os.path.normcase(path)))


def _write(path, text: str = "audio") -> str:
    path.write_text(text)
    return os.path.normcase(str(path))


def test_normalise_accepts_punctuated_isrcs_and_rejects_non_isrcs():
    assert normalise_isrc("gb-aye-12-34567") == ISRC_A
    assert normalise_isrc(" GBAYE1234567 ") == ISRC_A
    assert normalise_isrc(["gb aye 12 34567"]) == ISRC_A
    for bad in ("", None, "SHORT", "WAY-TOO-LONG-FOR-AN-ISRC", 12345):
        assert normalise_isrc(bad) is None


def test_audio_predicate_ignores_non_audio_neighbours():
    assert is_audio("01. Song.flac") and is_audio("Song.M4A")
    assert not is_audio("cover.jpg") and not is_audio("album.m3u8") and not is_audio("notes.txt")


def test_same_recording_matches_across_folders_names_and_track_numbers(tmp_path):
    standard = tmp_path / "Artist" / "Album"
    deluxe = tmp_path / "Artist" / "Album (Deluxe)"
    standard.mkdir(parents=True)
    deluxe.mkdir(parents=True)
    owned = _write(standard / "03. Song.flac")
    scan = _scan({owned: ISRC_A})
    # A wholly different file name and track number, same recording.
    assert scan.have(str(tmp_path / "Artist"), ISRC_A)
    assert scan.path_for(str(tmp_path / "Artist"), "gb-aye-12-34567") == str(standard / "03. Song.flac")


def test_a_different_recording_with_the_same_name_never_matches(tmp_path):
    folder = tmp_path / "Artist" / "Album"
    folder.mkdir(parents=True)
    live = _write(folder / "03. Song.flac")
    scan = _scan({live: ISRC_B})  # same title on disk, different recording
    assert not scan.have(str(tmp_path / "Artist"), ISRC_A)


def test_an_untagged_file_never_matches(tmp_path):
    folder = tmp_path / "Artist" / "Album"
    folder.mkdir(parents=True)
    _write(folder / "03. Song.flac")
    scan = _scan({})  # no ISRC readable anywhere
    assert not scan.have(str(tmp_path / "Artist"), ISRC_A)
    # And no ISRC to ask about is never a match either.
    assert not scan.have(str(tmp_path / "Artist"), None)


def test_a_deleted_copy_stops_counting(tmp_path):
    folder = tmp_path / "Artist" / "Album"
    folder.mkdir(parents=True)
    path = folder / "03. Song.flac"
    owned = _write(path)
    scan = _scan({owned: ISRC_A})
    root = str(tmp_path / "Artist")
    assert scan.have(root, ISRC_A)
    path.unlink()
    # Still cached, but the live existence check must veto it.
    assert not scan.have(root, ISRC_A)


def test_a_zero_byte_file_is_not_a_copy(tmp_path):
    folder = tmp_path / "Artist" / "Album"
    folder.mkdir(parents=True)
    owned = _write(folder / "03. Song.flac", "")
    scan = _scan({owned: ISRC_A})
    assert not scan.have(str(tmp_path / "Artist"), ISRC_A)


def test_a_file_added_after_a_scan_is_seen_without_a_restart(tmp_path):
    root = tmp_path / "Artist"
    first = root / "Album"
    first.mkdir(parents=True)
    tags: dict[str, str | None] = {}
    scan = RecordingScan(read_isrc=lambda path: tags.get(os.path.normcase(path)))
    assert not scan.have(str(root), ISRC_A)  # cold scan, nothing there
    later = root / "Album (Deluxe)"
    later.mkdir()
    tags[_write(later / "07. Song.flac")] = ISRC_A
    # A new folder changes the tree signature, so the cached map is rebuilt.
    assert scan.have(str(root), ISRC_A)


def test_forget_drops_the_cache(tmp_path):
    root = tmp_path / "Artist"
    folder = root / "Album"
    folder.mkdir(parents=True)
    tags = {_write(folder / "03. Song.flac"): ISRC_A}
    reads: list[str] = []

    def read(path):
        reads.append(path)
        return tags.get(os.path.normcase(path))

    scan = RecordingScan(read_isrc=read)
    assert scan.have(str(root), ISRC_A)
    before = len(reads)
    assert scan.have(str(root), ISRC_A)
    assert len(reads) == before, "a cached scan must not re-read tags"
    scan.forget()
    assert scan.have(str(root), ISRC_A)
    assert len(reads) > before, "forget() must force a fresh read"


def test_an_unreadable_root_never_gates_a_download(tmp_path):
    scan = _scan({})
    assert not scan.have(str(tmp_path / "does-not-exist"), ISRC_A)
