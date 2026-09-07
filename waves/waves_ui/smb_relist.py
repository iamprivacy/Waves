"""Reading the folders an SMB mount will not list.

The macOS SMB client fills a per-directory cache with several parallel
queries (``dir_cache_async_cnt``, default 10). On at least one Mac and one
server that fill goes wrong: all ten queries come back with the FIRST page,
all ten answers are cached, and a folder holding 1345 subfolders then lists
as 10000 entries carrying 1000 distinct names. The operating system reports
success, so every application on the machine sees the short listing, Finder
included. Opening any hidden folder by its exact name works perfectly at
about 5 ms, so only enumeration is affected, which is why nothing else the
user runs appears to notice.

A second mount of the same share, created fresh, lists all 1345 in about a
second. That is the whole move here: when a scan has already flagged a
listing as untrusted, mount the same share again at a private mount point,
read the names the flagged folder really holds, unmount, and hand those names
to the by-name indexer the cache already has (LibraryIndex.probe_folders).
Nothing is copied, because a mount is a view: only names cross the wire.

Deliberate properties, in the order they matter:

- **It only ever runs after the scan has already failed.** A healthy library
  never reaches this module, so it costs those users nothing.
- **Read only and invisible.** The private mount is ``ro`` (nothing can be
  written or deleted through it, even by a bug) and ``nobrowse`` (never on the
  desktop, in Finder's sidebar, or in a file dialog). It is ``soft`` so a
  sleeping NAS fails the call instead of wedging the thread.
- **It never prompts.** ``-N`` means the mount uses the credentials the user
  already saved when they connected the share, or fails. An app that popped a
  password sheet during a background scan would be worse than the bug.
- **It verifies before it is believed.** A fresh listing is refused unless it
  repeats no name (the same test that flagged the original) and contains every
  name the index already holds for that folder. A second broken mount, or a
  mount that landed on the wrong share, is therefore discarded rather than
  acted on.
- **Every failure is the status quo.** Every step returns empty on trouble,
  and an empty answer means the app behaves exactly as it does today.

macOS only, and only for ``smbfs``. AFP, NFS and WebDAV mounts do not have
this defect, and Windows and Linux SMB clients page directory listings without
this cache stage. Everything here returns empty off macOS.

Nothing in this module logs a host, a share, a mount point or a folder name:
those are identity, and the counts are what a diagnostic needs anyway.
"""

from __future__ import annotations

import errno
import logging
import os
import subprocess
import sys
import unicodedata
from collections.abc import Callable, Iterable

from . import diagnostics, netmount

logger = logging.getLogger("waves.smbrelist")

# The filesystems this workaround applies to. Kept narrow on purpose: the
# defect is in the SMB client's directory cache, and mounting an AFP or NFS
# share a second time would be motion without a reason.
RELIST_FSTYPES = frozenset({"smbfs", "cifs"})

# Where private mount points are made, under the app's own config directory.
MOUNTS_DIR_NAME = "relist-mounts"

# ro: nothing can be written or deleted through this view.
# nobrowse: never visible in Finder, on the desktop, or in a file dialog.
# soft: a sleeping server fails the call instead of hanging it forever.
# No cache options: a plain fresh mount was measured to list correctly, and
# nomdatacache/nodatacache made no difference, so the least invasive set wins.
MOUNT_OPTIONS = "ro,nobrowse,soft"

_MOUNT_BIN = "/sbin/mount_smbfs"
_UMOUNT_BIN = "/sbin/umount"

# A mount that has not answered in this long is not going to. The measured
# listing took 1.0 s cold; the margin is for a NAS spinning its disks up.
MOUNT_TIMEOUT_S = 30.0
UNMOUNT_TIMEOUT_S = 15.0


def _on_macos() -> bool:
    """The platform gate, as a function so a guard can drive the whole sequence
    on any machine. Everything public here checks it."""
    return sys.platform == "darwin"


def _key(name: str) -> str:
    """The comparison form for a folder name. SMB servers and macOS disagree
    about unicode composition, and most shares are case insensitive, so a
    superset check on raw bytes would refuse a listing that is in fact fine."""
    return unicodedata.normalize("NFC", name).casefold()


def mounts_dir(config_dir: str) -> str:
    """Where this module makes its private mount points."""
    return os.path.join(config_dir, MOUNTS_DIR_NAME)


