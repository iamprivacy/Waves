"""In-app self-updater for Waves.

Waves ships as a packaged single-file binary built by CI and published to a
public GitHub repo's Releases. This module lets the app notice a newer release,
download the build for the current OS/arch, verify it against its ``.sha256``
sidecar, and swap it in atomically, the same shape as the FFmpeg manager
(:mod:`waves.waves_ui.ffmpeg_manager`), which this deliberately mirrors.

Nothing here touches Qt, so it is pure and unit-testable; the Qt slots/signals
that drive the Settings UI live in :mod:`waves.waves_ui.backend`.

Design rules baked in here:

* **Opt-in, no telemetry.** This module only ever issues a plain ``GET`` to the
  GitHub Releases API and to a release asset URL. It sends no user data. The
  *automatic* check is gated by a user preference in the backend; nothing here
  runs on a schedule by itself.
* **No-op until configured.** When :data:`REPO` is blank, every entry point
  degrades to a safe no-op (``status()`` reports ``not_configured`` and no
  network call is made). It is now set to the public Waves repo, so a packaged
  build can find its releases; the automatic check still stays gated behind the
  user preference (see above).
* **Check anywhere, install only when frozen.** The version check works from a
  source checkout too (so a dev is told a newer release exists), but a real
  self-install only runs from a packaged/frozen build; from source the UI sends
  the user to the Releases page instead.
* **Defer to a managing package manager.** An install owned by Homebrew, Scoop,
  Snap, Flatpak or an AppImage (see :func:`managed_channel`) still gets update
  *notices*, but the self-installer stands down so the two update paths never
  fight over the same files; the UI points at the manager's upgrade command.
  External updater tools that replace the app bundle themselves keep working
  either way, nothing here locks the install.
"""

from __future__ import annotations

import errno
import json
import logging
import os
import platform
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path
from threading import Event, Thread

import requests

# Reuse the genuinely-identical, stable helpers from the FFmpeg manager rather
# than duplicating them: the sha256 file digest is byte-for-byte the same job.
from .ffmpeg_manager import _sha256_file
from .signing import UPDATE_PUBLIC_KEY, parse_sha256sums
from .signing import verify as verify_signature

logger = logging.getLogger("waves")

_TIMEOUT = 30
_CHUNK = 1 << 16  # 64 KiB streaming chunks
_UA = "Waves-updater"

# The public GitHub repo that hosts Waves releases, as ``"owner/name"``. When
# blank the updater is dormant (no network, ``status() -> "not_configured"``);
# it is set here at release time to the public Waves repo. The value ships in
# the binary and is inherently public once released, so this is not a secret.
REPO = "iamprivacy/Waves"

# Asset-name matching. CI names release assets per platform; rather than pin
# exact names (which don't exist yet), match on OS/arch tokens so the updater
# keeps working however the assets end up named.
_OS_TOKENS = {
    "macos": ("macos", "darwin", "osx", "apple", "mac"),
    "linux": ("linux",),
    "windows": ("windows", "win"),
}
_ARCH_TOKENS = {
    "arm64": ("arm64", "aarch64", "arm"),
    "amd64": ("amd64", "x86_64", "x86-64", "x64", "intel"),
}
# Real installable payloads (not checksums / metadata sidecars). Only formats
# the updater can actually unpack+install belong here: a mismatch between what
# _select_asset ranks as installable and what _extract_payload can open would
# copy an archive/disk-image raw and try to execute it (a bricked install). So
# .dmg/.7z are deliberately excluded until their handling exists, see
# _extract_payload. .tar.gz/.tgz are archives we unpack; .exe/.appimage are raw
# single-file binaries used as-is.
_INSTALL_EXTS = (".zip", ".exe", ".appimage", ".tar.gz", ".tgz")
_SIDECAR_EXTS = (".sha256", ".sha256sum", ".blockmap", ".yml", ".yaml", ".txt", ".sig", ".asc")

# macOS ships two flavors since the PySide6 wheel-tag lie (issue #14): the
# regular bundle requires macOS 15, and a "_legacy" bundle (pyside6 6.9.3)
# covers 12 through 14. Anything at or above this version installs the regular
# flavor; below it, only assets carrying the "legacy" token are eligible.
_MACOS_LEGACY_BELOW = 15


def _macos_wants_legacy() -> bool:
    """True when this Mac must run the legacy flavor (macOS below 15).

    Fail-safe direction: an unparseable ``mac_ver`` (or the compat "10.16"
    some builds report) selects legacy, which runs on every supported macOS;
    the reverse mistake would hand a Monterey machine a bundle dyld kills.
    """
    if platform.system() != "Darwin":
        return False
    try:
        major = int(platform.mac_ver()[0].split(".")[0])
    except (ValueError, IndexError):
        return True
    return major < _MACOS_LEGACY_BELOW


class UpdaterError(Exception):
    """A self-update could not be completed."""


class UpdateCancelled(Exception):
    """Raised when an install is aborted via its :class:`~threading.Event`."""


@dataclass(frozen=True)
class Release:
    """A resolved release + the asset to install for the current platform."""

    version: str  # release tag, e.g. "v1.2.0"
    asset: str  # asset filename ("" if no build matched this platform)
    url: str  # asset download URL ("" if none matched)
    sha256_url: str | None = None
    sha256sums_url: str | None = None  # the signed SHA256SUMS manifest (all assets)
    sig_url: str | None = None  # SHA256SUMS.sig, Ed25519 signature over the manifest
    notes_url: str | None = None  # the release's html_url (release page)
    notes: str = ""  # release body / changelog


# --------------------------------------------------------------------------- #
# Pure helpers (platform, versions, asset selection)
# --------------------------------------------------------------------------- #
def _os_arch() -> tuple[str, str]:
    """Return ``(os_key, arch_key)`` for the running machine, or ``("", "")``.

    ``os_key`` ∈ {macos, linux, windows}; ``arch_key`` ∈ {amd64, arm64}. Never
    raises, an unknown platform simply can't self-update.
    """
    system = platform.system()
    machine = platform.machine().lower()
    arch = "arm64" if ("arm" in machine or "aarch64" in machine) else "amd64"
    if system == "Darwin":
        return "macos", arch
    if system == "Linux":
        return "linux", arch
    if system == "Windows":
        return "windows", arch
    return "", ""


def is_frozen() -> bool:
    """True when running as a packaged/compiled build (PyInstaller or Nuitka).

    Deliberately NOT ``waves.is_dev_env()``: an editable/pip install is
    importlib-discoverable, so that helper would report a from-source run as
    non-dev. Only a genuine frozen build may self-install.
    """
    return bool(getattr(sys, "frozen", False)) or "__compiled__" in globals()


# Package managers that own the install and must not be fought with: the
# self-updater swapping files under Snap/Flatpak/AppImage hits read-only
# roots, and under Homebrew/Scoop it desyncs the manager's version records
# (the next `brew upgrade` would clobber a newer self-installed build).
_CHANNEL_LABELS = {
    "homebrew-cask": "Homebrew",
    "homebrew": "Homebrew",
    "scoop": "Scoop",
    "snap": "Snap",
    "flatpak": "Flatpak",
    "appimage": "AppImage",
    "winget": "winget",
    "aur": "the AUR",
}
_CHANNEL_HINTS = {
    "homebrew-cask": "brew upgrade --cask waves",
    "homebrew": "brew upgrade --cask waves",
    "scoop": "scoop update waves",
    "winget": "winget upgrade waves",
}


def channel_label(channel: str) -> str:
    """Human name for an install channel ("Homebrew"), the raw id if unknown."""
    return _CHANNEL_LABELS.get(channel, channel)


def channel_hint(channel: str) -> str:
    """The upgrade command for a channel, "" when there is no one-liner."""
    return _CHANNEL_HINTS.get(channel, "")


def _find_brew() -> str:
    """The brew binary's path, "" when Homebrew isn't installed.

    A GUI app's PATH usually misses Homebrew's bin dir (login-shell only), so
    the two standard install prefixes are probed directly before which().
    """
    for cand in ("/opt/homebrew/bin/brew", "/usr/local/bin/brew", "/home/linuxbrew/.linuxbrew/bin/brew"):
        if os.access(cand, os.X_OK):
            return cand
    return shutil.which("brew") or ""


def managed_upgrade_command(channel: str) -> list[str] | None:
    """The argv that upgrades this install through its package manager, or
    ``None`` when the channel has no runnable one-liner (containerized formats,
    unknown channels, or the manager's binary is missing).

    The cask is named fully qualified (tap/name) so a same-named formula in
    another tap can never be upgraded by mistake.
    """
    if channel in ("homebrew-cask", "homebrew"):
        brew = _find_brew()
        if brew:
            return [brew, "upgrade", "--cask", "iamprivacy/waves/waves"]
    return None


def managed_channel() -> str:
    """Which external package manager owns this install; "" when none does.

    Containerized formats announce themselves through the environment, Scoop
    is recognisable from the install path, and everything path-undetectable
    (chiefly Homebrew Cask, which moves the .app to /Applications) is covered
    by an ``install_channel`` sentinel file the installer drops in the config
    folder (content = channel id, e.g. ``homebrew-cask``). The sentinel lives
    in config rather than inside the bundle because writing into a signed
    .app would break its code signature.

    Best-effort by design: any probe failure reads as "not managed", which
    just means the self-updater stays available, today's behavior.
    """
    if os.environ.get("SNAP"):
        return "snap"
    if os.environ.get("FLATPAK_ID") or os.path.exists("/.flatpak-info"):
        return "flatpak"
    # An AppImage is deliberately NOT a managed channel: nothing owns the file,
    # and the self-updater can replace it in place (it targets $APPIMAGE, the
    # real .AppImage path, since the running executable lives on a read-only
    # squashfs mount). See _apply.
    try:
        if "/scoop/apps/" in str(_current_exe()).replace("\\", "/").lower():
            return "scoop"
    except OSError:
        logger.debug("updater: scoop probe failed", exc_info=True)
    # System packages (AUR, deb, rpm) can't write user config at install time,
    # so their sentinel sits next to the binary; user-scope installers
    # (Homebrew Cask) drop theirs in the config folder instead.
    try:
        token = _read_channel_sentinel(_current_exe().parent / "install_channel")
        if token:
            return token
    except OSError:
        logger.debug("updater: app-dir install_channel probe failed", exc_info=True)
    try:
        from waves.helper.path import path_config_base

        token = _read_channel_sentinel(Path(path_config_base()) / "install_channel")
        if token:
            return token
    except OSError:
        logger.debug("updater: config-dir install_channel probe failed", exc_info=True)
    return ""


def _read_channel_sentinel(sentinel: Path) -> str:
    """The sanitized channel id from an ``install_channel`` file, "" if absent.

    The file is plain text anyone could edit, so the value is reduced to one
    lowercase token, bounded, before it can reach a UI string.
    """
    if not sentinel.is_file():
        return ""
    first = sentinel.read_text(encoding="utf-8", errors="replace").strip().lower().split()
    return re.sub(r"[^a-z0-9_-]", "", first[0])[:32] if first else ""


