"""Remembering where a network volume came from, and mounting it back.

The recurring failure this solves: macOS quietly ejects an idle SMB volume
(sleep, lid close, a network blip), so the saved download folder's mount
point simply stops existing. Every probe the app runs then checks a path
that cannot come back on its own; navigating to the share in Finder "fixes"
it only because Finder issues a mount request served with the keychain's
saved credentials. This module gives the app that same move:

- :func:`mount_origin` asks the OS (statfs) where a mounted volume came
  from, so the app can remember "/Volumes/Media" = "smb://user@nas/Media"
  while the share is healthy.
- :func:`remount` asks macOS to mount that URL again, through the same
  NetFS framework Finder uses, with UI suppressed: keychain credentials or
  nothing. It never prompts, never blocks the caller past its timeout.

Both are macOS-only and degrade to no-ops elsewhere. Origin strings carry a
hostname and possibly a username, so callers must register them as secrets
with diagnostics before persisting; nothing in this module logs them.
"""

from __future__ import annotations

import ctypes
import logging
import sys
import urllib.parse
from threading import Lock, Thread
from typing import ClassVar

logger = logging.getLogger("waves.netmount")

# Filesystems that live on the network: only these get an origin remembered
# (a USB drive's "/dev/disk2s1" origin is useless for remounting).
NETWORK_FSTYPES = {"smbfs", "afpfs", "webdav", "nfs", "cifs"}

_MFSTYPENAMELEN = 16
_MAXPATHLEN = 1024


class _StatFS(ctypes.Structure):
    # struct statfs, 64-bit-inode layout (the only one on modern macOS).
    _fields_: ClassVar = [
        ("f_bsize", ctypes.c_uint32),
        ("f_iosize", ctypes.c_int32),
        ("f_blocks", ctypes.c_uint64),
        ("f_bfree", ctypes.c_uint64),
        ("f_bavail", ctypes.c_uint64),
        ("f_files", ctypes.c_uint64),
        ("f_ffree", ctypes.c_uint64),
        ("f_fsid", ctypes.c_int32 * 2),
        ("f_owner", ctypes.c_uint32),
        ("f_type", ctypes.c_uint32),
        ("f_flags", ctypes.c_uint32),
        ("f_fssubtype", ctypes.c_uint32),
        ("f_fstypename", ctypes.c_char * _MFSTYPENAMELEN),
        ("f_mntonname", ctypes.c_char * _MAXPATHLEN),
        ("f_mntfromname", ctypes.c_char * _MAXPATHLEN),
        ("f_flags_ext", ctypes.c_uint32),
        ("f_reserved", ctypes.c_uint32 * 7),
    ]


def _libc():
    return ctypes.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True)


def _inode64(libc, name: str):
    """The 64-bit-inode variant of a statfs-family call. Intel macOS exports
    it under the "$INODE64" suffix (the plain name there is the legacy call
    with a smaller struct statfs, read wrongly by _StatFS); Apple Silicon has
    only the plain symbol."""
    try:
        return libc[name + "$INODE64"]
    except (KeyError, AttributeError):
        return getattr(libc, name)


# getmntinfo(3) mode: answer from the kernel's cached mount table, never ask
# a server. The one safe way to look at a mount that may be hung.
_MNT_NOWAIT = 2
# getmntinfo hands back a buffer it owns and reuses on the next call.
_MNT_LOCK = Lock()


def mount_points() -> set[str] | None:
    """The mount point of every mounted filesystem ("/", "/Volumes/Media",
    ...), read from the kernel's own table with MNT_NOWAIT, so a dead network
    mount cannot hang the call the way statfs on the mount itself can. None
    when the table cannot be read or this is not macOS. Mount points name
    shares (identity): callers never log the set."""
    if sys.platform != "darwin":
        return None
    try:
        libc = _libc()
        getmntinfo = _inode64(libc, "getmntinfo")
        getmntinfo.restype = ctypes.c_int
        getmntinfo.argtypes = [ctypes.POINTER(ctypes.POINTER(_StatFS)), ctypes.c_int]
        with _MNT_LOCK:
            table = ctypes.POINTER(_StatFS)()
            count = getmntinfo(ctypes.byref(table), _MNT_NOWAIT)
            if count <= 0:
                return None
            return {table[i].f_mntonname.decode("utf-8", "replace") for i in range(count)}
    except Exception:
        logger.debug("mount table lookup failed", exc_info=True)
        return None


def is_mounted(mount_point: str) -> bool | None:
    """True when a filesystem is mounted exactly at ``mount_point``, False
    when the directory is there with nothing mounted on it (the leftover a
    dropped share leaves under /Volumes), None when the mount table cannot be
    read. Reads the cached table only: safe on a hung mount."""
    points = mount_points()
    if points is None:
        return None
    return (mount_point.rstrip("/") or "/") in points


def mount_info(path: str) -> tuple[str, str, str]:
    """(fstype, from_name, mount_point) for any path, e.g. ("smbfs",
    "//user@nas._smb._tcp.local/Media", "/Volumes/Media"). ("", "", "") when
    statfs fails or this is not macOS.

    Unlike :func:`mount_origin` the path need not BE the mount point: statfs
    answers for the volume holding it, so a library folder several levels
    down still names the share it lives on and where that share is mounted.
    Both the from-name and the mount point are identity (host, maybe user, and
    the share name): callers register them as secrets before storing or
    logging them.
    """
    if sys.platform != "darwin":
        return ("", "", "")
    try:
        libc = _libc()
        statfs = _inode64(libc, "statfs")
        buf = _StatFS()
        if statfs(path.encode("utf-8"), ctypes.byref(buf)) != 0:
            return ("", "", "")
        return (
            buf.f_fstypename.decode("utf-8", "replace"),
            buf.f_mntfromname.decode("utf-8", "replace"),
            buf.f_mntonname.decode("utf-8", "replace"),
        )
    except Exception:
        logger.debug("statfs origin lookup failed", exc_info=True)
        return ("", "", "")


