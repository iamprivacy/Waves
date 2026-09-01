"""A merge member owned in a folder the sanitizer RENAMED still counts as owned.

WHAT THIS FENCES OFF
--------------------
A best-of-both merge member (``waves_identity_id`` set) may skip, or be forced
into an upgrade, only when the copy already on disk sits in THIS job's
destination folder. Two things have to agree for that check to ever fire:

* the ownership record stores the path ``item()`` actually WROTE, and the write
  runs every candidate through ``path_file_sanitize(candidate, adapt=True)``
  (``Download._destination_path``): a folder component over the 255-byte
  filesystem cap is truncated, control characters become ``_``, and on Windows
  reserved names, trailing dots and the 260 MAX_PATH cap rewrite it further;
* the gate (``_owned_at_destination`` / ``_destination_dir``) recomputes the
  destination folder for the same member and compares the two parents.

Since cf29d1f the gate asks the engine's own ``_destination_path``, so today
they agree. Nothing pinned it: a scratch mutation that dropped the sanitize
half of the destination decision survived the whole suite, because every
existing fixture used a folder name the sanitizer leaves alone. With the
sanitizer out of the gate's half, a record for a folder the sanitizer really
rewrites (a 300-character album title under the default ``[{album_year}]
{album_title}`` folder shape) compares against the untruncated spelling, the
verdict is thrown away, and the merge re-fetches (or the upgrade is lost) for
every song in that album, forever.

HOW THIS STAYS FIXED
--------------------
The "path the write produced" is NOT taken from the engine (that would be a
tautology under the very mutation this guards against): it is rebuilt from
the write's own recipe, ``format_path_media`` then ``path_file_sanitize``, and
a premise test proves that recipe differs from the plain template output. The
gate is the real ``_TrackedDownload`` code on a stand-in built without the
engine's network-touching ``__init__``, on a real temp library.
"""

from __future__ import annotations

import os
import pathlib
from datetime import datetime
from types import SimpleNamespace

import pytest
from tidalapi import Album, Track

from waves.download import Download
from waves.helper.path import format_path_media, path_file_sanitize
from waves.waves_ui.backend import _as_member_of, _TrackedDownload

_ARTIST = "Bright Eyes"
# 300 characters: over the 255-byte component cap on its own, and the default
# album folder shape then prefixes "[2011] " on top of whatever survives.
_LONG_TITLE = "Cassadaga " * 30
_TEMPLATE = "{artist_name}/[{album_year}] {album_title}/{album_track_num}. {artist_name} - {track_title}"


class _SettingsData:
    """The settings the destination decision reads, spelled out (no MagicMock)
    so the extension guess and the stand-in laundering are deterministic."""

    album_track_num_pad_min = 0
    filename_delimiter_artist = ", "
    filename_delimiter_album_artist = ", "
    filename_illegal_replacement = ""
    filename_illegal_map = None
    use_primary_album_artist = False
    download_dolby_atmos = False
    extract_flac = False
    video_convert_mp4 = False
    symlink_to_track = False


class _Settings:
    data = _SettingsData()


def _track(album_title: str, tid: str = "src-1") -> Track:
    t = Track.__new__(Track)
    t.id = tid
    t.name = "Song"
    t.version = None
    t.full_name = "Song"
    t.explicit = False
    t.track_num = 1
    t.volume_num = 1
    t.media_metadata_tags = ["LOSSLESS"]
    t.artists = [SimpleNamespace(name=_ARTIST)]
    t.artist = SimpleNamespace(name=_ARTIST)
    album = Album.__new__(Album)
    album.id = "alb-1"
    album.name = album_title
    album.artists = [SimpleNamespace(name=_ARTIST, roles=None)]
    album.artist = SimpleNamespace(name=_ARTIST)
    album.num_tracks = 1
    album.num_volumes = 1
    album.release_date = datetime(2011, 1, 1)
    t.album = album
    return t


def _member(album_title: str = _LONG_TITLE):
    src = _track(album_title)
    return _as_member_of(src, src.album, 1, 1, "id-1")


def _gate(base: pathlib.Path, records: dict) -> _TrackedDownload:
    """The real gate on a _TrackedDownload built without the engine __init__:
    it carries exactly what _destination_path reads (settings, path_base) so the
    folder it resolves is the engine's own decision, not a copy of it."""
    dl = _TrackedDownload.__new__(_TrackedDownload)
    dl._ownership_of = records.get
    dl._target_rank = 2
    dl.settings = _Settings()
    dl.path_base = str(base)
    dl.skip_existing = True
    dl._force_redownload = False
    return dl


def _unsanitized(base: pathlib.Path, media, extension: str = ".flac") -> pathlib.Path:
    """The plain template output joined onto the base: what the write would
    produce if it stopped BEFORE path_file_sanitize."""
    relative = format_path_media(
        _TEMPLATE,
        media,
        _SettingsData.album_track_num_pad_min,
        0,
        0,
        delimiter_artist=_SettingsData.filename_delimiter_artist,
        delimiter_album_artist=_SettingsData.filename_delimiter_album_artist,
        use_primary_album_artist=_SettingsData.use_primary_album_artist,
        tidy_spacing=True,
        illegal_replacement="",
        illegal_map=None,
    )
    return (base / (relative + extension)).absolute()


def _written(base: pathlib.Path, media) -> pathlib.Path:
    """The path item() writes, rebuilt from the write's own recipe (template,
    then the OS sanitizer with adapt=True) WITHOUT going through the engine, so
    an engine that stops sanitizing cannot make this side agree with it."""
    return pathlib.Path(path_file_sanitize(_unsanitized(base, media), adapt=True))


def _same(a: pathlib.Path, b: pathlib.Path) -> bool:
    return os.path.normcase(str(a)) == os.path.normcase(str(b))