def share_url(from_name: str) -> str:
    """The ``//user@host/share`` mount_smbfs accepts, from a statfs from-name,
    or "" when the from-name does not name a share.

    A from-name is already in mount_smbfs's syntax, so this mostly validates:
    it insists on a host and a share, keeps any user (which is how the right
    saved credential is found when one server holds several accounts), drops
    anything after the share (a from-name for a submount), and drops a
    password outright. A password should never appear in a from-name, and if
    one ever did, putting it on a command line would publish it to every
    process table on the machine."""
    if not from_name.startswith("//"):
        return ""
    host_part, _, path = from_name[2:].partition("/")
    share = path.strip("/").split("/")[0] if path else ""
    if not host_part or not share:
        return ""
    if "@" in host_part:
        creds, _, host = host_part.rpartition("@")
        # "domain;user:password" keeps the domain and the user, loses the rest.
        creds = creds.split(":", 1)[0]
        if not host:
            return ""
        host_part = f"{creds}@{host}" if creds else host
    return f"//{host_part}/{share}"


def subpath_within(path: str, mount_point: str) -> str | None:
    """``path`` expressed relative to its mount point ("" when it IS the mount
    point), or None when it does not sit under that mount point at all."""
    point = (mount_point or "").rstrip(os.sep)
    target = (path or "").rstrip(os.sep)
    if not point or not target:
        return None
    if target == point:
        return ""
    prefix = point + os.sep
    if not target.startswith(prefix):
        return None
    return target[len(prefix) :]


def plan_for(path: str) -> tuple[str, str]:
    """(share_url, subpath) for a folder living on an SMB mount, or ("", "").

    Empty for every path this workaround does not apply to: off macOS, on a
    local disk, on a network filesystem that is not SMB, or on a mount whose
    origin statfs will not name."""
    if not _on_macos():
        return ("", "")
    fstype, from_name, point = netmount.mount_info(path)
    if fstype not in RELIST_FSTYPES:
        return ("", "")
    url = share_url(from_name)
    sub = subpath_within(path, point)
    if not url or sub is None:
        return ("", "")
    # The URL names a host and often a username, and it is about to be handed
    # to a subprocess whose argv an OSError or a timeout would carry into the
    # exception text. Registered here, at the one place it is derived, so a
    # stray traceback anywhere downstream is scrubbed rather than trusted.
    diagnostics.register_secret(url, "‹share-origin›")
    return (url, sub)


def _run(argv: list[str], timeout: float) -> bool:
    """A fixed-argv helper run to completion, with its output swallowed: it
    names the host and the share, which are identity and never logged."""
    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        # The class name only, never exc_info: a TimeoutExpired renders its
        # whole argv (so the share URL) and an OSError its path, and DEBUG
        # reaches disk in verbose mode, which is exactly the log a user
        # attaches to a public issue.
        logger.debug("private mount helper did not run (%s)", type(exc).__name__)
        return False
    return proc.returncode == 0


def mount_share(url: str, point: str) -> bool:
    """Mount ``url`` read only and invisibly at ``point``. False on any
    failure, including the one that matters most: no saved credential, which
    ``-N`` turns into a refusal instead of a password prompt nobody is there
    to answer."""
    return _run([_MOUNT_BIN, "-N", "-o", MOUNT_OPTIONS, url, point], MOUNT_TIMEOUT_S)


def unmount_share(point: str) -> bool:
    """Unmount ``point``, forcing it if the polite unmount is refused. A
    private mount holds nothing the user can be in the middle of, so forcing
    costs nothing and leaving it mounted would leak a connection per scan."""
    if _run([_UMOUNT_BIN, point], UNMOUNT_TIMEOUT_S):
        return True
    return _run([_UMOUNT_BIN, "-f", point], UNMOUNT_TIMEOUT_S)


def trusted_names(entries: Iterable[str]) -> list[str] | None:
    """The distinct names among ``entries`` sorted, or None when one repeats.

    A repeat is the same proof the scan uses (library_index._scandir_one): a
    healthy filesystem never hands over one entry twice, so a listing that does
    is a broken enumeration. Applying it to the FRESH listing too is what stops
    a second broken mount from being believed just because it is new. Dot
    entries are skipped before counting, exactly as the scan skips them."""
    raw = 0
    names: dict[str, str] = {}
    for name in entries:
        if not name or name.startswith("."):
            continue
        raw += 1
        names.setdefault(_key(name), name)
    if raw != len(names):
        logger.info(
            "a listing repeats itself (%d handed over, %d distinct): not trusted",
            raw,
            len(names),
        )
        return None
    return sorted(names.values())


def _read_dir_names(path: str) -> list[str] | None:
    """The subfolder names ``path`` lists, or None when it cannot be read or
    the listing repeats itself (see :func:`trusted_names`)."""
    found: list[str] = []
    try:
        with os.scandir(path) as it:
            for entry in it:
                try:
                    if not entry.is_dir(follow_symlinks=False):
                        continue
                except OSError:
                    continue
                found.append(entry.name)
    except OSError as exc:
        logger.debug("private mount listing failed (%s)", type(exc).__name__)
        return None
    return trusted_names(found)