def _current_exe() -> Path:
    """Path of the running app binary.

    Nuitka 2.x standalone points ``sys.executable`` at a phantom ``python.exe``
    next to the binary (it emulates a venv layout for child interpreters); the
    real launcher path is ``sys.argv[0]``. Prefer argv[0] when it names a real
    file, falling back to ``sys.executable``. Confirmed in the field: the
    Windows helper relaunched ``C:\\Waves\\python.exe``, which does not exist.
    """
    try:
        cand = Path(sys.argv[0]).resolve()
        if cand.is_file():
            return cand
    except Exception:
        logger.debug("argv[0] did not resolve to a file", exc_info=True)
    return Path(sys.executable).resolve()


def _parse_version(tag: str) -> tuple[int, ...]:
    """Parse a ``vX.Y.Z`` / ``X.Y.Z`` tag into a comparable int tuple.

    Leading ``v`` and any pre-release/build suffix are ignored; missing parts
    read as 0 so ``1.2`` and ``1.2.0`` compare equal. Unparseable → ``()``.
    """
    m = re.search(r"\d+(?:\.\d+)*", tag or "")
    if not m:
        return ()
    return tuple(int(p) for p in m.group(0).split("."))


def _is_newer(latest: str, current: str) -> bool:
    """True if release tag ``latest`` is strictly newer than ``current``."""
    lt, ct = _parse_version(latest), _parse_version(current)
    if not lt:
        return False
    # Pad to equal length for a lexicographic tuple compare (1.2 == 1.2.0).
    width = max(len(lt), len(ct))
    return lt + (0,) * (width - len(lt)) > ct + (0,) * (width - len(ct))


def _is_older(candidate: str, current: str) -> bool:
    """True if ``candidate`` is strictly older than ``current`` (1.2 == 1.2.0)."""
    cv, ct = _parse_version(candidate), _parse_version(current)
    if not cv:
        return False
    width = max(len(cv), len(ct))
    return cv + (0,) * (width - len(cv)) < ct + (0,) * (width - len(ct))


def _manifest_version(manifest_text: str) -> str:
    """Read the ``# waves-version: vX.Y.Z`` line CI writes into SHA256SUMS.

    Because this line lives inside the signature-verified manifest, the version is
    authenticated, unlike the release tag in the (unsigned) GitHub API response,
    so it can anchor the downgrade check in :meth:`AppUpdater.install`.
    """
    m = re.search(r"^#\s*waves-version:\s*(\S+)", manifest_text, re.MULTILINE)
    return m.group(1) if m else ""


def _running_appimage() -> str:
    """The real .AppImage file's path when running from one, else "".

    The AppImage runtime mounts the payload on a read-only squashfs and sets
    ``$APPIMAGE`` to the actual file; the running executable's own path points
    into the mount and must never be an update target.

    The runtime exports both ``$APPIMAGE`` and ``$APPDIR`` to every descendant
    process, so a non-AppImage Waves launched from an AppImage-parented shell
    inherits them. Trusting the variable alone would make the updater
    os.replace the Waves payload over that unrelated application's .AppImage
    file. Only claim the AppImage path when this process actually runs out of
    the advertised mount.
    """
    appimage = os.environ.get("APPIMAGE", "")
    appdir = os.environ.get("APPDIR", "")
    if not appimage or not appdir:
        return ""
    try:
        # Both the literal and the symlink-resolved executable path count as
        # inside the mount; runtimes differ in which one they expose.
        exe = Path(sys.executable)
        mount = Path(appdir)
        inside = exe.absolute().is_relative_to(mount.absolute()) or exe.resolve().is_relative_to(mount.resolve())
        if not inside:
            return ""
    except OSError:
        return ""
    return appimage


def _select_asset(
    assets: list[dict],
    os_key: str,
    arch: str,
    prefer_appimage: bool = False,
    want_legacy: bool | None = None,
) -> tuple[str, str, str | None]:
    """Pick the best release asset for ``os_key``/``arch``.

    Returns ``(name, download_url, sha256_url)``, empty strings / ``None`` when
    nothing matches. Scores OS match (required), arch match (preferred), and an
    installable extension over sidecars, so it is robust to however CI names the
    files. The ``.sha256`` sidecar is paired by filename when present.

    Format follows install: an AppImage install must only ever receive a
    ``.AppImage`` (a zip's tree can't replace a single file), and a zip install
    must never be switched to an AppImage just because the name sorts first, so
    ``prefer_appimage`` hard-partitions the pool instead of merely ranking.

    Flavor follows the HOST, and also hard-partitions: a Mac below macOS 15
    can only run the "legacy" bundle (see :data:`_MACOS_LEGACY_BELOW`), so it
    must never be offered the regular one, and a macOS 15+ machine must never
    be quietly downgraded to legacy just because the name sorts first. A
    machine that moves across the boundary (an OS upgrade, or a manually
    downloaded wrong flavor) self-corrects on its next update.
    """
    if want_legacy is None:
        # Legacy is a macOS-only concept: never let an old-mac HOST partition
        # a selection for some other OS (only tests select cross-OS today,
        # but the guard keeps this helper honest about what legacy means).
        want_legacy = os_key == "macos" and _macos_wants_legacy()
    os_tokens = _OS_TOKENS.get(os_key, ())
    arch_tokens = _ARCH_TOKENS.get(arch, ())
    all_arch_tokens = tuple(t for toks in _ARCH_TOKENS.values() for t in toks)
    by_name = {a.get("name", ""): a.get("browser_download_url", "") for a in assets}
    arch_match: list[str] = []  # tagged for our arch
    arch_agnostic: list[str] = []  # no arch token at all (a universal asset)
    for name in by_name:
        low = name.lower()
        if low.endswith(_SIDECAR_EXTS) or not any(t in low for t in os_tokens):
            continue
        if low.endswith(".appimage") != prefer_appimage:
            continue
        if ("legacy" in low) != want_legacy:
            continue
        if any(t in low for t in arch_tokens):
            arch_match.append(name)
        elif not any(t in low for t in all_arch_tokens):
            arch_agnostic.append(name)
        # else: tagged for a *different* arch → skip (never install the wrong arch)
    pool = arch_match or arch_agnostic
    if not pool:
        return "", "", None
    # Prefer a real installable payload over anything else, stable by name.
    pool.sort(key=lambda n: (0 if n.lower().endswith(_INSTALL_EXTS) else 1, n))
    name = pool[0]
    sha = by_name.get(name + ".sha256") or by_name.get(name + ".sha256sum") or None
    return name, by_name.get(name, ""), sha


def _session() -> requests.Session:
    sess = requests.Session()
    sess.headers["User-Agent"] = _UA
    return sess


def _exe_suffix(os_key: str) -> str:
    return ".exe" if os_key == "windows" else ""


