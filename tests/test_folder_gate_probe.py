"""Download-folder reachability gate (_probe_folder_verdict): a set folder is
write-probed before a download starts, because a stale network mount can pass
exists()/is_dir() while every real write fails, and the macOS remount pattern
(share comes back as "/Volumes/Name 1") is healed automatically.

Pure staticmethod, so no Qt or bridge construction is needed.
"""

from __future__ import annotations

from tidaler.waves_ui.backend import WavesBridge

probe = WavesBridge._probe_folder_verdict


def test_writable_folder_is_ok(tmp_path):
    verdict, path = probe(str(tmp_path), volumes_root=str(tmp_path / "Volumes"))
    assert verdict == "ok"
    assert path == str(tmp_path)
    assert not list(tmp_path.iterdir()), "the write probe must clean up after itself"


def test_creatable_folder_is_ok(tmp_path):
    target = tmp_path / "not" / "yet" / "there"
    verdict, _ = probe(str(target), volumes_root=str(tmp_path / "Volumes"))
    assert verdict == "ok"
    assert target.is_dir(), "the probe creates missing folders, like the download would"


def _dead_path(tmp_path):
    """A path that fails makedirs/writes: a child of a regular FILE."""
    blocker = tmp_path / "blocker"
    blocker.write_bytes(b"x")
    return blocker / "Music"


def test_unwritable_folder_is_dead(tmp_path):
    verdict, _ = probe(str(_dead_path(tmp_path)), volumes_root=str(tmp_path / "Volumes"))
    assert verdict == "dead"


def test_macos_remount_is_healed(tmp_path):
    # Stored path points at a dead mount /Volumes/Music/Library, but the share
    # remounted as "/Volumes/Music 1" with the same folder inside.
    volumes = tmp_path / "Volumes"
    (volumes / "Music 1" / "Library").mkdir(parents=True)
    dead = volumes / "Music"
    dead.write_bytes(b"")  # a stale mount point: exists, but not a real dir
    verdict, live = probe(str(dead / "Library"), volumes_root=str(volumes))
    assert verdict == "healed"
    assert live == str(volumes / "Music 1" / "Library")


def test_heal_works_in_both_suffix_directions(tmp_path):
    # Stored path carries the suffix ("Music-1", now a stale marker) and the
    # clean name is the live mount.
    volumes = tmp_path / "Volumes"
    (volumes / "Music" / "Library").mkdir(parents=True)
    (volumes / "Music-1").write_bytes(b"")  # stale mount point
    verdict, live = probe(str(volumes / "Music-1" / "Library"), volumes_root=str(volumes))
    assert verdict == "healed"
    assert live == str(volumes / "Music" / "Library")


def test_paths_outside_the_volumes_root_are_never_healed(tmp_path):
    volumes = tmp_path / "Volumes"
    volumes.mkdir()
    verdict, _ = probe(str(_dead_path(tmp_path)), volumes_root=str(volumes))
    assert verdict == "dead"


def test_unrelated_volume_names_are_not_healed(tmp_path):
    volumes = tmp_path / "Volumes"
    (volumes / "Backup").mkdir(parents=True)
    dead = volumes / "Music"
    dead.write_bytes(b"")  # stale mount point, and "Backup" is not a rename of it
    verdict, _ = probe(str(dead / "Library"), volumes_root=str(volumes))
    assert verdict == "dead"
