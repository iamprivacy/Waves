"""The m3u writer may only write where this run actually put a file.

WHAT THIS FENCES OFF
--------------------
Writing the playlist REPLACES whatever ``_<Name>.m3u8`` a directory already
holds, and deliberately RETARGETS an older ``_<Name>.m3u`` spelling to replace
that instead when it finds one (so a library never ends up with two files for
one playlist). That is right for a folder this run filled, and wrong for every
other folder on the disk.

The scope was ``{p.parent for p in result_paths}``, and a result path is not
proof of a write. ``item()`` answers ``(True, <the file it found>)`` for a
track already on disk, which is correct for the m3u's CONTENTS (re-downloading
an album you already own still lists the whole album) but wrong for its scope.
With skip-existing on, which is the default, re-downloading an owned album
therefore handed the writer nothing but folders that pre-date this run, and it
replaced a playlist file it had no business touching. The folder could pre-date
Waves entirely.

The feature is off by default (``playlist_create``), so this never became a
field report; the docstring in the source claimed the scope was already bounded
to folders the run filled, which is what let it stay hidden.

HOW THIS STAYS FIXED
--------------------
The distinction is recorded where a file is actually created
(``_note_dir_filled``), not inferred at the writer from skip flags, and the
scope is narrowed in the path collection (``_dirs_this_run_filled``). The real
``playlist_populate`` writes into a real temp folder here; only the parts that
would reach the network are stood in for.
"""

from __future__ import annotations

import logging
import pathlib
import threading
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from tidalapi import Album, Track

from waves.constants import PLAYLIST_EXTENSION, PLAYLIST_EXTENSION_LEGACY, PLAYLIST_PREFIX
from waves.download import Download

_TITLE = "Album X"
_M3U8 = f"{PLAYLIST_PREFIX}{_TITLE}{PLAYLIST_EXTENSION}"
_M3U_LEGACY = f"{PLAYLIST_PREFIX}{_TITLE}{PLAYLIST_EXTENSION_LEGACY}"
_STRANGER = "a playlist this run never touched\n"


def _album() -> Album:
    a = Album.__new__(Album)
    a.name = _TITLE
    return a


def _engine(playlist_create: bool = True) -> Download:
    """The real engine with its network-touching constructor skipped, carrying
    only what the playlist step reads."""
    dl = Download.__new__(Download)
    dl.settings = SimpleNamespace(
        data=SimpleNamespace(
            playlist_create=playlist_create,
            filename_illegal_replacement="",
            filename_illegal_map=None,
        )
    )
    dl.fn_logger = logging.getLogger("test.playlist.scope")
    dl._dirs_filled = set()
    dl._dirs_filled_lock = threading.Lock()
    return dl


def _owned_album(
    tmp_path: pathlib.Path, *, legacy: bool = False
) -> tuple[pathlib.Path, list[pathlib.Path], pathlib.Path]:
    """A folder that already holds two tracks and a playlist file, none of it
    written by the run under test."""
    folder = tmp_path / "Marina" / "[2010] Album X"
    folder.mkdir(parents=True)
    tracks = [folder / "01. Song One.flac", folder / "02. Song Two.flac"]
    for track in tracks:
        track.write_bytes(b"\0")
    playlist = folder / (_M3U_LEGACY if legacy else _M3U8)
    playlist.write_text(_STRANGER, encoding="utf-8")
    return folder, tracks, playlist


# --------------------------------------------------------------------------- #
# The regression: a run that wrote nothing writes no playlist
# --------------------------------------------------------------------------- #
def test_a_run_that_skipped_every_track_leaves_the_playlist_alone(tmp_path):
    """Every track already on disk: item() reports each file it found, and not
    one of those folders was filled by this run."""
    folder, tracks, playlist = _owned_album(tmp_path)
    dl = _engine()

    dl._playlist_for_collection(_album(), "{album_track_num}. {track_title}", tracks)

    assert playlist.read_text(encoding="utf-8") == _STRANGER, "a playlist file this run never filled was replaced"
    assert sorted(p.name for p in folder.iterdir()) == sorted([_M3U8, *(t.name for t in tracks)]), "no new file"


def test_a_skip_only_run_does_not_retarget_the_legacy_playlist_name(tmp_path):
    """The legacy loop is the sharper edge: finding no ``.m3u8`` it goes looking
    for an older ``.m3u`` to replace instead, so a folder holding only the old
    spelling was the one most exposed."""
    folder, tracks, playlist = _owned_album(tmp_path, legacy=True)
    dl = _engine()

    dl._playlist_for_collection(_album(), "{album_track_num}. {track_title}", tracks)

    assert playlist.read_text(encoding="utf-8") == _STRANGER, "the older spelling was retargeted and replaced"
    assert not (folder / _M3U8).exists(), "and no second file was written beside it"


def test_the_playlist_is_still_written_where_a_track_did_land(tmp_path):
    """The fix must not cost the feature: one landed file makes the folder the
    writer's, and the playlist lists the whole album, the skipped tracks too."""
    folder, tracks, playlist = _owned_album(tmp_path)
    dl = _engine()
    landed = folder / "03. Song Three.flac"
    landed.write_bytes(b"\0")
    dl._note_dir_filled(landed)

    dl._playlist_for_collection(_album(), "{album_track_num}. {track_title}", [*tracks, landed])

    assert playlist.read_text(encoding="utf-8").splitlines() == [t.name for t in [*tracks, landed]]


