"""The local-vs-network classification behind the library file-watcher.

The QFileSystemWatcher accelerator is attached ONLY on a confidently-local disk,
decided by filesystem TYPE (via QStorageInfo at runtime), because the watcher's
own addPaths() reports success for an SMB path on macOS and a kernel NFS/CIFS
path on Linux while delivering no events. This pins the pure classifier so a
network or unknown type is never mistaken for local (which would silently rely on
a dead watcher instead of the universal poll).
"""

from __future__ import annotations

import pytest

from waves.waves_ui.bridge_library import _is_local_fstype, _is_network_fstype, _is_remote_windows_device

LOCAL = [
    "apfs",
    "APFS",
    "hfs",
    "hfsplus",
    "ntfs",
    "NTFS",
    "refs",
    "ext2",
    "ext3",
    "ext4",
    "xfs",
    "btrfs",
    "zfs",
    "exfat",
    "vfat",
    "fat32",
    "msdos",
]
NETWORK_OR_UNKNOWN = [
    "smbfs",
    "cifs",
    "nfs",
    "nfs4",
    "afpfs",
    "webdav",
    "sshfs",
    "fuseblk",
    "ftp",
    "",
    "   ",
    "davfs",
    "9p",
]


@pytest.mark.parametrize("fstype", LOCAL)
def test_local_filesystems_are_local(fstype):
    assert _is_local_fstype(fstype) is True


@pytest.mark.parametrize("fstype", NETWORK_OR_UNKNOWN)
def test_network_and_unknown_filesystems_are_not_local(fstype):
    assert _is_local_fstype(fstype) is False


# A Windows MAPPED drive (Z: -> \\nas\music) reports the REMOTE volume's format
# (often "NTFS") as its fileSystemType, so the type check alone would class it
# local. Its QStorageInfo device string is the UNC share, which unmasks it; a
# real local volume's device is \\?\Volume{guid} or a /dev node.
REMOTE_DEVICES = [
    r"\\nas\music",
    r"\\SERVER01\share",
    r"\\10.0.0.5\media",
]
LOCAL_DEVICES = [
    r"\\?\Volume{5e9f0d6a-0000-0000-0000-100000000000}\\",
    "/dev/disk3s1s1",
    "/dev/sda2",
    "",
    "   ",
]


# The other side of the same question, and a separate list on purpose. The scan
# reads THIS one to decide whether to throttle its pools, and "not in the local
# list" is not the same claim as "on the network": a type in neither (a FUSE
# mount, a container overlay, a filesystem newer than the tables) must stay
# unknown, or a local disk this release has never heard of gets scanned at a
# quarter speed for no reason.
NETWORK = ["smbfs", "SMBFS", "cifs", "nfs", "nfs4", "afpfs", "webdav", "davfs", "sshfs", "ftp", "9p"]
NEITHER = ["fuseblk", "overlay", "f2fs", "bcachefs", "tmpfs", "", "   "]


@pytest.mark.parametrize("fstype", NETWORK)
def test_network_filesystems_are_named_as_such(fstype):
    assert _is_network_fstype(fstype) is True
    assert _is_local_fstype(fstype) is False


@pytest.mark.parametrize("fstype", NEITHER)
def test_an_unknown_filesystem_claims_neither(fstype):
    assert _is_network_fstype(fstype) is False
    assert _is_local_fstype(fstype) is False


@pytest.mark.parametrize("device", REMOTE_DEVICES)
def test_unc_backed_mapped_drives_are_remote(device):
    assert _is_remote_windows_device(device) is True


@pytest.mark.parametrize("device", LOCAL_DEVICES)
def test_local_volume_devices_are_not_remote(device):
    assert _is_remote_windows_device(device) is False
