"""A merge member is gated against the folder the engine will really write to.

WHAT THIS FENCES OFF
--------------------
A best-of-both merge borrows the higher-quality copy of each song and writes it
into one assembled album folder. A member (``waves_identity_id`` set) may skip
only when the copy already on disk sits in THIS job's destination folder,
otherwise the skip leaves a hole in the merged album while the job reports done.

"THIS job's destination folder" was computed twice. The engine picks it with
``_keep_existing_layout``, which writes into an older folder spelling whenever
one already exists on disk (the pre-0.1.17 doubled-space name, or a name kept
from before an illegal-character setting changed), and it guesses the file
extension, which the Windows path cap can turn into a different parent folder.
The bridge re-derived the folder from the template with none of that: one tidy
spelling, a hardcoded ``.x`` where the extension goes. On a fresh library the
two agreed; on a library with any legacy spelling they diverged, so
``_owned_at_destination`` compared the ownership record against the wrong
folder, returned False, and the ownership verdict (the whole upgrade the run
was for) was thrown away. The album reported complete, every song was still the
old copy, and the merge plan was gone so a retry could not recover it.

The fix removes the second derivation: the bridge asks the engine
(``Download._destination_path``) the same question the write asks, with the
same placement (quality and list position) ``item()`` was called with.

HOW THIS STAYS FIXED
--------------------
Real ``tidalapi.Track``/``Album``, a real ``Download`` with a real temp library
on disk, and the real ``_TrackedDownload`` gate as an unbound method. The
legacy folder is really created on disk so ``_keep_existing_layout`` really
diverts into it, and the test asserts the engine's own write target and the
gate's target are the SAME path, not two hand-written strings.
"""

from __future__ import annotations

import pathlib
import threading
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

from tidalapi import Album, Track

from tidaler.download import Download
from tidaler.waves_ui.backend import _as_member_of, _TrackedDownload

_TITLE = "The Better Life : Dead Love"  # the colon is stripped on disk
_LEGACY_DIR = "[2011] The Better Life  Dead Love"  # doubled space, pre-0.1.17
_TIDY_DIR = "[2011] The Better Life Dead Love"
_ARTIST = "Bright Eyes"
# The real default album template shape (model/cfg.py), so the destination has
# the artist/album folder structure a legacy spelling actually lives in. The
# explicit suffix is dropped: this album is not explicit and the token would
# only add noise to the folder name the test reads.
_TEMPLATE = "{artist_name}/[{album_year}] {album_title}/{album_track_num}. {artist_name} - {track_title}"


def _track(tid="src-1") -> Track:
    t = Track.__new__(Track)
    t.id = tid
    t.name = "Song"
    t.version = None
    t.full_name = "Song"
    t.explicit = False
    t.track_num = 1
    t.volume_num = 1
    t.media_metadata_tags = []
    t.artists = [SimpleNamespace(name="Bright Eyes")]
    t.artist = SimpleNamespace(name="Bright Eyes")
    album = Album.__new__(Album)
    album.id = "alb-1"
    album.name = _TITLE
    album.artists = [SimpleNamespace(name="Bright Eyes", roles=None)]
    album.artist = SimpleNamespace(name="Bright Eyes")
    album.num_tracks = 1
    album.num_volumes = 1
    album.release_date = datetime(2011, 1, 1)
    t.album = album
    return t


def _engine(base: pathlib.Path) -> Download:
    dl = Download(
        tidal_obj=MagicMock(),
        skip_existing=True,
        path_base=str(base),
        fn_logger=MagicMock(),
        progress=MagicMock(),
    )
    dl.settings = MagicMock()
    dl.settings.data.album_track_num_pad_min = 0
    dl.settings.data.filename_delimiter_artist = ", "
    dl.settings.data.filename_delimiter_album_artist = ", "
    dl.settings.data.use_primary_album_artist = False
    dl.settings.data.filename_illegal_replacement = ""
    dl.settings.data.filename_illegal_map = None
    dl.settings.data.symlink_to_track = False
    dl.event_abort = threading.Event()
    dl.event_run = threading.Event()
    dl.event_run.set()
    return dl


def _gate(base: pathlib.Path, records: dict) -> _TrackedDownload:
    """The real gate, on a _TrackedDownload built without the engine's
    network-touching __init__, sharing the engine's settings and path_base so
    it resolves the destination exactly as the engine does."""
    dl = _TrackedDownload.__new__(_TrackedDownload)
    dl._ownership_of = records.get
    dl._target_rank = 3  # a HI-RES run
    dl.settings = _engine(base).settings
    dl.path_base = str(base)
    dl.skip_existing = True
    dl._force_redownload = False
    return dl


def _member():
    m = _as_member_of(_track(), _track().album, 1, 1, "id-1")
    return m