# --------------------------------------------------------------------------- #
# Cross-process guard
# --------------------------------------------------------------------------- #
class _StagingLock:
    """An exclusive, OS-held lock over the staging directory.

    Two copies of Waves share one config dir, so they share one ``updates/``
    folder, and nothing kept them apart: the second one's extraction rmtree'd
    the staged tree the first one's helper was about to swap in, and rewrote
    the helper script while cmd.exe was executing it (cmd re-reads a batch file
    by byte offset, so a rewritten script can resume anywhere, including inside
    the branch that recursively deletes the install folder).

    The lock lives on an open descriptor, so the OS drops it when the process
    does, however it dies: a lock file left behind by a crash is never a lock.
    On Windows it is also held for the whole life of a process that has armed a
    swap helper, which is what makes "an update is already staged" a fact the
    other copy can read rather than a guess about a pid.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._fd: int | None = None

    def try_acquire(self) -> bool:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(self._path, os.O_RDWR | os.O_CREAT, 0o600)
        except OSError:
            return False
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(fd)
            return False
        self._fd = fd
        return True

    def acquire(self) -> None:
        if not self.try_acquire():
            raise UpdaterError("Another copy of Waves is installing this update; restart Waves to finish it.")

    def release(self) -> None:
        fd, self._fd = self._fd, None
        if fd is None:
            return
        try:
            os.close(fd)  # closing drops the lock on both platforms
        except OSError:
            logger.debug("could not release the staging lock", exc_info=True)


# --------------------------------------------------------------------------- #
# Manager
# --------------------------------------------------------------------------- #
class AppUpdater:
    """Check for and install a newer Waves build from GitHub Releases."""

    def __init__(
        self,
        app_dir: str | os.PathLike,
        current_version: str,
        repo: str | None = None,
    ) -> None:
        self.app_dir = Path(app_dir)
        self.current_version = current_version
        self.repo = (repo if repo is not None else REPO).strip().strip("/")
        self.os_key, self.arch = _os_arch()
        # Windows: the result of an install whose detached swap helper is
        # already waiting for this process to exit. A second install in the
        # same session must not extract over the staged tree that helper will
        # mirror, nor spawn a second helper to race the first over the same
        # backup folder (either race ends with a copy of the app deleted).
        self._armed_result: dict | None = None
        # ...and the staging lock that says so to the OTHER copies of Waves,
        # held from the moment a helper is armed until this process exits.
        self._armed_lock: _StagingLock | None = None
        # The NAME (never the path) of a previous version this install kept
        # rather than deleted, because it held files that were not part of the
        # build. Whole copies of the app, so the user is told once per update
        # in the message that stays on screen, not only in a passing status.
        # Set by every keep, on all three platforms: the swap the app performs
        # itself, and (at the next launch) the one the Windows helper performs
        # minutes after this process is gone.
        self.kept_backup: str = ""
        # ...and set when that folder could not be made unclaimable (see
        # _mark_kept). The user then has to move the files out themselves
        # before the next update, so the message must say so rather than
        # promising a folder Waves cannot actually keep.
        self.kept_unprotected: bool = False

    # ----- configuration / locations ------------------------------------- #
    def is_configured(self) -> bool:
        """True once a release repo is set and the platform is recognised."""
        return bool(self.repo) and bool(self.os_key)

    def releases_url(self) -> str:
        return f"https://github.com/{self.repo}/releases" if self.repo else ""

    @property
    def staging_dir(self) -> Path:
        return self.app_dir / "updates"

    # ----- status / update check ----------------------------------------- #
    def status(self) -> dict:
        """Static snapshot for the UI (no network).

        ``state`` ∈ {``not_configured``, ``source``, ``managed``, ``ready``}:
        ``ready`` means a frozen, configured build that can self-install;
        ``managed`` is a frozen build owned by a package manager (checks still
        work, installs go through that manager); ``source`` can still *check*
        but not install; ``not_configured`` is fully dormant.
        """
        frozen = is_frozen()
        configured = self.is_configured()
        channel = managed_channel()
        if not configured:
            state = "not_configured"
        elif not frozen:
            state = "source"
        elif channel:
            state = "managed"
        else:
            state = "ready"
        return {
            "state": state,
            "configured": configured,
            "frozen": frozen,
            # An update is staged and only a restart is missing. True after an
            # install this session AND after resume_pending_apply() re-armed one
            # from an earlier session, so the card reads the same either way.
            "pending_restart": self._armed_result is not None,
            "pending_version": (self._armed_result or {}).get("version", ""),
            # A previous version kept whole because it held files that were not
            # part of the build, named so the card can say where they are. Set
            # by an install this session AND, on Windows, by the launch after
            # the helper's own keep, which has no other voice than update.log.
            "kept_backup": self.kept_backup,
            "kept_unprotected": self.kept_unprotected,
            "can_self_install": configured and frozen and not channel,
            # A managed install whose manager has a runnable upgrade command
            # (and the manager's binary is present) still gets one-click
            # updates; install() routes to the manager instead of the
            # self-installer.
            "can_managed_install": bool(configured and frozen and channel and managed_upgrade_command(channel)),
            "channel": channel,
            "channel_label": channel_label(channel) if channel else "",
            "update_hint": channel_hint(channel),
            "current_version": self.current_version,
            "repo": self.repo,
            "releases_url": self.releases_url(),
            "os": self.os_key,
            "arch": self.arch,
        }

    def latest(self, session: requests.Session | None = None) -> Release | None:
        """Resolve the latest release + this platform's asset, or ``None``.

        Network/parse errors propagate to the caller (treated as best-effort).
        """
        if not self.is_configured():
            return None
        sess = session or _session()
        url = f"https://api.github.com/repos/{self.repo}/releases/latest"
        resp = sess.get(url, timeout=_TIMEOUT, headers={"Accept": "application/vnd.github+json"})
        resp.raise_for_status()
        data = resp.json()
        tag = data.get("tag_name") or ""
        if not tag:
            return None
        assets = data.get("assets", [])
        name, asset_url, sha_url = _select_asset(
            assets, self.os_key, self.arch, prefer_appimage=bool(_running_appimage())
        )
        # The signed manifest + its detached signature are single, fixed-named
        # assets shared by every platform in the release (see tools/sign_manifest.py).
        by_name = {a.get("name", ""): a.get("browser_download_url", "") for a in assets}
        return Release(
            version=tag,
            asset=name,
            url=asset_url,
            sha256_url=sha_url,
            sha256sums_url=by_name.get("SHA256SUMS") or None,
            sig_url=by_name.get("SHA256SUMS.sig") or None,
            notes_url=data.get("html_url") or self.releases_url(),
            notes=data.get("body") or "",
        )

    def update_available(self, session: requests.Session | None = None) -> tuple[bool, str, str]:
        """Return ``(available, current_version, latest_version)``.

        Works from source too (so a dev learns a release exists); a blank/unset
        repo reports no update without touching the network.
        """
        if not self.is_configured():
            return False, self.current_version, ""
        rel = self.latest(session)
        latest_v = rel.version if rel else ""
        avail = bool(rel and _is_newer(latest_v, self.current_version))
        # Callers display this next to a "v" of their own; hand back the bare
        # version, not the tag (a "v0.1.3" tag otherwise renders as "vv0.1.3").
        return avail, self.current_version, latest_v.lstrip("vV")

    # ----- install ------------------------------------------------------- #
    def install(
        self,
        release: Release | None = None,
        progress_cb=None,
        log_cb=None,
        abort: Event | None = None,
        session: requests.Session | None = None,
    ) -> dict:
        """Download, verify and apply the newest build, then return a result.

        Gated: raises if the build is unconfigured or not frozen (a source run
        can't replace itself, the UI opens the Releases page instead). The
        download → checksum → stage steps are atomic against a temp dir; the
        platform swap only touches the install on success.
        """

        def _log(msg: str) -> None:
            logger.info("updater: %s", msg)
            if log_cb:
                log_cb(msg)

        def _check_abort() -> None:
            if abort is not None and abort.is_set():
                raise UpdateCancelled()

        if not self.is_configured():
            raise UpdaterError("Updates aren't configured for this build.")
        if not is_frozen():
            raise UpdaterError("Self-update is only available in packaged builds, open the Releases page to update.")
        if self._armed_result is not None:
            _log("an update is already staged; restart to finish")
            return self._staged_result(self._armed_result, release)
        channel = managed_channel()
        if channel:
            cmd = managed_upgrade_command(channel)
            if cmd is None:
                hint = channel_hint(channel)
                raise UpdaterError(
                    f"This copy of Waves is managed by {channel_label(channel)}; update it there"
                    + (f" ({hint})" if hint else "")
                    + "."
                )
            return self._managed_upgrade(channel, cmd, progress_cb, _log, abort, session)

        self.staging_dir.mkdir(parents=True, exist_ok=True)
        # Everything from here to the armed helper is one exclusive stretch:
        # two copies of Waves share this folder, and the second one's
        # extraction would otherwise delete the tree the first one's helper is
        # waiting to swap in. On Windows the lock is KEPT once a helper is
        # armed (released only when this process exits), so the other copy is
        # told to restart rather than staging a rival swap.
        lock = _StagingLock(self.staging_dir / self._LOCK_NAME)
        lock.acquire()
        keep_lock = False
        try:
            # A swap staged by a copy of Waves that has since exited: nothing
            # is waiting for anyone to quit any more, so take it over (arming a
            # helper against THIS process) rather than downloading it again on
            # top of the tree that helper is going to move.
            staged = self._take_over_staged_swap() if self.os_key == "windows" else None
            if staged is not None:
                _log("an update is already staged; restart to finish")
                self._armed_lock = lock
                keep_lock = True
                return self._staged_result(staged, release)
            # Resolved inside the lock so a copy that only has to restart never
            # goes to the network to be told what it already staged.
            sess = session or _session()
            if release is None:
                _log("resolving latest release")
                release = self.latest(sess)
            if release is None or not release.url:
                kind = "AppImage build" if _running_appimage() else "build"
                raise UpdaterError(f"No Waves {kind} is available for {self.os_key}/{self.arch}.")
            result = self._install_locked(release, progress_cb, _log, _check_abort, abort, sess)
            if self.os_key == "windows":
                self._armed_result = dict(result)
                self._write_armed_marker(result)
                self._armed_lock = lock
                keep_lock = True
            return result
        finally:
            if not keep_lock:
                lock.release()

    @staticmethod
    def _staged_result(staged: dict, release: Release | None) -> dict:
        """An install result that is a swap staged EARLIER, flagged as one.

        Both short circuits above hand back a version this call did not stage
        and may not even have been asked for: a Windows swap staged yesterday
        is applied by the restart, whatever release the user clicked Install on
        today (staging a second one would race the armed helper over the same
        backup folder, so the short circuit itself is deliberate). The version
        in here is the one the restart will really land, and
        ``already_staged`` says so, so no caller can present it as the release
        it asked for. ``requested_version`` is filled in only when the caller
        named a release; the UI otherwise knows what it offered.
        """
        return {
            **staged,
            "already_staged": True,
            "requested_version": release.version if release is not None else "",
        }

    def _install_locked(self, release: Release, progress_cb, _log, _check_abort, abort, sess) -> dict:
        """The download, verify and apply half of :meth:`install`, under the lock."""
        _check_abort()

        _log(f"downloading {release.version} ({release.asset})")
        # release.asset comes from the (untrusted, pre-verification) release JSON, so
        # strip any path component before it reaches the filesystem as a temp suffix.
        suffix = os.path.basename((release.asset or "dl").replace("\\", "/")) or "dl"
        with tempfile.NamedTemporaryFile(dir=self.staging_dir, suffix="-" + suffix, delete=False) as tmp:
            payload = Path(tmp.name)
        applied_clean = False
        try:
            self._download(sess, release.url, payload, progress_cb, abort)
            _check_abort()

            # Verification is mandatory and fail-closed: the updater downloads and
            # *executes* code, so it must prove both authenticity and integrity
            # BEFORE anything is extracted, swapped in, or de-quarantined. The trust
            # anchor is an Ed25519 signature over the SHA256SUMS manifest, checked
            # against UPDATE_PUBLIC_KEY, a key baked into this binary, never on the
            # download host. A same-channel .sha256 alone proves only transport
            # integrity; the signature is what stops a tampered release. Order matters:
            # the manifest's signature is verified first, and only the authenticated
            # manifest's hash is then trusted to check the payload.
            if not UPDATE_PUBLIC_KEY:
                raise UpdaterError("Refusing to install an update: this build has no update-signing key configured.")
            if not release.sha256sums_url or not release.sig_url:
                raise UpdaterError("Refusing to install an update: the release has no signed checksum manifest.")
            _log("verifying update signature")
            manifest = self._fetch_manifest(sess, release.sha256sums_url)
            signature = self._fetch_signature(sess, release.sig_url)
            if not manifest or not signature:
                raise UpdaterError("Refusing to install an update: could not fetch the signed checksum manifest.")
            if not verify_signature(manifest, signature, UPDATE_PUBLIC_KEY):
                raise UpdaterError("Refusing to install an update: the checksum manifest's signature is invalid.")
            manifest_text = manifest.decode("utf-8", "replace")
            # Anti-rollback: the signed manifest carries the release version, so an
            # attacker can't replay an older (still-validly-signed) release to force a
            # downgrade to a build with known holes. The version is trusted only
            # because it lives inside the signature-verified manifest (not the
            # unsigned GitHub API tag).
            mver = _manifest_version(manifest_text)
            if not mver:
                raise UpdaterError("Refusing to install an update: the signed manifest has no version line.")
            if _is_older(mver, self.current_version):
                raise UpdaterError(
                    f"Refusing to install {mver}: it is older than the installed {self.current_version} (downgrade protection)."
                )
            sums = parse_sha256sums(manifest_text)
            expected = sums.get(release.asset)
            if not expected:
                raise UpdaterError(f"Refusing to install an update: {release.asset} is not in the signed manifest.")
            _log("verifying checksum")
            actual = _sha256_file(payload)
            if actual.lower() != expected.lower():
                raise UpdaterError(f"Checksum mismatch (expected {expected[:12]}…, got {actual[:12]}…).")

            _check_abort()
            _log("installing")
            applied_to = self._apply(payload, release, _log, abort=abort)
            applied_clean = True
        finally:
            payload.unlink(missing_ok=True)
            # A failed (or cancelled) apply can leave a full extracted app
            # copy under staged/; without this it sits in the config dir until
            # the next update attempt happens to overwrite it. Only our own
            # staging tree is touched, and only on failure: on success the
            # platform move has already consumed it. _rmtree never raises.
            if not applied_clean:
                _rmtree(self.staging_dir / "staged")

        self._write_manifest(release)
        _log(f"installed {release.version}")
        return {
            "ok": True,
            "version": release.version,
            "applied_to": str(applied_to),
            "relaunch": True,
            # A whole previous copy of the app, kept because it held files the
            # build did not ship. Empty on every ordinary update.
            "kept_backup": self.kept_backup,
            # ...and true when Waves could not reserve that folder against its
            # own next update, the one case where the message must ask the user
            # to move those files out themselves rather than promise them.
            "kept_unprotected": self.kept_unprotected,
            # This one really is the release that was just fetched and applied.
            # The two short circuits in install() hand back an earlier one and
            # say so (see _staged_result), so the key is always present and a
            # caller never has to guess which it is holding.
            "already_staged": False,
        }

    # ----- a staged swap that has not happened yet ----------------------- #
    _LOCK_NAME = "install.lock"
    _ARMED_NAME = "armed.json"

    def _armed_marker(self) -> Path:
        return self.staging_dir / self._ARMED_NAME

    def _read_armed_marker(self) -> dict | None:
        """The install result of a Windows swap that is staged but not applied."""
        try:
            data = json.loads(self._armed_marker().read_text("utf-8"))
        except (OSError, ValueError):
            return None
        # Valid JSON that is not an object (a list, a bare number) must not
        # crash a launch; a marker we cannot read is a marker we do not have.
        return data if isinstance(data, dict) and data.get("version") else None

    def _write_armed_marker(self, result: dict) -> None:
        try:
            self._armed_marker().write_text(json.dumps(result), encoding="utf-8")
        except OSError:
            logger.debug("could not record the staged update", exc_info=True)

    def _clear_armed_marker(self) -> None:
        try:
            self._armed_marker().unlink(missing_ok=True)
        except OSError:
            logger.debug("could not clear the staged-update marker", exc_info=True)

    def resume_pending_apply(self) -> dict | None:
        """Re-arm a Windows swap helper whose window closed without applying.

        The helper is armed when the update is installed, not when the user
        clicks Restart, and it waits for THIS process to exit: it gives up
        after ``_HELPER_WAIT_TICKS`` (a music app is regularly open longer than
        that), and a session that ends in a shutdown rather than a quit never
        wakes it at all. install() has already returned ok and the UI has
        already said "Updated, restart to finish", so without this the user
        quits, relaunches into the old version, and is told nothing: the
        updater reads as broken on the one release everybody has to cross.

        Called once at startup. If the staged build is still on disk (a whole
        tree, or the single exe staged beside the install) and still newer than
        what is running, a fresh helper is armed against this process and the
        caller re-shows the restart prompt. Returns the
        original install result, or None when there is nothing pending.
        """
        if self.os_key != "windows" or not is_frozen() or not self.is_configured():
            return None
        if self._armed_result is not None:
            return dict(self._armed_result)
        if self._read_armed_marker() is None:
            return None  # the common case: nothing staged, no lock taken
        lock = _StagingLock(self.staging_dir / self._LOCK_NAME)
        if not lock.try_acquire():
            return None  # another copy of Waves owns this update; it will apply it
        keep_lock = False
        try:
            result = self._take_over_staged_swap()
            if result is not None:
                self._armed_lock = lock
                keep_lock = True
            return result
        finally:
            if not keep_lock:
                lock.release()

    def _take_over_staged_swap(self) -> dict | None:
        """Arm a helper for a swap staged earlier, or clear one that is spent.

        The staging lock must already be held. Returns the original install
        result when a helper is now waiting on this process, None when there
        was nothing left to apply (in which case the marker and any leftover
        tree are cleared, so a fresh install is free to proceed). Both Windows
        layouts are understood: the standalone tree staged at ``Waves.new`` and
        the single exe staged at ``Waves.exe.new``.
        """
        pending = self._read_armed_marker()
        if pending is None:
            return None
        target = _current_exe()
        install_root = target.parent
        # Same rule the arming side used, so a ".new" that turned out to be
        # someone else's folder is neither read from nor deleted here either.
        new_tree = _spare_sibling(install_root, ".new", target.name)
        # ...and the same spelling the single-file layout stages under, which
        # is a FILE beside the exe rather than a tree. Both layouts arm a
        # helper and both write the marker, so both have to be resumable here:
        # while only the tree was, a staged single-file update was announced as
        # installed and then thrown away at the next boot as "nothing staged".
        new_exe = target.with_suffix(target.suffix + ".new")
        # Whatever comes back from here was staged by an EARLIER session, so it
        # is a previously staged swap by definition: the marker was written
        # with the flag false (it was a fresh install then) and would otherwise
        # travel on unchanged into a result that says this call staged it.
        pending = {**pending, "already_staged": True}
        if not _is_newer(pending.get("version", ""), self.current_version):
            # The swap landed: this IS the staged build. Clear the leftovers.
            self._clear_armed_marker()
            _rmtree(new_tree)
            if new_exe.is_file():
                _rmtree(new_exe)
            self._notice_kept_backup(install_root)
            return None
        if (new_tree / target.name).is_file():
            logger.info("updater: arming the staged swap for %s", pending.get("version", ""))
            self._arm_tree_helper(install_root, new_tree, target)
            self._armed_result = dict(pending)
            return dict(pending)
        if new_exe.is_file() and new_exe.stat().st_size:
            logger.info("updater: arming the staged swap for %s", pending.get("version", ""))
            self._arm_exe_helper(target, new_exe)
            self._armed_result = dict(pending)
            return dict(pending)
        # Staged build gone (a helper already moved it in, someone cleaned the
        # folder): there is nothing left to apply.
        self._clear_armed_marker()
        _rmtree(new_tree)
        self._notice_kept_backup(install_root)
        return None

    def _notice_kept_backup(self, install_root: Path) -> None:
        """Pick up a backup the Windows swap helper kept, and name it.

        That keep happens minutes after this process is gone (the reclaim
        robocopy failed, so the folder holds files that were not part of the
        build) and the helper's only voice is update.log: nobody is running to
        put it on screen. This is the launch after that swap, so the folder is
        found by the mark the helper left and reported through the same
        ``kept_backup`` the two keeps the app performs itself report through.

        Best-effort by design: a listing that fails, or a folder someone has
        since cleaned up, simply means there is nothing to say.
        """
        try:
            siblings = sorted(install_root.parent.iterdir())
        except OSError:
            return
        for cand in siblings:
            if not cand.name.startswith(install_root.name + ".old"):
                continue
            if (cand / _KEPT_MARKER).is_file():
                self.kept_backup = cand.name
                logger.info("updater: a previous version was kept whole because it held files the build did not ship")
                return

    def _managed_upgrade(self, channel: str, cmd: list[str], progress_cb, log, abort: Event | None, session) -> dict:
        """Upgrade a package-manager-owned install by running the manager itself.

        The manager does its own download and checksum verification (Homebrew
        checks the cask's sha256), so nothing is fetched or verified here; this
        just runs the upgrade with its output streamed to the UI and coarse
        progress milestones keyed off the output. Replacing a running .app is
        safe on macOS (the process keeps its open files; the restart lands in
        the new bundle). The command's argv is a fixed table entry, never
        user input.
        """
        label = channel_label(channel)
        # Version is cosmetic here (the completion message); the manager decides
        # what it actually installs. Best-effort, never a reason to fail.
        version = ""
        try:
            rel = self.latest(session or _session())
            version = rel.version if rel else ""
        except Exception:
            logger.debug("managed upgrade: could not resolve the latest version tag", exc_info=True)

        log(f"updating via {label}")
        if progress_cb:
            progress_cb(5.0)
        env = dict(os.environ)
        env.setdefault("HOMEBREW_NO_ENV_HINTS", "1")
        env["PATH"] = os.path.dirname(cmd[0]) + os.pathsep + env.get("PATH", "")
        proc = subprocess.Popen(  # - fixed argv from the table above
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            errors="replace",
            env=env,
        )
        # The line reader blocks while the manager downloads silently, so a
        # watcher turns an abort into terminate() (which unblocks the reader
        # at EOF) instead of waiting for the next output line.
        reader_done = Event()
        if abort is not None:

            def _abort_watch() -> None:
                while not reader_done.is_set():
                    if abort.is_set():
                        proc.terminate()
                        return
                    abort.wait(0.25)

            Thread(target=_abort_watch, daemon=True).start()
        lines: list[str] = []
        try:
            # stdout=PIPE above guarantees a stream; `or ()` keeps a typing
            # stub / exotic Popen replacement from crashing the loop.
            for raw in proc.stdout or ():
                line = raw.strip()
                if not line:
                    continue
                lines.append(line)
                low = line.lower()
                if progress_cb:
                    if "downloading" in low or "fetching" in low:
                        progress_cb(35.0)
                    elif "installing" in low or "upgrading" in low or "moving" in low:
                        progress_cb(70.0)
                log(line[:160])
            code = proc.wait()
        finally:
            reader_done.set()
            if proc.poll() is None:
                proc.terminate()
        if abort is not None and abort.is_set():
            raise UpdateCancelled()
        output = "\n".join(lines)
        if code != 0:
            tail = "\n".join(lines[-4:])
            raise UpdaterError(f"{label} reported an error:\n{tail}" if tail else f"{label} exited with code {code}.")
        if re.search(r"already (installed|up-to-date)|not upgrading", output, re.IGNORECASE):
            raise UpdaterError(
                f"{label} does not see the new version yet; its package lists may be stale."
                + (
                    f" Try `{channel_hint(channel)}` in a terminal after `brew update`."
                    if channel_hint(channel)
                    else ""
                )
            )
        if progress_cb:
            progress_cb(100.0)
        log(f"updated via {label}")
        return {"ok": True, "version": version, "applied_to": "", "relaunch": True, "channel": channel}

    # ----- internals ----------------------------------------------------- #
    def _download(self, sess, url: str, dest: Path, progress_cb, abort: Event | None) -> None:
        with sess.get(url, stream=True, timeout=_TIMEOUT) as resp:
            resp.raise_for_status()
            total = int(resp.headers.get("Content-Length") or 0)
            done = 0
            with open(dest, "wb") as fh:
                for chunk in resp.iter_content(chunk_size=_CHUNK):
                    if abort is not None and abort.is_set():
                        raise UpdateCancelled()
                    if not chunk:
                        continue
                    fh.write(chunk)
                    done += len(chunk)
                    if progress_cb and total:
                        progress_cb(min(100.0, done / total * 100.0))
            if progress_cb and not total:
                progress_cb(100.0)

    def _fetch_manifest(self, sess, url: str | None) -> bytes | None:
        """Fetch the raw SHA256SUMS bytes (returns ``None`` on any failure → abort).

        The exact bytes matter: they are what the signature is verified against, so
        this never decodes, strips, or normalises them.
        """
        if not url:
            return None
        try:
            resp = sess.get(url, timeout=_TIMEOUT)
            resp.raise_for_status()
        except Exception:
            logger.debug("could not fetch SHA256SUMS from %s", url, exc_info=True)
            return None
        return resp.content

    def _fetch_signature(self, sess, url: str | None) -> str | None:
        """Fetch the base64 SHA256SUMS.sig text (returns ``None`` on failure → abort)."""
        if not url:
            return None
        try:
            resp = sess.get(url, timeout=_TIMEOUT)
            resp.raise_for_status()
        except Exception:
            logger.debug("could not fetch SHA256SUMS.sig from %s", url, exc_info=True)
            return None
        return resp.text.strip() or None

    def _write_manifest(self, release: Release) -> None:
        try:
            self.staging_dir.mkdir(parents=True, exist_ok=True)
            with open(self.staging_dir / "applied.json", "w", encoding="utf-8") as fh:
                json.dump({**asdict(release), "applied_at": int(time.time())}, fh, indent=2)
        except Exception:
            logger.debug("could not write update manifest", exc_info=True)

    #: The file list of the build that is installed, kept beside the rest of
    #: the updater's state (in the app data dir, never in the install tree,
    #: which the swap replaces whole). applied.json next to it holds the
    #: release metadata; this holds what that release put on disk.
    _SHIPPED_NAME = "installed_files.json"

    def _record_shipped(self, paths: list[str], version: str) -> None:
        """Record the file list of the build just installed, for the NEXT update.

        Without it the foreign-file net is a bare set-difference of the backup
        against the fresh install, so every file a release legitimately drops
        (a Qt library the trim sweep sheds, a dependency replaced by a shim, a
        bundled folder that moved) is indistinguishable from a file the user
        put in the install folder. macOS then strands a whole extra copy of the
        app behind a notice that is not true, and the Linux and Windows reclaim
        carries the dropped files back into the brand-new install, where they
        stay for good, a release's worth at a time.

        Nothing wrote this before, so the first update from a build that
        predates it still finds no list; a missing list means the conservative
        answer, everything unmatched is treated as the user's, which is what
        the updater did all along.
        """
        if not paths:
            return
        try:
            self.staging_dir.mkdir(parents=True, exist_ok=True)
            with open(self.staging_dir / self._SHIPPED_NAME, "w", encoding="utf-8") as fh:
                json.dump({"version": version, "paths": sorted(paths)}, fh)
        except Exception:
            logger.debug("could not record the installed file list", exc_info=True)

    def _shipped_by_the_old_build(self) -> set[str] | None:
        """What the build being replaced shipped, or ``None`` when that is unknown.

        Trusted only when the recorded version is the one actually running. A
        record left by an update whose swap never landed, or by an install the
        user replaced by hand, describes files that are not the ones on disk,
        and treating a user's file as the old build's is how a backup gets
        deleted with their music in it. Two installs of the SAME version ship
        the same files, so the version is the only thing worth checking.

        ``None`` is the conservative answer and every failure returns it.
        """
        try:
            data = json.loads((self.staging_dir / self._SHIPPED_NAME).read_text("utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(data, dict) or not isinstance(data.get("paths"), list):
            return None
        recorded = _parse_version(str(data.get("version", "")))
        if not recorded or recorded != _parse_version(self.current_version):
            return None
        return {p for p in data["paths"] if isinstance(p, str)}

    # --- platform swap --------------------------------------------------- #
    # These run only from a frozen, configured build (guarded in install()), so
    # they execute exactly where they can be hardened against the real CI
    # artifacts. Each extracts the payload (zip or raw) and swaps it next to the
    # running executable; Windows defers the swap to a helper because a running
    # .exe can't overwrite itself.
    def _apply(self, payload: Path, release: Release, log, abort: Event | None = None) -> Path:
        # The last chance to honour Cancel: extraction is the slow half of the
        # apply, so check right after it, before the platform swap touches the
        # install (aborting MID-swap would be worse than finishing it, so the
        # swap itself stays uninterruptible, like _managed_upgrade's runner).
        def _check_abort() -> None:
            if abort is not None and abort.is_set():
                raise UpdateCancelled()

        # Running from an AppImage: the executable path is inside a read-only
        # squashfs mount; the real install is the single .AppImage file, so
        # swap that (asset selection already guaranteed an .AppImage payload).
        appimage = _running_appimage()
        if appimage:
            staged = self._extract_payload(payload, release.asset, log)
            _check_abort()
            if not staged.is_file():
                raise UpdaterError("Refusing to install: expected a single-file AppImage payload.")
            return self._apply_unix(staged, Path(appimage), log)
        target = _current_exe()
        staged = self._extract_payload(payload, release.asset, log)
        _check_abort()
        # What this build ships is read off the staged copy, the one moment it
        # is separable from whatever the user keeps in the install folder, and
        # recorded only once the apply has returned, so a failed one records
        # nothing. The NEXT update is what reads it (see _record_shipped).
        if self.os_key == "macos":
            shipped = _tree_paths(staged)
            applied = self._apply_macos(staged, target, log)
            self._record_shipped(shipped, release.version)
            return applied
        # Nuitka --standalone ships a multi-file directory (the .dist tree). When the
        # asset extracted to a nested directory, swap the WHOLE tree, replacing only
        # the executable would leave the new binary running against the old bundled
        # Qt/Python libraries. A genuine single-file build lands directly in the
        # staging root and is swapped as one file.
        staging_root = (self.staging_dir / "staged").resolve()
        is_tree = staged.is_file() and staged.parent.resolve() != staging_root
        if not is_tree:
            if self.os_key == "windows":
                return self._apply_windows(staged, target, log)
            return self._apply_unix(staged, target, log)
        shipped = _tree_paths(staged.parent)
        applied = (
            self._apply_windows_tree(staged.parent, target, log)
            if self.os_key == "windows"
            else self._apply_unix_tree(staged.parent, target, log)
        )
        self._record_shipped(shipped, release.version)
        return applied

    def _extract_payload(self, payload: Path, asset: str, log) -> Path:
        """Return a path to the new executable/bundle extracted from the asset.

        ``.zip`` and ``.tar.gz``/``.tgz`` archives are unpacked (safely) into the
        staging dir; a raw single binary (``.exe``/``.appimage`` or an
        extensionless build) is used as-is. Any archive format we can't unpack is
        never selected in the first place (see :data:`_INSTALL_EXTS`), so it can't
        reach here to be copied raw and mis-executed.
        """
        low = (asset or payload.name).lower()
        out = self.staging_dir / "staged"
        if out.exists():
            _rmtree(out)
        out.mkdir(parents=True, exist_ok=True)
        if low.endswith(".zip"):
            with zipfile.ZipFile(payload) as zf:
                self._safe_extractall(zf, out)
            return self._find_executable(out)
        if low.endswith((".tar.gz", ".tgz")):
            with tarfile.open(payload, "r:gz") as tf:
                self._safe_extractall_tar(tf, out)
            return self._find_executable(out)
        # An archive/disk-image format we don't unpack must never fall through to
        # the raw-binary path below (which would copy it verbatim and try to exec
        # it, a bricked install). _select_asset only *ranks* installable formats,
        # so a release carrying nothing but, say, a .dmg can still reach here.
        if low.endswith((".dmg", ".7z", ".pkg", ".rar", ".tar", ".tar.bz2", ".tar.xz", ".gz", ".bz2", ".xz")):
            raise UpdaterError(f"Refusing to install {Path(asset).name}: this build can't unpack that archive format.")
        # Raw binary asset (Linux/macOS single-file build): copy into staging.
        dest = out / (Path(asset).name or "Waves")
        dest.write_bytes(payload.read_bytes())
        return dest

    @staticmethod
    def _safe_extractall(zf: zipfile.ZipFile, dest: Path) -> None:
        """Extract ``zf`` into ``dest``, preserving symlinks + exec bits and
        refusing any member (or symlink target) that escapes ``dest``.

        Plain :meth:`zipfile.ZipFile.extractall` flattens symlink members into
        regular files, which breaks a macOS ``.app`` whose frameworks rely on
        ``Versions/Current`` symlinks, so we recreate symlinks ourselves and
        carry over the executable bit. The escape checks are defence-in-depth: the
        payload is already signature-verified, but extraction must never write or
        point outside the staging directory regardless.
        """
        root = dest.resolve()

        def _within(p: Path) -> bool:
            rp = p.resolve()
            return rp == root or root in rp.parents

        for info in zf.infolist():
            out_path = root / info.filename
            if not _within(out_path):
                raise UpdaterError(f"Refusing to extract unsafe archive member: {info.filename!r}")
            mode = (info.external_attr >> 16) & 0xFFFF
            if stat.S_ISLNK(mode):
                link_target = zf.read(info).decode("utf-8", "strict")
                resolved = Path(link_target) if os.path.isabs(link_target) else out_path.parent / link_target
                if not _within(resolved):
                    raise UpdaterError(f"Refusing unsafe symlink {info.filename!r} -> {link_target!r}")
                out_path.parent.mkdir(parents=True, exist_ok=True)
                if out_path.is_symlink() or out_path.exists():
                    out_path.unlink()
                os.symlink(link_target, out_path)
            elif info.is_dir():
                out_path.mkdir(parents=True, exist_ok=True)
            else:
                out_path.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(info) as src, open(out_path, "wb") as dst:
                    shutil.copyfileobj(src, dst)
                if mode & 0o111:  # carry over the executable bit for binaries
                    out_path.chmod(out_path.stat().st_mode | 0o755)

    @staticmethod
    def _safe_extractall_tar(tf: tarfile.TarFile, dest: Path) -> None:
        """Extract ``tf`` into ``dest``, preserving symlinks + exec bits and
        refusing any member (or link target) that escapes ``dest``.

        Mirrors :meth:`_safe_extractall` for the gzip-tar case: :meth:`tarfile`'s
        own ``extractall`` will happily write ``../`` members and absolute paths
        outside the target, so we place each member ourselves and reject anything
        that resolves outside ``dest``, including symlink/hardlink targets. The
        payload is already signature-verified, but extraction must never write or
        point outside the staging directory regardless.
        """
        root = dest.resolve()

        def _within(p: Path) -> bool:
            rp = p.resolve()
            return rp == root or root in rp.parents

        for member in tf.getmembers():
            out_path = root / member.name
            if os.path.isabs(member.name) or not _within(out_path):
                raise UpdaterError(f"Refusing to extract unsafe archive member: {member.name!r}")
            if member.issym() or member.islnk():
                # linkname is relative to the member's own directory (symlink) or
                # to the archive root (hardlink); reject either if it escapes.
                base = out_path.parent if member.issym() else root
                target = Path(member.linkname)
                resolved = target if target.is_absolute() else base / target
                if target.is_absolute() or not _within(resolved):
                    raise UpdaterError(f"Refusing unsafe link {member.name!r} -> {member.linkname!r}")
                out_path.parent.mkdir(parents=True, exist_ok=True)
                if out_path.is_symlink() or out_path.exists():
                    out_path.unlink()
                if member.issym():
                    os.symlink(member.linkname, out_path)
                else:
                    try:
                        os.link(root / member.linkname, out_path)
                    except FileNotFoundError as exc:  # hardlink target not yet extracted
                        raise UpdaterError(
                            f"Refusing to install: archive hardlink {member.name!r} precedes its target."
                        ) from exc
            elif member.isdir():
                out_path.mkdir(parents=True, exist_ok=True)
            elif member.isfile():
                out_path.parent.mkdir(parents=True, exist_ok=True)
                src = tf.extractfile(member)
                if src is None:
                    continue
                with src, open(out_path, "wb") as dst:
                    shutil.copyfileobj(src, dst)
                if member.mode & 0o111:  # carry over the executable bit for binaries
                    out_path.chmod(out_path.stat().st_mode | 0o755)
            # else: skip device/fifo/char nodes, never part of a build payload.

    def _find_executable(self, root: Path) -> Path:
        """Locate the app executable (or ``.app`` bundle) inside ``root``."""
        if self.os_key == "macos":
            apps = list(root.rglob("*.app"))
            if apps:
                return apps[0]
        suffix = _exe_suffix(self.os_key)
        # Prefer something named like the current executable.
        wanted = _current_exe().name
        cands = [p for p in root.rglob("*") if p.is_file()]
        named = [p for p in cands if p.name == wanted]
        if named:
            return named[0]
        if suffix:
            exes = [p for p in cands if p.suffix.lower() == suffix]
            if exes:
                return exes[0]
        if not cands:
            raise UpdaterError("Downloaded update contained no executable.")
        # Single-file builds: the lone (or largest) file is the binary.
        return max(cands, key=lambda p: p.stat().st_size)

    def _apply_unix(self, staged: Path, target: Path, log) -> Path:
        """Linux/macOS single-file: chmod + replace ``target`` (cross-device safe).

        The staged download usually lives under the app data dir (e.g.
        ``~/.config``) while the install can sit on a different volume, so
        ``os.replace`` (rename(2)) raises ``EXDEV``. On that, stage a copy *beside*
        the target (same filesystem) and do the final swap as a same-device rename.
        """
        _chmod_exec(staged)
        try:
            os.replace(staged, target)
        except OSError as exc:
            if exc.errno != errno.EXDEV:
                raise
            tmp = target.with_name(target.name + ".new")
            tmp.unlink(missing_ok=True)
            shutil.copy2(staged, tmp)
            _chmod_exec(tmp)
            os.replace(tmp, target)  # same filesystem now → atomic
            staged.unlink(missing_ok=True)
        return target

    def _apply_unix_tree(self, new_tree: Path, target: Path, log) -> Path:
        """Linux: replace the whole standalone ``.dist`` directory next to ``target``.

        The new binary must run against its own bundled libraries, so the entire
        install tree (the directory holding the executable) is swapped, not just
        the executable file.

        Cross-device safe: the staged tree usually lives under the app data dir
        (``~/.config``) while the install sits on another volume, so we first land
        the new tree *on the install filesystem* (``shutil.move`` copies across
        devices), then do the backup + swap as same-device renames, and roll the
        live install back if the swap fails partway, so a failed update never
        leaves the app uninstalled.
        """
        install_root = target.parent
        new_exe = new_tree / target.name
        if new_exe.exists():
            _chmod_exec(new_exe)
        # 1. Land the new tree on the install volume so the final swap is same-device.
        staged_same_dev = _spare_sibling(install_root, ".new", target.name)
        _rmtree(staged_same_dev)
        shutil.move(str(new_tree), str(staged_same_dev))  # rename if same-dev, copy if cross-dev
        # 2. Back up the live install, then swap the new tree in (both same-device).
        backup = _spare_sibling(install_root, ".old", target.name)
        _rmtree(backup)
        backed_up = False
        try:
            if install_root.exists():
                os.replace(install_root, backup)
                backed_up = True
            os.replace(staged_same_dev, install_root)
        except OSError:
            # Roll back: restore the live install if we moved it away but failed.
            if backed_up and not install_root.exists() and backup.exists():
                os.replace(backup, install_root)
            _rmtree(staged_same_dev)
            raise
        # 3. Confirm the swapped-in tree actually contains the executable before
        #    the backup is discarded; a payload that unpacked without it must
        #    never leave the app uninstalled. Roll the old install back instead.
        if not (install_root / target.name).is_file():
            _rmtree(install_root)
            if backed_up and backup.exists():
                os.replace(backup, install_root)
            raise UpdaterError("Updated install tree is missing the app executable; previous install restored.")
        # 4. The backup holds the whole OLD install folder, foreign files and
        #    all. Anything the new tree does not have, and the old build did
        #    not ship either, goes back in before the backup is discarded (see
        #    _reclaim_foreign).
        kept, protected = _reclaim_foreign(backup, install_root, log, shipped=self._shipped_by_the_old_build())
        if kept:
            # Same record the macOS keep makes, so the message that stays on
            # screen names the folder on this platform too.
            self.kept_backup = kept
            self.kept_unprotected = not protected
        return target

    def _apply_windows_tree(self, new_tree: Path, target: Path, log) -> Path:
        """Windows: the running ``.exe`` and its loaded DLLs lock the whole ``.dist``
        directory, so a detached helper waits for this process to exit, then swaps
        the new tree in for the install directory and relaunches.

        The new tree is landed on the install volume HERE, while the app is
        still running, so all the helper has to do is two same-volume directory
        renames. It used to robocopy the whole tree (hundreds of megabytes,
        tens of seconds) at the one moment that is most likely to be a Windows
        shutdown: the app exiting. A shutdown killed the mirror halfway and
        left the install broken with the only good copy stranded at ``.old``,
        unrepaired and unrepairable except by hand. Two renames leave a window
        measured in the gap between them.

        Crash-safe: the helper renames the live install to ``.old`` first (a
        fast same-volume move), renames the new tree into its place, and
        deletes the backup ONLY once the swapped-in folder holds the
        executable. A failed rename, or one that left no executable, restores
        ``.old``, so no outcome leaves the user without a working copy. If even
        the initial backup rename fails, the live install is untouched and
        simply relaunched. The staged tree is checked for the executable before
        the helper is even written, and while the app is still running the
        helper only waits, it never starts a second instance.

        The backup holds the whole OLD install folder, including anything the
        user kept in there that the build never shipped (a download folder
        pointed inside it, a zip extracted over a folder that already held
        other files). Waves never deletes a user's files, so before the backup
        is discarded a robocopy moves back everything the new tree does not
        have (``/XC /XN /XO`` leave only the files missing from the swapped-in
        install), and a failure there keeps the backup folder rather than
        deleting it, marked (see :func:`_mark_kept`) so that neither a later
        update nor a later helper can take that name for a backup slot of its
        own and delete the folder to make room.

        The script itself is pure ASCII and every path reaches it as an
        argument. cmd.exe decodes a .bat in the console's OEM code page, so a
        UTF-8 helper with, say, a Cyrillic account name interpolated into it
        used to decode as mojibake: the first `if not exist` then tested a path
        that cannot exist, the helper concluded there was nothing to apply, and
        every update silently did nothing while the UI said it had worked.
        Arguments arrive through the command line, which is UTF-16 all the way,
        so they are immune to that; the same is true of the `%` a batch file
        would otherwise expand out of a path. Only the app's own paths are
        passed (never the asset name), so there is no command injection.
        The per-OS swap is only fully verifiable against a real packaged build.
        """
        new_exe = new_tree / target.name
        if not new_exe.is_file() or new_exe.stat().st_size == 0:
            raise UpdaterError(f"Refusing to install: the downloaded build has no {target.name}.")
        install_root = target.parent
        staged_same_dev = _spare_sibling(install_root, ".new", target.name)
        log("preparing the swap")
        _rmtree(staged_same_dev)
        try:
            # rename if the staging dir and the install share a volume, copy if
            # they do not; either way the helper's move is same-volume.
            shutil.move(str(new_tree), str(staged_same_dev))
        except OSError:
            _rmtree(staged_same_dev)
            raise
        self._arm_tree_helper(install_root, staged_same_dev, target)
        return target

    def _arm_tree_helper(self, install_root: Path, new_tree: Path, target: Path) -> None:
        """Write and launch the detached helper that performs the tree swap.

        Split out because a staged swap that never happened is re-armed at the
        next launch (see :meth:`resume_pending_apply`); the script is named per
        pid so a re-arm can never overwrite a helper another copy of Waves is
        already executing, which cmd.exe would resume at whatever byte offset
        it had reached.
        """
        pid = os.getpid()
        # The BACKUP is named per pid for the same reason the script is. Two
        # copies of Waves can each have a helper waiting (one arms at install
        # time and exits, the other re-arms at its next launch), and a shared
        # ".old" made them collide on the one folder that must not be touched:
        # the loser wakes inside the window between the winner's
        # move INSTALL -> BACKUP and its move NEWTREE -> INSTALL, when NEWTREE
        # still exists so the "already applied elsewhere" recheck passes, and
        # its next line is an unconditional rmdir of BACKUP. At that instant
        # the backup is the only copy of the user's foreign files, which the
        # winner has not reclaimed yet: exactly the harm F-01 exists to
        # prevent. A per-pid name makes that rmdir reach only this helper's
        # own leftover from an earlier run of the same pid.
        #
        # "The same pid" is where the name stops being an identity, though:
        # Windows recycles pids freely, so a session weeks later can be pid
        # 4128 again and inherit the folder a failed reclaim KEPT because it
        # held the user's files. _spare_sibling refuses a marked folder, so
        # this lands on ".old-4128-1" instead, and the helper checks the same
        # mark before its rmdir for the case where the mark is written between
        # the two (a helper still finishing while the next session arms).
        backup = _spare_sibling(install_root, f".old-{pid}", target.name)
        # robocopy exit codes 0-7 are success (files copied / nothing to do); >=8
        # means a real failure. Each branch is its own label: a cmd
        # `if ... cmd1 & cmd2` binds cmd2 INTO the if, so the former one-line
        # restore chain silently skipped the restore whenever the mirror had
        # created no folder, and the script fell through to deleting the backup
        # (an emptied install in the field). The initial rename is retried: the
        # folder can stay locked for a moment after the process exits (AV scan,
        # straggling handles). Every step logs to update.log so a failed swap in
        # the field is diagnosable.
        cmd = (
            f"@echo off\r\n"
            f'set "INSTALL=%WAVES_UPDATE_1%"\r\n'
            f'set "BACKUP=%WAVES_UPDATE_2%"\r\n'
            f'set "NEWTREE=%WAVES_UPDATE_3%"\r\n'
            f'set "TARGET=%WAVES_UPDATE_4%"\r\n'
            f'set "KEPT=%BACKUP%\\{_KEPT_MARKER}"\r\n'
            f'set "LOG=%~dp0update.log"\r\n'
            f'echo helper start %date% %time% > "%LOG%"\r\n'
            f'if not exist "%NEWTREE%" (echo nothing staged, nothing applied >> "%LOG%" & goto done)\r\n'
            f"set tries=0\r\n"
            f":wait\r\n"
            f"set /a tries+=1\r\n"
            f"if %tries% GTR {self._HELPER_WAIT_TICKS} goto giveup\r\n"
            f'tasklist /FI "PID eq {pid}" 2>nul | find "{pid}" >nul && (ping -n 2 127.0.0.1 >nul & goto wait)\r\n'
            f'echo app exited after %tries% checks >> "%LOG%"\r\n'
            f'if not exist "%NEWTREE%" (echo already applied elsewhere, nothing to do >> "%LOG%" & goto done)\r\n'
            f'if exist "%KEPT%" (echo backup holds files that were not part of Waves, applying nothing >> "%LOG%" & goto relaunch)\r\n'
            f'if exist "%BACKUP%" rmdir /S /Q "%BACKUP%" >> "%LOG%" 2>&1\r\n'
            f"set mtries=0\r\n"
            f":swap\r\n"
            f"set /a mtries+=1\r\n"
            f'move "%INSTALL%" "%BACKUP%" >> "%LOG%" 2>&1 && goto swapin\r\n'
            f"if %mtries% LSS 30 (ping -n 2 127.0.0.1 >nul & goto swap)\r\n"
            f'echo backup rename failed, relaunching old build >> "%LOG%"\r\n'
            f"goto relaunch\r\n"
            f":swapin\r\n"
            f'move "%NEWTREE%" "%INSTALL%" >> "%LOG%" 2>&1 || (echo swap-in failed >> "%LOG%" & goto restore)\r\n'
            f'if not exist "%TARGET%" (echo swap left no {target.name} >> "%LOG%" & goto restore)\r\n'
            f'echo swap ok, reclaiming anything that was not part of Waves >> "%LOG%"\r\n'
            f'robocopy "%BACKUP%" "%INSTALL%" /E /MOVE /XC /XN /XO /XJ /R:1 /W:1 /NFL /NDL /NJH /NJS /NP >> "%LOG%" 2>&1\r\n'
            f'if %ERRORLEVEL% GEQ 8 (echo could not reclaim, keeping the backup folder >> "%LOG%" & echo Waves kept this folder because it holds files that were not part of the app.> "%KEPT%" & goto relaunch)\r\n'
            f'rmdir /S /Q "%BACKUP%" >nul 2>&1\r\n'
            f"goto relaunch\r\n"
            f":restore\r\n"
            f'echo restoring backup >> "%LOG%"\r\n'
            f'if exist "%INSTALL%" rmdir /S /Q "%INSTALL%" >> "%LOG%" 2>&1\r\n'
            f'move "%BACKUP%" "%INSTALL%" >> "%LOG%" 2>&1\r\n'
            f":relaunch\r\n"
            f'if exist "%TARGET%" (start "" "%TARGET%" & echo relaunched >> "%LOG%") else (echo nothing to relaunch >> "%LOG%")\r\n'
            f"goto done\r\n"
            f":giveup\r\n"
            f'echo gave up waiting for pid {pid}, nothing applied >> "%LOG%"\r\n'
            f":done\r\n"
            f'del "%~f0"\r\n'
        )
        helper = self.staging_dir / f"apply_update_{pid}.bat"
        helper.write_text(cmd, encoding="ascii")
        self._spawn_helper(helper, install_root, backup, new_tree, target)

    # How long (in ~1 s ticks) a Windows helper waits for the app to exit before
    # giving up WITHOUT touching the install. The helper is armed at install
    # time, not at "Restart now", so a user who reads on for an hour must still
    # get the swap when they finally restart (the old 150 s cap then launched a
    # second instance of the running app, and the later restart swapped nothing).
    _HELPER_WAIT_TICKS = 4 * 60 * 60

    def _apply_macos(self, staged: Path, target: Path, log) -> Path:
        """Replace the running ``.app`` bundle, or fall back to a single file."""
        if staged.suffix == ".app" or staged.is_dir():
            # sys.executable is …/Waves.app/Contents/MacOS/Waves → bundle root.
            bundle = target
            for parent in target.parents:
                if parent.suffix == ".app":
                    bundle = parent
                    break
            # Cross-device safe (like _apply_unix_tree): the staged bundle usually
            # lives under ~/.config while the install sits in /Applications, a
            # different volume, so land it on the install filesystem first
            # (shutil.move copies across devices), then do the backup + swap as
            # same-device renames, rolling the live bundle back if the swap fails.
            inside = "Contents/MacOS"
            staged_same_dev = _spare_sibling(bundle, ".new", inside)
            _rmtree(staged_same_dev)
            shutil.move(str(staged), str(staged_same_dev))
            backup = _spare_sibling(bundle, ".old", inside)
            _rmtree(backup)
            backed_up = False
            try:
                if bundle.exists():
                    os.replace(bundle, backup)
                    backed_up = True
                os.replace(staged_same_dev, bundle)
            except OSError:
                if backed_up and not bundle.exists() and backup.exists():
                    os.replace(backup, bundle)
                _rmtree(staged_same_dev)
                raise
            # INVARIANT: only ever reached from install() *after* the signature +
            # checksum gate has passed, so quarantine is only stripped off bytes we
            # have authenticated. Do not call _apply before that gate.
            subprocess.run(["xattr", "-dr", "com.apple.quarantine", str(bundle)], capture_output=True, check=False)
            # Same exposure as the tree swap (the old bundle is about to be
            # deleted with anything the user kept inside it), but the answer
            # differs: moving foreign files INTO the new bundle would break its
            # code signature and the app would stop launching. So the backup is
            # kept whole instead, and only a bundle that held nothing but the
            # build is deleted.
            if _foreign_leftovers(backup, bundle, shipped=self._shipped_by_the_old_build()):
                # Protected first: the folder is the user's from here on, and
                # the next update's backup slot must not be allowed to land on
                # it (_spare_sibling would otherwise hand out this very name,
                # and its caller's next statement is a recursive delete). The
                # name is read back off the result, because protecting it can
                # mean renaming it, and the user is told where it IS.
                backup, protected = _mark_kept(backup)
                # Recorded, not only logged. The log line is a transient status
                # the "Updated to vX. Restart to finish." message overwrites a
                # moment later, so a user could accumulate a whole extra copy
                # of the app per update (hundreds of megabytes) and never be
                # told it existed. The NAME only: the path is under the user's
                # home and never goes on screen or into a log.
                self.kept_backup = backup.name
                self.kept_unprotected = not protected
                log(f"kept files that were not part of Waves in {backup.name}")
                if not protected:
                    log(f"move them out of {backup.name} before updating again; Waves could not reserve it")
            else:
                _rmtree(backup)
            return bundle
        return self._apply_unix(staged, target, log)

    def _apply_windows(self, staged: Path, target: Path, log) -> Path:
        """A running ``.exe`` can't overwrite itself: stage beside it and hand a
        detached cmd helper the job of swapping once this process exits.

        The helper first backs the live exe up to ``.old`` and, if any move
        fails (locked file, full disk, denied permission), restores the backup
        and relaunches it, so a failed update always leaves the user on the
        working old build, never on a missing or half-written one.

        Pure ASCII, with every path passed as an argument, for the reason given
        on :meth:`_apply_windows_tree`: cmd.exe decodes a .bat in the OEM code
        page, so an interpolated non-ASCII path decodes as mojibake and the
        swap silently never happens.
        """
        new = target.with_suffix(target.suffix + ".new")
        if new.exists():
            new.unlink()
        try:
            os.replace(staged, new)
        except OSError as exc:
            if exc.errno != errno.EXDEV:
                raise
            # Staging lives under the app data dir, the install can sit on
            # another drive, and MoveFileExW without COPY_ALLOWED refuses that.
            # Copy across the boundary here (the app is still running, so this
            # is the safe moment); the helper's own move is then a same-volume
            # rename, exactly as the three sibling apply paths already are.
            shutil.copy2(staged, new)
            staged.unlink(missing_ok=True)
        self._arm_exe_helper(target, new)
        return target

    def _arm_exe_helper(self, target: Path, new: Path) -> None:
        """Write and launch the detached helper that swaps a single ``.exe``.

        Split out for the reason :meth:`_arm_tree_helper` is: the helper waits
        on the process that armed it, and a session that ends in a shutdown
        (rather than a quit) never wakes it, so the next launch has to be able
        to arm a fresh one over the same staged file. Without that split the
        single-file layout staged an update, told the user it had installed,
        and then had its marker cleared at the next boot as "nothing left to
        apply", because only the tree layout could be resumed.
        """
        pid = os.getpid()
        # Per pid, like the tree helper's backup and for the same reason: two
        # copies of Waves can each have a helper waiting, and a shared ".old"
        # let the loser delete the winner's only copy of the old build.
        backup = target.with_suffix(target.suffix + f".old-{pid}")
        # Wait for our PID to vanish, back up the old exe, move the new one in,
        # relaunch. If backing up fails, the old exe is untouched → just relaunch
        # it. If the new-in move fails, restore the backup before relaunching, so
        # ``target`` is never left missing. The backup move is retried while the
        # exe stays briefly locked after exit; every step logs to update.log.
        cmd = (
            f"@echo off\r\n"
            f'set "TARGET=%WAVES_UPDATE_1%"\r\n'
            f'set "BACKUP=%WAVES_UPDATE_2%"\r\n'
            f'set "NEWEXE=%WAVES_UPDATE_3%"\r\n'
            f'set "LOG=%~dp0update.log"\r\n'
            f'echo helper start %date% %time% > "%LOG%"\r\n'
            f"set tries=0\r\n"
            f":wait\r\n"
            f"set /a tries+=1\r\n"
            f'if %tries% GTR {self._HELPER_WAIT_TICKS} (echo gave up waiting for pid {pid}, nothing applied >> "%LOG%" & del "%~f0" & exit /b 1)\r\n'
            f'tasklist /FI "PID eq {pid}" 2>nul | find "{pid}" >nul && (ping -n 2 127.0.0.1 >nul & goto wait)\r\n'
            f'echo app exited after %tries% checks >> "%LOG%"\r\n'
            # The same post-wait recheck the tree helper does, which this half
            # never had: another copy's helper may have applied the very same
            # staged build while this one was waiting, and without the look
            # this helper would back the NEW exe up and then fail to move a
            # file that is no longer there.
            f'if not exist "%NEWEXE%" (echo already applied elsewhere, nothing to do >> "%LOG%" & start "" "%TARGET%" & del "%~f0" & exit /b 0)\r\n'
            f'if exist "%BACKUP%" del /F /Q "%BACKUP%" >nul 2>&1\r\n'
            f"set mtries=0\r\n"
            f":swap\r\n"
            f"set /a mtries+=1\r\n"
            f'move /Y "%TARGET%" "%BACKUP%" >> "%LOG%" 2>&1 && goto newin\r\n'
            f"if %mtries% LSS 30 (ping -n 2 127.0.0.1 >nul & goto swap)\r\n"
            f'echo backup move failed, relaunching old build >> "%LOG%"\r\n'
            f'start "" "%TARGET%" & del "%~f0" & exit /b 1\r\n'
            f":newin\r\n"
            f'move /Y "%NEWEXE%" "%TARGET%" >> "%LOG%" 2>&1 || (echo new-in move failed, restoring >> "%LOG%" & move /Y "%BACKUP%" "%TARGET%" >nul & start "" "%TARGET%" & del "%~f0" & exit /b 1)\r\n'
            f'del /F /Q "%BACKUP%" >nul 2>&1\r\n'
            f'start "" "%TARGET%"\r\n'
            f'echo relaunched >> "%LOG%"\r\n'
            f'del "%~f0"\r\n'
        )
        helper = self.staging_dir / f"apply_update_{pid}.bat"
        helper.write_text(cmd, encoding="ascii")
        self._spawn_helper(helper, target, backup, new)

    #: Environment variable per helper path, read once at the top of the script
    #: into its own local. See :meth:`_spawn_helper` for why not a command line.
    _HELPER_ENV_PREFIX = "WAVES_UPDATE_"

    def _spawn_helper(self, helper: Path, *args: str | os.PathLike) -> None:
        """Launch the detached swap helper, handing it its paths in the
        environment.

        CREATE_NO_WINDOW (0x08000000) gives the helper cmd a hidden console:
        with DETACHED_PROCESS it had no console at all and the batch never
        executed (tasklist/find/start are console programs), which left updates
        downloaded but never applied. The working directory is pinned to the
        staging dir so the helper's cwd can never hold a lock inside the
        install folder it has to rename, and the script is named by its bare
        (ASCII, space-free) filename relative to that cwd, which keeps the
        command line from starting with a quote: cmd's /c quote-stripping rule
        would otherwise rewrite it.

        The paths do not travel on the command line at all. Quoting them there
        made an ``&`` ("Rock & Roll") safe, but quotes do not stop cmd's OWN
        percent expansion: a folder named with a matched pair of an existing
        variable (``%TEMP% mixes``, legal on Windows) was rewritten before
        ``%~1`` could capture it, and the helper then found nothing staged and
        applied nothing. An environment value is substituted once where the
        script reads it and is never rescanned, so every character in a path,
        percent, ampersand, caret and bang alike, arrives as data.
        """
        env = dict(os.environ)
        for i, value in enumerate((str(x) for x in args), start=1):
            env[f"{self._HELPER_ENV_PREFIX}{i}"] = value
        subprocess.Popen(
            f"cmd /c {helper.name}",
            cwd=str(self.staging_dir),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            creationflags=0x08000000,  # CREATE_NO_WINDOW
        )

    def relaunch(self) -> None:
        """Restart the application from the (now-updated) executable.

        On Windows the detached helper already handles relaunch, so the caller
        simply exits; elsewhere we exec the new binary in place.
        """
        if self.os_key == "windows":
            return
        # From an AppImage, exec the (freshly swapped) .AppImage file itself:
        # _current_exe points into the old read-only mount, which unmounts the
        # moment this process exits.
        exe = _running_appimage() or str(_current_exe())
        try:
            if self.os_key == "macos" and ".app/" in exe:
                bundle = exe.split(".app/")[0] + ".app"
                subprocess.Popen(["open", "-n", bundle], close_fds=True)
                return
            os.execv(exe, [exe, *sys.argv[1:]])
        except Exception:
            logger.exception("relaunch failed")


def _tree_paths(root: Path) -> list[str]:
    """Every path under ``root``, relative and posix-spelled, symlinks not followed.

    Directories are listed alongside files, with a trailing ``/``: a folder a
    later release drops may hold both the old build's files and something the
    user put in there, so the leftovers walk has to be able to descend into it
    rather than judge it whole (see :func:`_foreign_leftovers`). The slash is
    what keeps the two kinds apart, and that matters in exactly one direction:
    a user who replaced a folder the build shipped with a file of their own
    must not have that file read as the folder we are entitled to delete.

    A symlink to a directory is recorded like a file: it is a leaf here (the
    walk never follows it) and a leaf to the leftovers walk too.
    """
    if not root.is_dir():
        return []
    out: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        base = Path(dirpath).relative_to(root)
        for name in dirnames:
            slash = "" if os.path.islink(os.path.join(dirpath, name)) else "/"
            out.append((base / name).as_posix() + slash)
        out.extend((base / name).as_posix() for name in filenames)
    return out


#: Dropped into a backup that is being KEPT because it holds things the build
#: never shipped. It says the folder is the user's now, not a leftover of ours:
#: no later run may claim the name to back up into, and no swap helper may
#: clear it. It lives INSIDE the folder it protects so it cannot outlive it,
#: and it holds a plain sentence so a user who finds it understands it.
_KEPT_MARKER = ".waves-kept-your-files"

#: The fallback protection, for when the mark cannot be written. _spare_sibling
#: only ever produces ``base + suffix`` and ``base + suffix + "-N"``, so a
#: folder whose name ends in this is outside every spelling it can generate,
#: with no file inside it and no free space needed to get there.
_KEPT_SUFFIX = ".your-files"


def _mark_kept(backup: Path) -> tuple[Path, bool]:
    """Make a kept backup unclaimable. Returns where it ended up, and whether it worked.

    The keep is the whole protection: the folder holds files that were not part
    of the build, i.e. the user's, and a music library is not recoverable. But
    the name is one _spare_sibling hands out again the moment the next update
    needs a backup slot, and that call site's next statement deletes what it
    got. The mark is what makes a kept backup unclaimable, here and in the
    Windows swap helper, which checks for the same file before its own rmdir.

    So a mark that could not be written leaves the folder exactly as exposed as
    it was before any of this existed, and the callers have already promised
    the user their files are in there. The likely failure is the one the keep
    itself makes likely: the macOS swap holds two whole bundles on the volume
    at once, so a full disk is precisely where the 150-byte marker write dies.
    A same-directory rename needs no space and is atomic, and it moves the
    folder out of the set of names _spare_sibling can produce at all, so it is
    the stronger protection rather than a consolation. Only if even that fails
    does this return ``False``, and then the caller has to say so: silence
    would mean telling the user their files are safe in a folder the next
    update will delete.
    """
    try:
        (backup / _KEPT_MARKER).write_text(
            "Waves kept this folder because it holds files that were not part of the app.\n"
            "Move anything you want out of it, then delete it; Waves will not touch it.\n",
            encoding="utf-8",
        )
    except OSError:
        # No path in the log line: it is under the user's home.
        logger.warning("update: could not mark the kept backup, renaming it out of reach instead")
    else:
        return backup, True
    for n in range(12):
        cand = backup.with_name(backup.name + _KEPT_SUFFIX + ("" if n == 0 else f"-{n}"))
        if cand.exists() or cand.is_symlink():
            continue
        try:
            os.replace(backup, cand)  # same parent, so no ".." rewrite and no copy
        except OSError:
            break
        return cand, True
    logger.warning("update: could not protect the kept backup; the user has to move those files themselves")
    return backup, False


def _spare_sibling(base: Path, suffix: str, ours: str) -> Path:
    """A sibling of ``base`` named ``base.name + suffix`` that we may clear.

    The swap stages the new tree at ``Waves.new`` and backs the live install up
    to ``Waves.old``, wiping whatever sits at those names first. Waves never
    deletes a user's files, and nothing says a folder called ``Waves.old`` next
    to the install is ours, so a name is only taken when it is free or when it
    holds ``ours`` (the executable, so it is plainly a tree we staged or backed
    up on an earlier run). Otherwise the next spelling is tried.

    A backup an earlier update KEPT is the exception to the second half, and
    the reason for the marker check: it holds ``ours`` too (the old build never
    left it), so without this the next update would pick the very folder the
    last one told the user it had kept their files in, and its next statement
    is an unconditional recursive delete.
    """
    for n in range(12):
        cand = base.with_name(base.name + suffix + ("" if n == 0 else f"-{n}"))
        if not cand.exists() and not cand.is_symlink():
            return cand
        if cand.is_dir() and not cand.is_symlink() and (cand / ours).exists() and not (cand / _KEPT_MARKER).exists():
            return cand
    raise UpdaterError(f"Could not find a free name beside {base.name} to stage the update in.")


def _foreign_leftovers(
    backup: Path, installed: Path, _rel: Path | None = None, shipped: set[str] | None = None
) -> list[Path]:
    """Paths (relative to ``backup``) the freshly installed tree has no counterpart for.

    A whole directory that the new tree does not have is reported as ONE entry
    and not descended into, so a download folder someone pointed at the install
    directory comes back as a single move rather than ten thousand.

    ``shipped`` is the file list of the build being replaced, on the updates
    where the previous one recorded it (see
    :meth:`AppUpdater._record_shipped`). Without it this is a bare
    set-difference, and a file the new release legitimately DROPPED (a Qt
    library the trim sweep sheds, a dependency replaced by a shim, a bundled
    folder that moved) reads exactly like a file the user put in the install
    folder: it is then kept or carried into the fresh install for good. A path
    the old build shipped is the release's business, not the user's, so it is
    not reported. A DIRECTORY the old build shipped is descended into rather
    than judged whole: the user may have put something inside it, and only what
    the old build did not ship in there is theirs.
    """
    rel = _rel if _rel is not None else Path()
    found: list[Path] = []
    try:
        entries = sorted((backup / rel).iterdir())
    except OSError:
        return found
    for entry in entries:
        child = rel / entry.name
        mirror = installed / child
        walkable = entry.is_dir() and not entry.is_symlink()
        # The old build's spelling of this path, "dir/" or "file" (see
        # _tree_paths): matching on the wrong kind is how a user's file that
        # took a build folder's name would end up counted as ours.
        was_ours = shipped is not None and (child.as_posix() + ("/" if walkable else "")) in shipped
        if mirror.exists() or mirror.is_symlink():
            if walkable and mirror.is_dir() and not mirror.is_symlink():
                found.extend(_foreign_leftovers(backup, installed, child, shipped))
        elif was_ours:
            if walkable:  # the old build's folder, which the user may have added to
                found.extend(_foreign_leftovers(backup, installed, child, shipped))
        else:
            found.append(child)
    return found


def _reclaim_foreign(backup: Path, installed: Path, log, shipped: set[str] | None = None) -> tuple[str, bool]:
    """Carry anything that was not part of the build out of ``backup``, then drop it.

    Returns ``(name of the folder kept for the user, whether it is protected)``,
    or ``("", True)`` when nothing was kept. The caller records the name: a keep
    here used to say so only through ``log``, a passing status the restart
    message overwrites a moment later, so on Linux a whole kept copy of the
    install could pile up per update with nothing on screen ever naming it.

    The swap replaces the WHOLE install directory, so every file in there that
    the build did not ship (a download folder pointed inside it, a zip
    extracted over a folder that already held other things) is sitting in the
    backup with a recursive delete pointed at it. Waves never deletes a user's
    files, so they are moved into the new install first, and if even one of
    them cannot be moved the backup is KEPT instead of deleted: a stale folder
    is recoverable, a deleted music library is not.

    A path the new build also ships keeps the new build's file; that case is
    indistinguishable from a leftover of the old build, and the install folder
    belongs to the app. A path the OLD build shipped and the new one dropped is
    the release's own business and is left in the backup to be deleted with it,
    which is what ``shipped`` (when a previous update recorded one) is for:
    without it every dropped build file was carried back into the fresh install
    and then stayed there, release after release.
    """
    leftovers = _foreign_leftovers(backup, installed, shipped=shipped)
    if not leftovers:
        _rmtree(backup)
        return "", True
    log(f"keeping {len(leftovers)} item(s) in the install folder that were not part of Waves")
    stuck = 0
    for rel in leftovers:
        dest = installed / rel
        if dest.exists() or dest.is_symlink():
            stuck += 1  # the new build claimed the path meanwhile; leave the old copy be
            continue
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(backup / rel), str(dest))
        except OSError:
            stuck += 1
            # A name out of the user's own folder is user content (it is very
            # often an album or artist folder), so it is marked for the
            # export's "also hide titles and searches" pass.
            from . import diagnostics

            logger.warning("update: could not put %s back into the install folder", diagnostics.content(rel.name))
    if stuck:
        # Protected before the message names it: from here on the folder is the
        # user's, and the next update's backup slot has to go somewhere else.
        # Protecting it can rename it, so the message names what came back.
        backup, protected = _mark_kept(backup)
        log(f"could not move {stuck} of them back; they are still in {backup.name}")
        if not protected:
            log(f"move them out of {backup.name} before updating again; Waves could not reserve it")
        return backup.name, protected
    _rmtree(backup)
    return "", True


def _rmtree(path: Path) -> None:
    import shutil

    try:
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
        elif path.exists():
            path.unlink()
    except Exception:
        logger.debug("could not remove %s", path, exc_info=True)


def _chmod_exec(path: Path) -> None:
    """Make ``path`` user-rwx + group/other r-x (no-op effect on Windows)."""
    path.chmod(path.stat().st_mode | stat.S_IRWXU | stat.S_IRGRP | stat.S_IXGRP | stat.S_IROTH | stat.S_IXOTH)
