"""Audit batch 7: the T4 notes (findings 65-70).

65: the stage-and-swap crash-safety promise is now real (fsync before rename)
    and both existence gates reject zero-byte truncation artifacts.
66: the updater's tree swap confirms the executable exists before discarding
    the backup, and restores the old install when it does not.
67: the dead recentlyAddedLoaded / loadRecentlyAdded pair is gone from the
    bridge and from BRIDGE.md.
68: the video preview passes the same padding downloads use.
69: ArtCard no longer runs collection ownership rollups whose results can
    never render.
70: QueueStack's marching step is gated on visible, so idle download controls
    stop re-evaluating a binding 20 times a second.
"""

import os
import pathlib

import pytest

from tidaler.download import Download
from tidaler.helper.path import check_file_exists
from tidaler.ownership import OwnershipStore
from tidaler.waves_ui.updater import AppUpdater, UpdaterError

ROOT = pathlib.Path(__file__).resolve().parent.parent
BACKEND_SRC = (ROOT / "tidaler" / "waves_ui" / "backend.py").read_text()
MAIN_QML = (ROOT / "tidaler" / "waves_ui" / "qml" / "Main.qml").read_text()
BRIDGE_MD = (ROOT / "tidaler" / "waves_ui" / "BRIDGE.md").read_text()


# ---------------------------------------------------------------- finding 65


class _CopyHost:
    _COPY_BUFFER_BYTES = Download._COPY_BUFFER_BYTES


def test_copy_file_contents_fsyncs_before_returning(tmp_path, monkeypatch):
    """The bytes must be durable before the caller renames the temp over the
    final name, or a power cut can leave a truncated file under a trusted name."""
    src = tmp_path / "src.bin"
    src.write_bytes(b"x" * 1024)
    dst = tmp_path / "dst.bin"
    synced: list[int] = []
    real_fsync = os.fsync

    def spy_fsync(fd):
        synced.append(fd)
        real_fsync(fd)

    monkeypatch.setattr("tidaler.download.os.fsync", spy_fsync)
    Download._copy_file_contents(_CopyHost(), src, dst)
    assert synced, "destination file was never fsynced"
    assert dst.read_bytes() == b"x" * 1024


def test_check_file_exists_rejects_zero_byte_file(tmp_path):
    empty = tmp_path / "track.flac"
    empty.touch()
    assert check_file_exists(empty) is False
    empty.write_bytes(b"audio")
    assert check_file_exists(empty) is True


def test_check_file_exists_extension_ignore_rejects_zero_byte(tmp_path):
    (tmp_path / "track.m4a").touch()
    assert check_file_exists(tmp_path / "track.flac", extension_ignore=True) is False
    (tmp_path / "track.m4a").write_bytes(b"audio")
    assert check_file_exists(tmp_path / "track.flac", extension_ignore=True) is True


def test_ownership_skips_zero_byte_copy(tmp_path):
    store = OwnershipStore(str(tmp_path / "ownership.sqlite3"))
    truncated = tmp_path / "song.flac"
    truncated.touch()
    store.record("123", str(truncated), "LOSSLESS")
    assert store.ownership_of("123") is None
    truncated.write_bytes(b"audio")
    info = store.ownership_of("123")
    assert info is not None and info["owned"] is True
    store.close()


def test_ownership_zero_byte_falls_through_to_real_copy(tmp_path):
    store = OwnershipStore(str(tmp_path / "ownership.sqlite3"))
    truncated = tmp_path / "hi_res.flac"
    truncated.touch()
    real = tmp_path / "lossless.flac"
    real.write_bytes(b"audio")
    store.record("123", str(truncated), "HI_RES_LOSSLESS")
    store.record("123", str(real), "LOSSLESS")
    info = store.ownership_of("123")
    assert info is not None
    assert info["path"] == str(real)
    store.close()


# ---------------------------------------------------------------- finding 66


class _TreeHost:
    pass


def _tree_setup(tmp_path):
    install_root = tmp_path / "waves.dist"
    install_root.mkdir()
    target = install_root / "waves.bin"
    target.write_bytes(b"OLD")
    (install_root / "lib.so").write_bytes(b"OLDLIB")
    return install_root, target


def test_tree_swap_missing_executable_restores_old_install(tmp_path):
    install_root, target = _tree_setup(tmp_path)
    new_tree = tmp_path / "staged" / "waves.dist"
    new_tree.mkdir(parents=True)
    (new_tree / "lib.so").write_bytes(b"NEWLIB")  # no waves.bin in the payload
    with pytest.raises(UpdaterError):
        AppUpdater._apply_unix_tree(_TreeHost(), new_tree, target, lambda *_: None)
    assert target.read_bytes() == b"OLD", "old install was not restored"
    assert not install_root.with_name(install_root.name + ".old").exists()
    assert not install_root.with_name(install_root.name + ".new").exists()


def test_tree_swap_with_executable_still_succeeds(tmp_path):
    install_root, target = _tree_setup(tmp_path)
    new_tree = tmp_path / "staged" / "waves.dist"
    new_tree.mkdir(parents=True)
    (new_tree / "waves.bin").write_bytes(b"NEW")
    out = AppUpdater._apply_unix_tree(_TreeHost(), new_tree, target, lambda *_: None)
    assert out == target
    assert target.read_bytes() == b"NEW"
    assert not install_root.with_name(install_root.name + ".old").exists()


# ---------------------------------------------------------------- finding 67


def test_dead_recently_added_pair_removed():
    assert "recentlyAdded" not in BACKEND_SRC
    assert "loadRecentlyAdded" not in BACKEND_SRC
    assert "recentlyAdded" not in MAIN_QML
    assert "recentlyAddedLoaded" not in BRIDGE_MD


# ---------------------------------------------------------------- finding 68


def test_video_preview_passes_padding():
    assert 'format_path_media(template, vid, pad, **kw) + ".mp4"' in BACKEND_SRC


# ---------------------------------------------------------------- finding 69


def test_artcard_runs_no_unrenderable_collection_rollups():
    """Both ArtCard download controls must opt out of the collection rollup:
    one is only visible with live state (which outranks the rollup), the other
    only renders for non-collection kinds."""
    assert "collectionCheck: ac.kind ===" not in MAIN_QML


# ---------------------------------------------------------------- finding 70


def test_queuestack_step_is_gated_on_visible():
    line = next(ln for ln in MAIN_QML.splitlines() if "readonly property int step:" in ln)
    assert "visible ?" in line, "step must not depend on marchTick while hidden"
