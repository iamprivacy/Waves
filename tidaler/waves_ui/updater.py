"""In-app self-updater for Waves.

Waves ships as a packaged single-file binary built by CI and published to a
public GitHub repo's Releases. This module lets the app notice a newer release,
download the build for the current OS/arch, verify it against its ``.sha256``
sidecar, and swap it in atomically, the same shape as the FFmpeg manager
(:mod:`tidaler.waves_ui.ffmpeg_manager`), which this deliberately mirrors.

Nothing here touches Qt, so it is pure and unit-testable; the Qt slots/signals
that drive the Settings UI live in :mod:`tidaler.waves_ui.backend`.

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
from threading import Event

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

    Deliberately NOT ``tidaler.is_dev_env()``: an editable/pip install is
    importlib-discoverable, so that helper would report a from-source run as
    non-dev. Only a genuine frozen build may self-install.
    """
    return bool(getattr(sys, "frozen", False)) or "__compiled__" in globals()


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


def _select_asset(assets: list[dict], os_key: str, arch: str) -> tuple[str, str, str | None]:
    """Pick the best release asset for ``os_key``/``arch``.

    Returns ``(name, download_url, sha256_url)``, empty strings / ``None`` when
    nothing matches. Scores OS match (required), arch match (preferred), and an
    installable extension over sidecars, so it is robust to however CI names the
    files. The ``.sha256`` sidecar is paired by filename when present.
    """
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

        ``state`` ∈ {``not_configured``, ``source``, ``ready``}: ``ready`` means
        a frozen, configured build that can self-install; ``source`` can still
        *check* but not install; ``not_configured`` is fully dormant.
        """
        frozen = is_frozen()
        configured = self.is_configured()
        if not configured:
            state = "not_configured"
        elif not frozen:
            state = "source"
        else:
            state = "ready"
        return {
            "state": state,
            "configured": configured,
            "frozen": frozen,
            "can_self_install": configured and frozen,
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
        name, asset_url, sha_url = _select_asset(assets, self.os_key, self.arch)
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

        sess = session or _session()
        if release is None:
            _log("resolving latest release")
            release = self.latest(sess)
        if release is None or not release.url:
            raise UpdaterError(f"No Waves build is available for {self.os_key}/{self.arch}.")

        self.staging_dir.mkdir(parents=True, exist_ok=True)
        _check_abort()

        _log(f"downloading {release.version} ({release.asset})")
        # release.asset comes from the (untrusted, pre-verification) release JSON, so
        # strip any path component before it reaches the filesystem as a temp suffix.
        suffix = os.path.basename((release.asset or "dl").replace("\\", "/")) or "dl"
        with tempfile.NamedTemporaryFile(dir=self.staging_dir, suffix="-" + suffix, delete=False) as tmp:
            payload = Path(tmp.name)
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
            applied_to = self._apply(payload, release, _log)
        finally:
            payload.unlink(missing_ok=True)

        self._write_manifest(release)
        _log(f"installed {release.version}")
        return {"ok": True, "version": release.version, "applied_to": str(applied_to), "relaunch": True}

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

    # --- platform swap --------------------------------------------------- #
    # These run only from a frozen, configured build (guarded in install()), so
    # they execute exactly where they can be hardened against the real CI
    # artifacts. Each extracts the payload (zip or raw) and swaps it next to the
    # running executable; Windows defers the swap to a helper because a running
    # .exe can't overwrite itself.
    def _apply(self, payload: Path, release: Release, log) -> Path:
        target = _current_exe()
        staged = self._extract_payload(payload, release.asset, log)
        if self.os_key == "macos":
            return self._apply_macos(staged, target, log)
        # Nuitka --standalone ships a multi-file directory (the .dist tree). When the
        # asset extracted to a nested directory, swap the WHOLE tree, replacing only
        # the executable would leave the new binary running against the old bundled
        # Qt/Python libraries. A genuine single-file build lands directly in the
        # staging root and is swapped as one file.
        staging_root = (self.staging_dir / "staged").resolve()
        is_tree = staged.is_file() and staged.parent.resolve() != staging_root
        if self.os_key == "windows":
            return (
                self._apply_windows_tree(staged.parent, target, log)
                if is_tree
                else self._apply_windows(staged, target, log)
            )
        return self._apply_unix_tree(staged.parent, target, log) if is_tree else self._apply_unix(staged, target, log)

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
        staged_same_dev = install_root.with_name(install_root.name + ".new")
        _rmtree(staged_same_dev)
        shutil.move(str(new_tree), str(staged_same_dev))  # rename if same-dev, copy if cross-dev
        # 2. Back up the live install, then swap the new tree in (both same-device).
        backup = install_root.with_name(install_root.name + ".old")
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
        _rmtree(backup)
        return target

    def _apply_windows_tree(self, new_tree: Path, target: Path, log) -> Path:
        """Windows: the running ``.exe`` and its loaded DLLs lock the whole ``.dist``
        directory, so a detached helper waits for this process to exit, then swaps
        the new tree in for the install directory and relaunches.

        Crash-safe: the helper first renames the live install to ``.old`` (a fast
        same-volume move), mirrors the new tree into place, and, if the mirror
        fails, deletes the partial copy and restores ``.old`` before relaunching,
        so a failed/partial update never leaves a broken install tree. If even the
        initial backup rename fails, the live install is untouched and is simply
        relaunched.

        Only the app's own paths are interpolated (never the asset name), so there
        is no command injection. The per-OS swap is only fully verifiable against a
        real packaged build.
        """
        install_root = target.parent
        backup = install_root.with_name(install_root.name + ".old")
        pid = os.getpid()
        log_file = self.staging_dir / "update.log"
        # robocopy exit codes 0–7 are success (files copied / nothing to do); >=8
        # means a real failure. Back up first, and only relaunch the new build when
        # the mirror succeeded, otherwise restore the backup and relaunch that, so
        # we never start the app against a half-mirrored, broken install tree.
        # The initial rename is retried: the folder can stay locked for a moment
        # after the process exits (AV scan, straggling handles). Every step logs
        # to update.log so a failed swap in the field is diagnosable.
        cmd = (
            f"@echo off\r\n"
            f'echo helper start %date% %time% > "{log_file}"\r\n'
            f"set tries=0\r\n"
            f":wait\r\n"
            f"set /a tries+=1\r\n"
            f'if %tries% GTR 150 (echo gave up waiting for pid {pid} >> "{log_file}" & start "" "{target}" & del "%~f0" & exit /b 1)\r\n'
            f'tasklist /FI "PID eq {pid}" 2>nul | find "{pid}" >nul && (ping -n 2 127.0.0.1 >nul & goto wait)\r\n'
            f'echo app exited after %tries% checks >> "{log_file}"\r\n'
            f'if exist "{backup}" rmdir /S /Q "{backup}" >> "{log_file}" 2>&1\r\n'
            f"set mtries=0\r\n"
            f":swap\r\n"
            f"set /a mtries+=1\r\n"
            f'move "{install_root}" "{backup}" >> "{log_file}" 2>&1 && goto mirror\r\n'
            f"if %mtries% LSS 30 (ping -n 2 127.0.0.1 >nul & goto swap)\r\n"
            f'echo backup rename failed, relaunching old build >> "{log_file}"\r\n'
            f'start "" "{target}" & del "%~f0" & exit /b 1\r\n'
            f":mirror\r\n"
            f'robocopy "{new_tree}" "{install_root}" /MIR /MOVE >> "{log_file}" 2>&1\r\n'
            f'if %ERRORLEVEL% GEQ 8 (echo robocopy failed %ERRORLEVEL%, restoring backup >> "{log_file}" & if exist "{install_root}" rmdir /S /Q "{install_root}" & move "{backup}" "{install_root}" >nul & start "" "{target}" & del "%~f0" & exit /b 1)\r\n'
            f'echo mirror ok, cleaning up >> "{log_file}"\r\n'
            f'rmdir /S /Q "{backup}" >nul 2>&1\r\n'
            f'start "" "{target}"\r\n'
            f'echo relaunched >> "{log_file}"\r\n'
            f'del "%~f0"\r\n'
        )
        helper = self.staging_dir / "apply_update.bat"
        helper.write_text(cmd, encoding="utf-8")
        self._spawn_helper(helper)
        return target

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
            staged_same_dev = bundle.with_name(bundle.name + ".new")
            _rmtree(staged_same_dev)
            shutil.move(str(staged), str(staged_same_dev))
            backup = bundle.with_suffix(".app.old")
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
        """
        new = target.with_suffix(target.suffix + ".new")
        if new.exists():
            new.unlink()
        os.replace(staged, new)
        backup = target.with_suffix(target.suffix + ".old")
        pid = os.getpid()
        log_file = self.staging_dir / "update.log"
        # Wait for our PID to vanish, back up the old exe, move the new one in,
        # relaunch. If backing up fails, the old exe is untouched → just relaunch
        # it. If the new-in move fails, restore the backup before relaunching, so
        # ``target`` is never left missing. The backup move is retried while the
        # exe stays briefly locked after exit; every step logs to update.log.
        cmd = (
            f"@echo off\r\n"
            f'echo helper start %date% %time% > "{log_file}"\r\n'
            f"set tries=0\r\n"
            f":wait\r\n"
            f"set /a tries+=1\r\n"
            f'if %tries% GTR 150 (echo gave up waiting for pid {pid} >> "{log_file}" & start "" "{target}" & del "%~f0" & exit /b 1)\r\n'
            f'tasklist /FI "PID eq {pid}" 2>nul | find "{pid}" >nul && (ping -n 2 127.0.0.1 >nul & goto wait)\r\n'
            f'echo app exited after %tries% checks >> "{log_file}"\r\n'
            f'if exist "{backup}" del /F /Q "{backup}" >nul 2>&1\r\n'
            f"set mtries=0\r\n"
            f":swap\r\n"
            f"set /a mtries+=1\r\n"
            f'move /Y "{target}" "{backup}" >> "{log_file}" 2>&1 && goto newin\r\n'
            f"if %mtries% LSS 30 (ping -n 2 127.0.0.1 >nul & goto swap)\r\n"
            f'echo backup move failed, relaunching old build >> "{log_file}"\r\n'
            f'start "" "{target}" & del "%~f0" & exit /b 1\r\n'
            f":newin\r\n"
            f'move /Y "{new}" "{target}" >> "{log_file}" 2>&1 || (echo new-in move failed, restoring >> "{log_file}" & move /Y "{backup}" "{target}" >nul & start "" "{target}" & del "%~f0" & exit /b 1)\r\n'
            f'del /F /Q "{backup}" >nul 2>&1\r\n'
            f'start "" "{target}"\r\n'
            f'echo relaunched >> "{log_file}"\r\n'
            f'del "%~f0"\r\n'
        )
        helper = self.staging_dir / "apply_update.bat"
        helper.write_text(cmd, encoding="utf-8")
        self._spawn_helper(helper)
        return target

    def _spawn_helper(self, helper: Path) -> None:
        """Launch the detached swap helper.

        CREATE_NO_WINDOW (0x08000000) gives the helper cmd a hidden console:
        with DETACHED_PROCESS it had no console at all and the batch never
        executed (tasklist/find/start are console programs), which left updates
        downloaded but never applied. The working directory is pinned to the
        staging dir so the helper's cwd can never hold a lock inside the
        install folder it has to rename.
        """
        subprocess.Popen(
            ["cmd", "/c", str(helper)],
            cwd=str(self.staging_dir),
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
        exe = str(_current_exe())
        try:
            if self.os_key == "macos" and ".app/" in exe:
                bundle = exe.split(".app/")[0] + ".app"
                subprocess.Popen(["open", "-n", bundle], close_fds=True)
                return
            os.execv(exe, [exe, *sys.argv[1:]])
        except Exception:
            logger.exception("relaunch failed")


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
