"""The duplicate-recording skip only fires where it is meant to.

WHAT THIS FENCES OFF
--------------------
"Skip songs you already have" (issue #18) is the one gate in the download path
that can skip a track nothing else would, so every carve-off around it matters:

* OFF by default. With the setting off, nothing is scanned and nothing skipped,
  so every album folder stays a complete album.
* Never inside a "best of both" merge. A merge exists to assemble one complete
  folder at the best available quality, and skipping its shared tracks would
  both hole the album and forgo the upgrades it was built to fetch. A merge
  member is recognised by ``waves_identity_id``.
* Never for a copy already in the destination folder. That case is skip
  existing's, reported through the normal path, so the two can't both claim it.
* Never scans the download root. The root is the whole library; the scan scope
  is the artist folder this job writes into and no wider.
* No ISRC, no skip.

The verdict method is exercised directly on a bare instance (no Qt, no network,
no real files): everything it consults is injected.
"""

from __future__ import annotations

import os
import pathlib
import types

from tidalapi import Track

from tidaler.waves_ui.backend import _TrackedDownload

ISRC = "GBAYE1234567"


class _Scan:
    """Stand-in RecordingScan: answers with ``found`` and records the roots it
    was asked about, so a test can assert nothing was scanned at all."""

    def __init__(self, found: str | None = None) -> None:
        self.found = found
        self.roots: list[str] = []

    def path_for(self, root: str, isrc: str | None) -> str | None:
        self.roots.append(root)
        return self.found


def _settings():
    data = types.SimpleNamespace(
        album_track_num_pad_min=2,
        filename_delimiter_artist=", ",
        filename_delimiter_album_artist=", ",
        use_primary_album_artist=True,
        filename_illegal_replacement="-",
        filename_illegal_map=None,
    )
    return types.SimpleNamespace(data=data)


def _track(tmp_path, *, isrc: str | None = ISRC, identity: str | None = None):
    """A REAL Track: the formatter's token lookups are isinstance-gated, so a
    plain stand-in would resolve no tokens and every path assertion here would
    pass without proving anything.

    roles=None is how tidalapi leaves a main artist carrying no explicit role,
    which name_builder_album_artist treats as main.
    """
    artist = types.SimpleNamespace(name="Artist", id=1, roles=None)
    track = Track.__new__(Track)
    track.id = 99
    track.name = "Song"
    track.artist = artist
    track.artists = [artist]
    track.track_num = 3
    track.volume_num = 1
    track.duration = 200
    track.explicit = False
    track.isrc = isrc
    track.version = None
    track.album = types.SimpleNamespace(
        name="Album (Deluxe)",
        artist=artist,
        artists=[artist],
        id=10,
        num_tracks=12,
        num_volumes=1,
        release_date=None,
        explicit=False,
    )
    if identity is not None:
        track.waves_identity_id = identity
    return track


def _gate(tmp_path, *, enabled=True, scan=None, isrc=ISRC, identity=None):
    dl = _TrackedDownload.__new__(_TrackedDownload)
    dl.settings = _settings()
    dl.path_base = str(tmp_path)
    dl._skip_duplicate_recordings = enabled
    dl._recording_scan = scan
    return dl, _track(tmp_path, isrc=isrc, identity=identity)


TEMPLATE = "{album_artist}/{album_title}/{album_track_num}. {track_title}"


def test_a_copy_in_another_album_folder_is_a_duplicate(tmp_path):
    owned = tmp_path / "Artist" / "Album" / "03. Song.flac"
    owned.parent.mkdir(parents=True)
    owned.write_text("audio")
    scan = _Scan(str(owned))
    dl, track = _gate(tmp_path, scan=scan)
    assert dl._duplicate_recording_owned(track, TEMPLATE) is True
    # Scanned the artist folder, never the download root.
    assert scan.roots and all(os.path.normcase(r) != os.path.normcase(str(tmp_path)) for r in scan.roots)
    assert os.path.normcase(scan.roots[0]) == os.path.normcase(str(tmp_path / "Artist"))


def test_off_by_default_scans_nothing(tmp_path):
    scan = _Scan(str(tmp_path / "Artist" / "Album" / "03. Song.flac"))
    dl, track = _gate(tmp_path, enabled=False, scan=scan)
    assert dl._duplicate_recording_owned(track, TEMPLATE) is False
    assert scan.roots == [], "with the setting off nothing may be scanned at all"