# --------------------------------------------------------------------------- #
# Premises: the fixture really is one the sanitizer rewrites, and the recipe
# really is what the engine writes. Without these the tests below could pass
# on a folder the sanitizer leaves alone and prove nothing.
# --------------------------------------------------------------------------- #
def test_premise_the_long_album_folder_is_rewritten_by_the_sanitizer(tmp_path):
    member = _member()
    plain = _unsanitized(tmp_path, member)
    written = _written(tmp_path, member)
    assert not _same(plain.parent, written.parent), "the premise is wrong: the sanitizer left this folder alone"
    assert len(os.fsencode(written.parent.name)) <= 255
    assert len(os.fsencode(plain.parent.name)) > 255


def test_premise_the_engine_writes_the_sanitized_path(tmp_path):
    """Download._destination_path is the write's decision (item() reads it).
    On a fresh library it must equal the recipe: template, then sanitizer."""
    member = _member()
    engine = Download.__new__(Download)
    engine.settings = _Settings()
    engine.path_base = str(tmp_path)
    dst, _ext = engine._destination_path(member, _TEMPLATE, None)
    assert _same(dst, _written(tmp_path, member)), "the engine's destination is not the sanitized recipe"


# --------------------------------------------------------------------------- #
# The wire: a record holding the SANITIZED path is owned at the destination.
# --------------------------------------------------------------------------- #
def test_a_member_owned_in_the_truncated_folder_is_owned_at_the_destination(tmp_path):
    member = _member()
    written = _written(tmp_path, member)
    written.parent.mkdir(parents=True)
    written.write_bytes(b"x")
    rec = {"path": str(written), "quality_rank": 2}
    gate = _gate(tmp_path, {"id-1": rec})

    assert gate._owned_at_destination(rec, member, _TEMPLATE) is True


def test_a_member_owned_in_the_truncated_folder_gets_a_verdict(tmp_path):
    """The user-visible half: with the folder recognised, the gate answers
    'skip' at the run's quality and 'force' below it, instead of (None, None),
    which would re-fetch the song into the very folder it already sits in."""
    member = _member()
    written = _written(tmp_path, member)
    written.parent.mkdir(parents=True)
    written.write_bytes(b"x")

    current = {"path": str(written), "quality_rank": 2}
    verdict, rec = _gate(tmp_path, {"id-1": current})._ownership_decision(member, _TEMPLATE)
    assert verdict == "skip"
    assert rec is current

    stale = {"path": str(written), "quality_rank": 1}
    verdict, rec = _gate(tmp_path, {"id-1": stale})._ownership_decision(member, _TEMPLATE)
    assert verdict == "force"
    assert rec is stale


def test_the_placement_the_write_used_still_resolves_the_truncated_folder(tmp_path):
    """The gate is handed item()'s own placement; the folder does not depend
    on the list position here, so the recognised folder must be the same."""
    member = _member()
    written = _written(tmp_path, member)
    rec = {"path": str(written), "quality_rank": 2}
    gate = _gate(tmp_path, {"id-1": rec})
    placement = {"quality_audio": None, "list_position": 1, "list_total": 12}

    assert gate._owned_at_destination(rec, member, _TEMPLATE, placement) is True


# --------------------------------------------------------------------------- #
# The negatives: the untruncated spelling and a sibling folder are NOT the
# destination. Under the mutation this guards against, the first one would
# suddenly compare equal (the gate's half would be the untruncated spelling).
# --------------------------------------------------------------------------- #
def test_a_record_in_the_unsanitized_spelling_is_not_owned_at_the_destination(tmp_path):
    member = _member()
    plain = _unsanitized(tmp_path, member)
    rec = {"path": str(plain), "quality_rank": 2}
    gate = _gate(tmp_path, {"id-1": rec})

    assert gate._owned_at_destination(rec, member, _TEMPLATE) is False
    assert gate._ownership_decision(member, _TEMPLATE) == (None, None)


def test_a_record_in_a_sibling_folder_is_not_owned_at_the_destination(tmp_path):
    member = _member()
    written = _written(tmp_path, member)
    # Same artist folder, a differently spelled album folder beside the real
    # one (trimmed first so the sibling itself fits the component cap).
    sibling = written.parent.parent / (written.parent.name[:-12] + " (Deluxe)") / written.name
    sibling.parent.mkdir(parents=True)
    sibling.write_bytes(b"x")
    rec = {"path": str(sibling), "quality_rank": 2}
    gate = _gate(tmp_path, {"id-1": rec})

    assert gate._owned_at_destination(rec, member, _TEMPLATE) is False
    assert gate._ownership_decision(member, _TEMPLATE) == (None, None)


# --------------------------------------------------------------------------- #
# Control characters: format_path_media's per-component sanitizer already drops
# them on this platform, so path_file_sanitize has nothing left to rewrite in
# the folder name. The case is kept for a platform (or a future sanitizer)
# where the two halves diverge, and skips honestly where they do not.
# --------------------------------------------------------------------------- #
def test_a_member_owned_in_a_control_character_folder_is_owned_at_the_destination(tmp_path):
    member = _member("Album\x00Name")
    plain = _unsanitized(tmp_path, member)
    written = _written(tmp_path, member)
    if _same(plain.parent, written.parent):
        pytest.skip("path_file_sanitize leaves this folder unchanged on this platform")
    written.parent.mkdir(parents=True)
    written.write_bytes(b"x")
    rec = {"path": str(written), "quality_rank": 2}
    gate = _gate(tmp_path, {"id-1": rec})

    assert gate._owned_at_destination(rec, member, _TEMPLATE) is True
    assert gate._ownership_decision(member, _TEMPLATE)[0] == "skip"