def _accept(point: str, sub: str, known: Iterable[str]) -> list[str] | None:
    """The folder names a freshly mounted share offers for one flagged folder,
    or None when the listing is not good enough to act on.

    Two ways to fail, and each one has cost a user something before: a listing
    that repeats itself is a second broken mount, and a listing missing a
    folder the index already holds would, if believed, retire music the app has
    already read. Counts are logged, never a name or a path."""
    names = _read_dir_names(os.path.join(point, sub) if sub else point)
    if names is None:
        return None
    have = {_key(n) for n in known}
    lost = have - {_key(n) for n in names}
    if lost:
        logger.info(
            "a private mount listed %d folders but lost %d the index holds: not trusted",
            len(names),
            len(lost),
        )
        return None
    logger.info(
        "a private mount listed %d folders where the share's own mount showed %d",
        len(names),
        len(have),
    )
    return names


def _make_point(base: str) -> str:
    """An empty directory to mount on, or "" when one cannot be made. The name
    is the process id, so two Waves instances scanning at once never fight over
    one mount point, and a leftover is recognisably ours to sweep."""
    try:
        os.makedirs(base, exist_ok=True)
        point = os.path.join(base, f"pid-{os.getpid()}")
        os.makedirs(point, exist_ok=True)
    except OSError as exc:
        logger.debug("could not make a private mount point (%s)", type(exc).__name__)
        return ""
    diagnostics.register_secret(point, "‹mount-point›")
    return point


def _drop_point(point: str) -> None:
    """Remove a mount point directory once it is unmounted. rmdir ONLY: it
    refuses a directory that still has anything in it, so a mount that somehow
    survived the unmount is left alone rather than having the share's contents
    walked. Nothing here can ever delete a file (see the app's standing rule
    that Waves does not delete user files)."""
    try:
        os.rmdir(point)
    except OSError as exc:
        if exc.errno not in (errno.ENOENT, errno.ENOTEMPTY, errno.EBUSY):
            logger.debug("could not remove a private mount point (%s)", type(exc).__name__)


def sweep_stale(config_dir: str, *, unmount: Callable[[str], bool] | None = None) -> int:
    """Unmount and remove mount points a previous run left behind (a crash, a
    force quit, a power cut). Returns how many were cleaned up. Safe to call at
    every startup: with nothing left behind it does nothing."""
    if not _on_macos():
        return 0
    unmount = unmount or unmount_share
    base = mounts_dir(config_dir)
    try:
        leftovers = sorted(os.listdir(base))
    except OSError:
        return 0
    cleaned = 0
    for name in leftovers:
        point = os.path.join(base, name)
        if not os.path.isdir(point):
            continue
        unmount(point)
        _drop_point(point)
        if not os.path.exists(point):
            cleaned += 1
    if cleaned:
        logger.info("cleaned up %d leftover private mount point(s)", cleaned)
    _drop_point(base)
    return cleaned


def relist_folders(
    targets: Iterable[str],
    *,
    config_dir: str,
    known: dict[str, Iterable[str]] | None = None,
    mount: Callable[[str, str], bool] | None = None,
    unmount: Callable[[str], bool] | None = None,
) -> dict[str, list[str]]:
    """{folder path: the subfolder names it really holds} for those ``targets``
    a fresh mount could list better than the mount they are on.

    A target missing from the answer means "no better listing", which every
    caller must read as "carry on exactly as before". That covers all of: not
    macOS, not an SMB mount, no saved credential, the server asleep, a listing
    that repeated itself again, and a listing that lost names the index
    already holds.

    ``known`` is what the index already has under each target. A fresh listing
    must contain all of it to be believed. The measured good listing was a
    strict superset of both the broken listing and the index, and requiring
    that every time is what makes a differently-behaving server safe: the
    worst it can do is be ignored.

    One mount per share, not one per target: mounting is the expensive step,
    and several flagged folders on one share are all readable through a single
    view of it. ``mount`` and ``unmount`` are injectable so the guard can drive
    the whole sequence without a server; they are resolved at CALL time, so
    replacing the module's own pair works too."""
    if not _on_macos():
        return {}
    mount = mount or mount_share
    unmount = unmount or unmount_share
    known = known or {}

    # Group by share, keeping each target's own subpath: two folders on one
    # share cost one mount, and the order is stable so a log reads the same
    # way twice.
    groups: dict[str, list[tuple[str, str]]] = {}
    for target in targets:
        url, sub = plan_for(target)
        if not url:
            continue
        groups.setdefault(url, []).append((target, sub))
    if not groups:
        return {}

    base = mounts_dir(config_dir)
    out: dict[str, list[str]] = {}
    for url, members in groups.items():
        point = _make_point(base)
        if not point:
            continue
        try:
            if not mount(url, point):
                logger.info("a private mount of the flagged share was refused")
                continue
            for target, sub in members:
                names = _accept(point, sub, known.get(target, ()))
                if names is not None:
                    out[target] = names
        finally:
            unmount(point)
            _drop_point(point)
    _drop_point(base)
    return out