def test_a_merge_member_is_never_skipped(tmp_path):
    owned = tmp_path / "Artist" / "Album" / "03. Song.flac"
    owned.parent.mkdir(parents=True)
    owned.write_text("audio")
    scan = _Scan(str(owned))
    dl, track = _gate(tmp_path, scan=scan, identity="10")
    # The gate is reached through _ownership_verdict, which is what applies the
    # merge carve-out; prove it there, with no ownership record for this id.
    dl._ownership_of = lambda _id: None
    dl._target_rank = 5
    assert dl._ownership_verdict(track, TEMPLATE) is None
    assert scan.roots == [], "a merge plan must not even consult the scan"


def test_a_plain_track_with_a_duplicate_elsewhere_skips(tmp_path):
    owned = tmp_path / "Artist" / "Album" / "03. Song.flac"
    owned.parent.mkdir(parents=True)
    owned.write_text("audio")
    dl, track = _gate(tmp_path, scan=_Scan(str(owned)))
    dl._ownership_of = lambda _id: None
    dl._target_rank = 5
    assert dl._ownership_verdict(track, TEMPLATE) == "skip"


def test_a_copy_in_the_destination_folder_is_left_to_skip_existing(tmp_path):
    # Same folder this job writes into: not this gate's business.
    destination = tmp_path / "Artist" / "Album (Deluxe)"
    destination.mkdir(parents=True)
    owned = destination / "03. Song.flac"
    owned.write_text("audio")
    dl, track = _gate(tmp_path, scan=_Scan(str(owned)))
    assert dl._duplicate_recording_owned(track, TEMPLATE) is False


def test_no_isrc_no_skip(tmp_path):
    scan = _Scan(str(tmp_path / "Artist" / "Album" / "03. Song.flac"))
    dl, track = _gate(tmp_path, scan=scan, isrc=None)
    assert dl._duplicate_recording_owned(track, TEMPLATE) is False
    assert scan.roots == []


def test_a_template_that_puts_albums_at_the_root_is_refused(tmp_path):
    # "{album_title}/..." makes the scan scope the download root itself, i.e.
    # the whole library. Refuse rather than scan everything.
    scan = _Scan(str(tmp_path / "Album" / "03. Song.flac"))
    dl, track = _gate(tmp_path, scan=scan)
    assert dl._duplicate_recording_owned(track, "{album_title}/{album_track_num}. {track_title}") is False
    assert scan.roots == []


def test_a_missing_template_is_not_a_skip(tmp_path):
    dl, track = _gate(tmp_path, scan=_Scan("anything"))
    assert dl._duplicate_recording_owned(track, None) is False


def test_an_owned_record_still_wins_the_verdict(tmp_path):
    # The ISRC gate only runs on an ownership MISS; an owned copy keeps its
    # existing skip/force verdict untouched.
    dl, track = _gate(tmp_path, scan=_Scan("anything"))
    dl._target_rank = 5
    dl._ownership_of = lambda _id: {"owned": True, "path": str(tmp_path / "x.flac"), "quality_rank": 9}
    assert dl._ownership_verdict(track, TEMPLATE) == "skip"
    dl._ownership_of = lambda _id: {"owned": True, "path": str(tmp_path / "x.flac"), "quality_rank": 1}
    assert dl._ownership_verdict(track, TEMPLATE) == "force"


def test_a_scan_failure_never_gates(tmp_path):
    class _Boom:
        def path_for(self, root, isrc):
            raise OSError("share went away")

    dl, track = _gate(tmp_path, scan=_Boom())
    dl._ownership_of = lambda _id: None
    dl._target_rank = 5
    # Not a skip, and not an exception escaping into the download path.
    try:
        verdict = dl._ownership_verdict(track, TEMPLATE)
    except OSError:
        raise AssertionError("a scan failure must not escape the gate") from None
    assert verdict is None


def test_the_destination_resolver_agrees_with_the_template(tmp_path):
    dl, track = _gate(tmp_path, scan=_Scan(None))
    resolved = dl._destination_dir(track, TEMPLATE)
    assert resolved is not None
    assert pathlib.Path(resolved).name.startswith("Album (Deluxe)")
    assert pathlib.Path(resolved).parent.name == "Artist"