# --------------------------------------------------------------------------- #
# The premise: the engine really does divert into the legacy folder.
# --------------------------------------------------------------------------- #
def test_the_engine_writes_into_the_legacy_folder_when_one_exists(tmp_path):
    (tmp_path / "Bright Eyes" / _LEGACY_DIR).mkdir(parents=True)
    engine = _engine(tmp_path)
    dst, _ext = engine._destination_path(_member(), _TEMPLATE, None)
    assert dst.parent.name == _LEGACY_DIR, "the premise is wrong: no legacy diversion happened"


def test_a_fresh_library_writes_into_the_tidy_folder(tmp_path):
    engine = _engine(tmp_path)
    dst, _ext = engine._destination_path(_member(), _TEMPLATE, None)
    assert dst.parent.name == _TIDY_DIR


# --------------------------------------------------------------------------- #
# The fix: the gate's folder and the engine's write folder are the same.
# --------------------------------------------------------------------------- #
def test_the_gate_resolves_the_same_folder_the_engine_writes_to(tmp_path):
    (tmp_path / "Bright Eyes" / _LEGACY_DIR).mkdir(parents=True)
    engine = _engine(tmp_path)
    written = engine._destination_path(_member(), _TEMPLATE, None)[0].parent
    gate = _gate(tmp_path, {})
    assert gate._destination_dir(_member(), _TEMPLATE) == written


def test_a_member_owned_in_the_legacy_folder_is_gated_as_owned(tmp_path):
    """The record's file sits in the doubled-space folder the engine will write
    into. Before the fix the gate looked at the tidy folder, missed it, and
    threw the verdict away; now it forces the upgrade."""
    legacy = tmp_path / "Bright Eyes" / _LEGACY_DIR
    legacy.mkdir(parents=True)
    on_disk = legacy / "1 Song.flac"
    on_disk.write_bytes(b"x")
    records = {"id-1": {"path": str(on_disk), "quality_rank": 1}}  # owned at HIGH, run targets HI-RES
    gate = _gate(tmp_path, records)
    assert gate._ownership_verdict(_member(), _TEMPLATE) == "force"


def test_a_member_owned_in_the_legacy_folder_at_top_quality_skips(tmp_path):
    """Same folder resolution, the other verdict: already at the run's quality,
    so the merge correctly skips it instead of re-fetching."""
    legacy = tmp_path / "Bright Eyes" / _LEGACY_DIR
    legacy.mkdir(parents=True)
    on_disk = legacy / "1 Song.flac"
    on_disk.write_bytes(b"x")
    records = {"id-1": {"path": str(on_disk), "quality_rank": 3}}
    gate = _gate(tmp_path, records)
    assert gate._ownership_verdict(_member(), _TEMPLATE) == "skip"


def test_a_member_owned_in_a_different_folder_is_still_not_skipped(tmp_path):
    """The rule that armed the whole check: an owned copy in ANOTHER folder (a
    playlist, the standard edition) must not satisfy a merge member, or the
    assembled album gets a hole."""
    (tmp_path / "Bright Eyes" / _LEGACY_DIR).mkdir(parents=True)
    elsewhere = tmp_path / "Playlists" / "Mix" / "1 Song.flac"
    elsewhere.parent.mkdir(parents=True)
    elsewhere.write_bytes(b"x")
    records = {"id-1": {"path": str(elsewhere), "quality_rank": 1}}
    gate = _gate(tmp_path, records)
    assert gate._ownership_verdict(_member(), _TEMPLATE) is None


# --------------------------------------------------------------------------- #
# Finding 10: the destination is resolved with the real guessed extension, not
# a hardcoded two-character ".x". At the Windows path cap the two lengths
# truncate the name differently and can land in different parent folders. The
# bridge asking the engine gets the real extension by construction; this pins
# that the file the gate resolves ends in a real audio extension.
# --------------------------------------------------------------------------- #
def test_the_gate_resolves_a_real_audio_extension_not_a_placeholder(tmp_path):
    engine = _engine(tmp_path)
    _dst, extension = engine._destination_path(_member(), _TEMPLATE, None)
    assert extension not in ("", ".x"), "the destination still carries the placeholder extension"
    assert extension.startswith("."), extension


def test_the_placement_item_passed_reaches_the_engine(tmp_path):
    """The list position rides in the template through {album_track_num}; a job
    that passes list_position must resolve the same numbered file the write
    does, so the gate is handed item()'s own placement."""
    engine = _engine(tmp_path)
    numbered = engine._destination_path(_member(), _TEMPLATE, None, 4, 10)[0]
    gate = _gate(tmp_path, {})
    placement = {"quality_audio": None, "list_position": 4, "list_total": 10}
    assert gate._destination_dir(_member(), _TEMPLATE, placement) == numbered.parent


def test_an_unresolvable_template_gates_nothing_rather_than_crashing(tmp_path):
    gate = _gate(tmp_path, {"id-1": {"path": str(tmp_path / "x.flac"), "quality_rank": 1}})
    assert gate._owned_at_destination({"path": "/x.flac"}, _member(), "") is False