def test_only_the_folder_this_run_filled_gets_a_playlist(tmp_path):
    """A collection can straddle two folders (a playlist download, a multi-disc
    album). Filling one says nothing about the other."""
    folder_owned, tracks_owned, playlist_owned = _owned_album(tmp_path)
    folder_new = tmp_path / "Marina" / "[2012] Album Y"
    folder_new.mkdir(parents=True)
    landed = folder_new / "01. Song Three.flac"
    landed.write_bytes(b"\0")

    dl = _engine()
    dl._note_dir_filled(landed)

    dl._playlist_for_collection(_album(), "{album_track_num}. {track_title}", [*tracks_owned, landed])

    assert playlist_owned.read_text(encoding="utf-8") == _STRANGER, "the untouched folder kept its file"
    assert (folder_new / _M3U8).read_text(encoding="utf-8").splitlines() == [landed.name]


def test_the_setting_still_gates_everything(tmp_path):
    folder, tracks, playlist = _owned_album(tmp_path)
    dl = _engine(playlist_create=False)
    landed = folder / "03. Song Three.flac"
    landed.write_bytes(b"\0")
    dl._note_dir_filled(landed)

    dl._playlist_for_collection(_album(), "{album_track_num}. {track_title}", [*tracks, landed])

    assert playlist.read_text(encoding="utf-8") == _STRANGER


# --------------------------------------------------------------------------- #
# The distinction is made at the source, not guessed at the writer
# --------------------------------------------------------------------------- #
def test_a_reported_path_is_not_proof_this_run_wrote_it(tmp_path):
    """``_landed_paths`` keeps reporting the skipped file, because the m3u's
    contents need it. Only the scope narrows."""
    folder, tracks, _playlist = _owned_album(tmp_path)
    dl = _engine()

    assert dl._dirs_this_run_filled(tracks) == set()

    dl._note_dir_filled(tracks[0])
    assert dl._dirs_this_run_filled(tracks) == {folder}


def test_a_directory_filled_elsewhere_is_not_in_scope_by_itself(tmp_path):
    """Symlinking moves the audio into a track folder no result path names.
    Filling it must not put a playlist there."""
    dl = _engine()
    track_dir = tmp_path / "Tracks"
    track_dir.mkdir()
    dl._note_dir_filled(track_dir / "Song.flac")

    assert dl._dirs_this_run_filled([tmp_path / "Playlists" / "Song.flac"]) == set()


def test_an_on_disk_skip_reports_its_path_and_fills_nothing(tmp_path):
    """The engine's own skip, driven through the real ``item()``: it answers
    (True, <path>) so the caller records the file, and nothing was written, so
    no folder is claimed."""
    dl = _engine()
    dl.event_abort = threading.Event()
    dst = tmp_path / "Marina" / "01. Song One.flac"
    dst.parent.mkdir(parents=True)
    dst.write_bytes(b"\0")
    media = Track.__new__(Track)
    media.audio_modes = None

    with (
        patch.object(Download, "_rate_limit_pause", lambda *a, **k: None),
        patch.object(Download, "_validate_and_prepare_media", return_value=media),
        patch.object(Download, "_prepare_file_paths_and_skip_logic", return_value=(dst, "", True, False)),
    ):
        ok, path = dl.item("{track_title}", media=media)

    assert (ok, pathlib.Path(path)) == (True, dst), "a skipped file is still reported, for the m3u's contents"
    assert dl._dirs_filled == set(), "but the folder holding it was not filled by this run"
    assert dl._dirs_this_run_filled([pathlib.Path(path)]) == set()


def test_a_symlink_left_behind_does_fill_its_folder(tmp_path):
    """The one skip that still creates a file: the audio was already in the
    track folder, so nothing is downloaded, but the playlist folder gets a
    symlink and that folder really is this run's to write in."""
    dl = _engine()
    playlist_dir = tmp_path / "Playlists" / "Mix"
    track_dir = tmp_path / "Tracks"
    playlist_dir.mkdir(parents=True)
    track_dir.mkdir()
    path_src = playlist_dir / "01. Song One.flac"
    path_dst = track_dir / "Song One.flac"
    path_dst.write_bytes(b"\0")

    with patch.object(Download, "_ensure_directory", lambda *a, **k: None):
        dl._symlink_after_move(path_src, path_dst, skip_file=True, skip_symlink=False, overwrite=False)

    assert path_src.is_symlink()
    assert dl._dirs_filled == {playlist_dir}
    assert dl._dirs_this_run_filled([path_src]) == {playlist_dir}


@pytest.mark.parametrize("existing", [_M3U8, _M3U_LEGACY])
def test_the_writer_still_replaces_its_own_playlist_when_the_run_filled_the_folder(tmp_path, existing):
    """The behaviour being fenced is the SCOPE, not the replace: a folder this
    run filled still gets its playlist rewritten, old spelling and all."""
    folder = tmp_path / "Marina" / "[2010] Album X"
    folder.mkdir(parents=True)
    (folder / existing).write_text("stale\n", encoding="utf-8")
    landed = folder / "01. Song One.flac"
    landed.write_bytes(b"\0")

    dl = _engine()
    dl._note_dir_filled(landed)
    dl._playlist_for_collection(_album(), "{album_track_num}. {track_title}", [landed])

    assert (folder / existing).read_text(encoding="utf-8").splitlines() == [landed.name]