def mount_origin(mount_point: str) -> tuple[str, str]:
    """(fstype, from_name) for a mounted path, e.g. ("smbfs",
    "//user@nas._smb._tcp.local/Media"). ("", "") when the path is not a
    mount, statfs fails, or this is not macOS. The from-name is identity
    (host, maybe user): the caller registers it as a secret before storing.
    """
    fstype, from_name, _point = mount_info(mount_point)
    return (fstype, from_name)


def origin_url(fstype: str, from_name: str) -> str:
    """Turn a statfs from-name into a mountable URL, or "" when it is not a
    network origin. "//user@nas/Media" (smbfs) becomes "smb://user@nas/Media",
    with each path segment percent-encoded (share names carry spaces).
    macOS already stores smbfs from-names encoded ("//nas/My%20Music"), so
    each segment is decoded first and never encoded twice."""
    if fstype not in NETWORK_FSTYPES or not from_name.startswith("//"):
        return ""
    scheme = {"smbfs": "smb", "cifs": "smb", "afpfs": "afp", "webdav": "http", "nfs": "nfs"}[fstype]
    rest = from_name[2:]
    host, _, path = rest.partition("/")
    if not host:
        return ""
    quoted = "/".join(urllib.parse.quote(urllib.parse.unquote(seg)) for seg in path.split("/"))
    return f"{scheme}://{host}/{quoted}" if quoted else f"{scheme}://{host}"


_ENC_UTF8 = 0x08000100  # kCFStringEncodingUTF8


def _remount_sync(url: str) -> bool:
    """The blocking NetFS call itself; only ever run under :func:`remount`'s
    timeout guard. UI is suppressed: with the credentials Finder saved to the
    keychain it mounts silently, without them it fails instead of prompting."""
    cf = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
    netfs = ctypes.CDLL("/System/Library/Frameworks/NetFS.framework/NetFS")

    cf.CFURLCreateWithBytes.restype = ctypes.c_void_p
    cf.CFURLCreateWithBytes.argtypes = [
        ctypes.c_void_p,
        ctypes.c_char_p,
        ctypes.c_long,
        ctypes.c_uint32,
        ctypes.c_void_p,
    ]
    cf.CFStringCreateWithCString.restype = ctypes.c_void_p
    cf.CFStringCreateWithCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32]
    cf.CFDictionaryCreateMutable.restype = ctypes.c_void_p
    cf.CFDictionaryCreateMutable.argtypes = [ctypes.c_void_p, ctypes.c_long, ctypes.c_void_p, ctypes.c_void_p]
    cf.CFDictionarySetValue.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
    cf.CFRelease.argtypes = [ctypes.c_void_p]
    netfs.NetFSMountURLSync.restype = ctypes.c_int32
    netfs.NetFSMountURLSync.argtypes = [ctypes.c_void_p] * 6 + [ctypes.c_void_p]

    raw = url.encode("utf-8")
    cfurl = cf.CFURLCreateWithBytes(None, raw, len(raw), _ENC_UTF8, None)
    if not cfurl:
        return False
    open_options = None
    ui_key = None
    no_ui = None
    try:
        key_cb = ctypes.c_void_p.in_dll(cf, "kCFTypeDictionaryKeyCallBacks")
        val_cb = ctypes.c_void_p.in_dll(cf, "kCFTypeDictionaryValueCallBacks")
        open_options = cf.CFDictionaryCreateMutable(None, 0, ctypes.byref(key_cb), ctypes.byref(val_cb))
        # kNAUIOptionKey / kNAUIOptionNoUI are CFSTR macros in NetFS.h, not
        # exported symbols: build the same strings by value.
        ui_key = cf.CFStringCreateWithCString(None, b"UIOption", _ENC_UTF8)
        no_ui = cf.CFStringCreateWithCString(None, b"NoUI", _ENC_UTF8)
        cf.CFDictionarySetValue(open_options, ui_key, no_ui)
        mounts = ctypes.c_void_p(None)
        rc = netfs.NetFSMountURLSync(cfurl, None, None, None, open_options, None, ctypes.byref(mounts))
        if mounts.value:
            cf.CFRelease(mounts)
        return rc == 0
    finally:
        for ref in (no_ui, ui_key, open_options):
            if ref:
                cf.CFRelease(ref)
        cf.CFRelease(cfurl)


def remount(url: str, timeout_s: float = 20.0) -> bool:
    """Ask macOS to mount ``url`` (no UI, keychain credentials). True when
    the mount call reports success within the timeout; False on failure,
    timeout, or off macOS. Runs the blocking call on a daemon thread so a
    wedged mount daemon costs the caller at most ``timeout_s``."""
    if sys.platform != "darwin" or not url:
        return False
    result: list[bool] = []

    def work() -> None:
        try:
            result.append(_remount_sync(url))
        except Exception:
            logger.debug("NetFS remount call failed", exc_info=True)
            result.append(False)

    t = Thread(target=work, daemon=True)
    t.start()
    t.join(timeout_s)
    if not result:
        logger.info("Share remount attempt timed out")
        return False
    logger.info("Share remount attempt %s", "succeeded" if result[0] else "was declined")
    return result[0]
