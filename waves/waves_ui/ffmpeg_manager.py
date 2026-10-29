"""In-app FFmpeg manager for Waves.

Waves needs FFmpeg for three optional post-processing steps in
``waves.download``, converting videos to MP4, extracting FLAC out of MP4
containers, and downsampling hi-res audio. Rather than make the user install
FFmpeg and point the app at the binary, this module downloads a trusted static
build on demand into the app's own data folder, verifies it, and can tell when a
newer build is available.

Nothing here touches Qt, so it is pure and unit-testable; the Qt slots/signals
that drive the Settings UI live in :mod:`waves.waves_ui.backend`.

Sources (native build per CPU arch, chosen for trust + automatability):

* **macOS / Linux** → ``ffmpeg.martin-riedl.de``, native per-arch builds (real
  Apple-Silicon arm64, not Rosetta); the macOS builds are published signed +
  notarized, and on macOS we *verify* that signature (``codesign --verify``)
  before trusting the download rather than assuming it. Clean URLs
  ``/download/{os}/{arch}/{version}/ffmpeg.zip`` each with a ``.sha256`` sidecar.
  ``{version}`` is ``<epoch>_<label>`` so "is there a newer build?" is an integer
  compare on the epoch. (GPL build, we never redistribute it; the app merely
  invokes a separate program the user downloaded.)
* **Windows** → ``BtbN/FFmpeg-Builds`` GitHub releases (martin-riedl has no
  Windows builds). LGPL ``win64``/``winarm64`` zip, verified against the
  release's ``checksums.sha256``.

We never ship the binary ourselves, so Waves carries no FFmpeg redistribution or
licensing obligation.

A note on trust: the ``.sha256`` sidecar comes from the same origin as the zip,
so it proves integrity (no corruption in transit), not authenticity, a
compromised origin could publish a matching hash. On macOS we additionally
verify the platform code signature; on other platforms HTTPS to a trusted origin
plus the smoke test is the guarantee, and we do not claim more.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import platform
import re
import stat
import subprocess
import tempfile
import time
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path
from threading import Event

import requests

from waves.waves_ui import proc

logger = logging.getLogger("waves")

_TIMEOUT = 30
_CHUNK = 1 << 16  # 64 KiB streaming chunks
_MR_BASE = "https://ffmpeg.martin-riedl.de"
_BTBN_LATEST = "https://api.github.com/repos/BtbN/FFmpeg-Builds/releases/tags/latest"
_UA = "Waves-ffmpeg-manager"


class FfmpegUnsupportedPlatform(Exception):
    """Raised when the current OS/arch has no configured download source."""


class FfmpegCancelled(Exception):
    """Raised when an install is aborted via its :class:`~threading.Event`."""


@dataclass(frozen=True)
class Release:
    """A downloadable FFmpeg build resolved from a source."""

    source: str  # "martin-riedl" | "btbn"
    version: str  # opaque id used for update comparison (e.g. "1778761665_8.1.1")
    label: str  # human-friendly version (e.g. "8.1.1" or "win64-2026-06-27")
    url: str
    sha256_url: str | None = None
    sha256: str | None = None  # inline expected hash, when known up front
    sha256_name: str | None = None  # asset basename to select from a multi-line manifest


def target() -> tuple[str, str]:
    """Return ``(os_key, arch_key)`` for the running machine.

    ``os_key`` ∈ {macos, linux, windows}; ``arch_key`` ∈ {amd64, arm64}.
    """
    system = platform.system()
    machine = platform.machine().lower()
    is_arm = "arm" in machine or "aarch64" in machine
    arch = "arm64" if is_arm else "amd64"
    if system == "Darwin":
        return "macos", arch
    if system == "Linux":
        return "linux", arch
    if system == "Windows":
        return "windows", arch
    raise FfmpegUnsupportedPlatform(f"No FFmpeg source for {system}/{machine}")


def _exe_name(os_key: str) -> str:
    return "ffmpeg.exe" if os_key == "windows" else "ffmpeg"


# --------------------------------------------------------------------------- #
# Source: ffmpeg.martin-riedl.de  (macOS + Linux)
# --------------------------------------------------------------------------- #
_MR_LINK = re.compile(r"/download/(macos|linux)/(amd64|arm64)/(\d+_[A-Za-z0-9.\-]+)/ffmpeg\.zip")


def _mr_parse(html: str, os_key: str, arch: str) -> Release | None:
    """Pick the newest build for ``os_key/arch`` from the martin-riedl index.

    Prefers a tagged **release** (label like ``8.1.1``) over a **snapshot**
    (label like ``N-125070-g…``); within a kind, highest epoch wins.
    """
    best_release: tuple[int, str] | None = None
    best_snapshot: tuple[int, str] | None = None
    for m_os, m_arch, segment in _MR_LINK.findall(html):
        if m_os != os_key or m_arch != arch:
            continue
        epoch_s, _, label = segment.partition("_")
        try:
            epoch = int(epoch_s)
        except ValueError:
            continue
        is_snapshot = label.startswith("N-")
        bucket = best_snapshot if is_snapshot else best_release
        if bucket is None or epoch > bucket[0]:
            if is_snapshot:
                best_snapshot = (epoch, segment)
            else:
                best_release = (epoch, segment)
    chosen = best_release or best_snapshot
    if chosen is None:
        return None
    segment = chosen[1]
    base = f"{_MR_BASE}/download/{os_key}/{arch}/{segment}/ffmpeg.zip"
    return Release(
        source="martin-riedl",
        version=segment,
        label=segment.partition("_")[2],
        url=base,
        sha256_url=base + ".sha256",
    )


def _mr_latest(session: requests.Session, os_key: str, arch: str) -> Release | None:
    resp = session.get(_MR_BASE + "/", timeout=_TIMEOUT)
    resp.raise_for_status()
    return _mr_parse(resp.text, os_key, arch)


# --------------------------------------------------------------------------- #
# Source: BtbN/FFmpeg-Builds  (Windows)
# --------------------------------------------------------------------------- #
def _btbn_asset_name(arch: str) -> str:
    return f"ffmpeg-master-latest-{'winarm64' if arch == 'arm64' else 'win64'}-lgpl.zip"


def _btbn_latest(session: requests.Session, arch: str) -> Release | None:
    resp = session.get(_BTBN_LATEST, timeout=_TIMEOUT, headers={"Accept": "application/vnd.github+json"})
    resp.raise_for_status()
    data = resp.json()
    want = _btbn_asset_name(arch)
    assets = {a.get("name"): a.get("browser_download_url") for a in data.get("assets", [])}
    url = assets.get(want)
    if not url:
        return None
    # BtbN publishes ONE combined manifest (checksums.sha256), not per-asset
    # sidecars; _fetch_sha256 selects our asset's line from it via sha256_name.
    sha_url = assets.get("checksums.sha256")
    # The asset name is always "…-latest-…"; use the release timestamp as the
    # version id so update detection notices a new daily build.
    version = f"{want}@{data.get('published_at') or data.get('created_at') or ''}"
    return Release(
        source="btbn",
        version=version,
        label=f"{want.split('-lgpl')[0].replace('ffmpeg-master-latest-', '')} ({(data.get('published_at') or '')[:10]})",
        url=url,
        sha256_url=sha_url,
        sha256_name=want,
    )


def latest(os_key: str, arch: str, session: requests.Session | None = None) -> Release | None:
    """Resolve the newest build for the target, or ``None`` if unavailable.

    Network errors propagate to the caller, which treats an update check as
    best-effort.
    """
    sess = session or _session()
    if os_key in ("macos", "linux"):
        return _mr_latest(sess, os_key, arch)
    if os_key == "windows":
        return _btbn_latest(sess, arch)
    raise FfmpegUnsupportedPlatform(os_key)


def _session() -> requests.Session:
    sess = requests.Session()
    sess.headers["User-Agent"] = _UA
    return sess


# Attribution for the FFmpeg build source, surfaced in Settings. We only ever
# show the entry for the *current* platform so users aren't confused by sources
# that don't apply to their machine. (Mirror any change here into the README's
# acknowledgements, see the project memory note on FFmpeg sources.)
_SOURCE_INFO = {
    "macos": {"name": "ffmpeg.martin-riedl.de", "url": "https://ffmpeg.martin-riedl.de", "license": "GPL"},
    "linux": {"name": "ffmpeg.martin-riedl.de", "url": "https://ffmpeg.martin-riedl.de", "license": "GPL"},
    "windows": {"name": "BtbN/FFmpeg-Builds", "url": "https://github.com/BtbN/FFmpeg-Builds", "license": "LGPL"},
}


def source_info(os_key: str) -> dict:
    """Return ``{name, url, license}`` for the build source used on ``os_key``."""
    return _SOURCE_INFO.get(os_key, {"name": "", "url": "", "license": ""})


# --------------------------------------------------------------------------- #
# Manager
# --------------------------------------------------------------------------- #
class FfmpegManager:
    """Download / inspect / update a managed FFmpeg binary under ``app_dir``."""

    def __init__(self, app_dir: str | os.PathLike) -> None:
        self.app_dir = Path(app_dir)
        self.os_key, self.arch = _safe_target()
        # Aside binaries (see _swap_in) are swept once per process, on the
        # first status() call, which is the launch.
        self._swept = False

    # ----- locations ----------------------------------------------------- #
    @property
    def install_dir(self) -> Path:
        return self.app_dir / "bin"

    @property
    def binary_path(self) -> Path:
        return self.install_dir / _exe_name(self.os_key)

    @property
    def manifest_path(self) -> Path:
        return self.install_dir / "ffmpeg.json"

    def is_installed(self) -> bool:
        p = self.binary_path
        return p.is_file() and os.access(p, os.X_OK)

    def _read_manifest(self) -> dict:
        try:
            with open(self.manifest_path, encoding="utf-8") as fh:
                return json.load(fh)
        except Exception:
            return {}

    # ----- status / update check ----------------------------------------- #
    def status(self, custom_path: str = "") -> dict:
        """Describe the current FFmpeg situation for the UI.

        ``state`` is ``managed`` (our copy, green), ``path`` (an unmanaged
        binary: a user-linked ``custom_path`` or one found on $PATH, yellow) or
        ``missing`` (red). ``managed`` is True only for our own copy; ``custom``
        is True when a user-linked override is what's being used.
        ``source``/``source_url``/``source_license`` describe the build source
        for *this* platform only.

        Precedence is ``managed → custom → PATH → missing``: the managed copy is
        reported first so the transient managed path that the backend injects
        into ``path_binary_ffmpeg`` (see ``WavesBridge._resolve_ffmpeg``) can
        never be mistaken for a user override here.
        """
        if not self._swept:
            self._swept = True
            self.sweep_aside()
        info = source_info(self.os_key)
        base = {
            "source": info["name"],
            "source_url": info["url"],
            "source_license": info["license"],
            "os": self.os_key,
            "arch": self.arch,
        }
        if self.is_installed():
            mani = self._read_manifest()
            return {
                **base,
                "state": "managed",
                "available": True,
                "managed": True,
                "custom": False,
                "path": str(self.binary_path),
                "version": mani.get("ffmpeg_version") or mani.get("label") or "",
                "build": mani.get("version", ""),
            }
        cp = (custom_path or "").strip()
        if cp and os.path.isfile(cp):
            ver = _probe_version(cp)
            if ver:  # only treat a *working* binary as available (fail-closed)
                return {
                    **base,
                    "state": "path",
                    "available": True,
                    "managed": False,
                    "custom": True,
                    "path": cp,
                    "version": ver,
                    "build": "",
                }
        on_path = _which_ffmpeg(self.os_key)
        if on_path:
            return {
                **base,
                "state": "path",
                "available": True,
                "managed": False,
                "custom": False,
                "path": on_path,
                "version": _probe_version(on_path),
                "build": "",
            }
        return {
            **base,
            "state": "missing",
            "available": False,
            "managed": False,
            "custom": False,
            "path": "",
            "version": "",
            "build": "",
        }

    def update_available(self, session: requests.Session | None = None) -> tuple[bool, str, str]:
        """Return ``(available, current_build, latest_build)``.

        Only meaningful for a *managed* install; PATH/missing report no update.
        """
        if not self.is_installed():
            return False, "", ""
        current = self._read_manifest().get("version", "")
        rel = latest(self.os_key, self.arch, session)
        latest_build = rel.version if rel else ""
        return (bool(rel and latest_build and latest_build != current), current, latest_build)

    # ----- install ------------------------------------------------------- #
    def install(
        self,
        release: Release | None = None,
        progress_cb=None,
        log_cb=None,
        abort: Event | None = None,
        session: requests.Session | None = None,
    ) -> dict:
        """Download, verify, extract and install the binary; return ``status()``.

        Atomic: a fresh binary is staged next to the target, checksum-verified,
        (on macOS) signature-verified, and smoke-tested *before* it is swapped in
        via :func:`os.replace`. A failed/canceled install, including a
        checksum-valid download that won't actually run, therefore never
        corrupts the existing working copy. ``progress_cb(pct)`` and
        ``log_cb(msg)`` are optional.
        """

        def _log(msg: str) -> None:
            logger.info("ffmpeg-manager: %s", msg)
            if log_cb:
                log_cb(msg)

        def _check_abort() -> None:
            if abort is not None and abort.is_set():
                raise FfmpegCancelled()

        sess = session or _session()
        if release is None:
            _log("resolving latest build")
            release = latest(self.os_key, self.arch, sess)
        if release is None:
            raise FfmpegUnsupportedPlatform(f"No FFmpeg build for {self.os_key}/{self.arch}")

        self.install_dir.mkdir(parents=True, exist_ok=True)
        _check_abort()

        # 1. download the zip (streamed, with progress) to a temp file.
        _log(f"downloading {release.label or release.version}")
        with tempfile.NamedTemporaryFile(dir=self.install_dir, suffix=".zip", delete=False) as tmp:
            zip_tmp = Path(tmp.name)
        # A staged name of this install's OWN: two Waves instances share
        # <config>/bin, and both staging through one fixed "ffmpeg.new" let
        # either truncate, promote, or unlink the other's half-written binary
        # (the in-process inflight flag cannot see a second process). A
        # crashed install's leftover stays behind under the same policy as
        # the tmp zip above; neither carries user data.
        fd_staged, staged_name = tempfile.mkstemp(
            dir=self.install_dir, prefix=_exe_name(self.os_key) + ".", suffix=".new"
        )
        os.close(fd_staged)
        staged = Path(staged_name)
        try:
            self._download(sess, release.url, zip_tmp, progress_cb, abort)
            _check_abort()

            # 2. verify the published checksum, mandatory (fail-closed).
            # A same-channel .sha256 is only a corruption check, not proof of
            # authenticity, but refusing to install an *unverifiable* download
            # closes the easiest attack: dropping/404ing the sidecar so the check
            # is skipped. Both real sources (martin-riedl, BtbN) always publish a
            # .sha256, so this never blocks a legitimate install.
            expected = release.sha256 or self._fetch_sha256(sess, release.sha256_url, release.sha256_name)
            if not expected:
                raise ValueError("refusing to install FFmpeg: no checksum available to verify the download")
            _log("verifying checksum")
            actual = _sha256_file(zip_tmp)
            if actual.lower() != expected.lower():
                raise ValueError(f"checksum mismatch: expected {expected}, got {actual}")

            # 3. extract the ffmpeg member to a staged binary next to the target.
            _log("installing")
            _extract_ffmpeg(zip_tmp, staged, self.os_key)
            staged.chmod(
                staged.stat().st_mode | stat.S_IRWXU | stat.S_IRGRP | stat.S_IXGRP | stat.S_IROTH | stat.S_IXOTH
            )

            # 4. macOS: verify the advertised signature / notarization before we
            # trust the binary. We do NOT strip quarantine or ad-hoc re-sign an
            # unverified download to slip it past Gatekeeper, that would launder
            # a possibly-tampered binary. On a genuine signed+notarized build
            # this is a no-op that leaves the real signature intact.
            if self.os_key == "macos":
                self._macos_verify(staged)

            # 5. smoke-test the STAGED binary BEFORE swapping it in, so a
            # checksum-valid but non-runnable download (wrong arch, missing
            # loader, Gatekeeper block) leaves the existing working copy
            # untouched. Only a binary that actually runs gets promoted. This
            # runs on a worker, so the probe may outwait a first-run virus
            # scan instead of failing a good install at the short timeout.
            ver = _probe_version(str(staged), timeout=_SMOKE_TIMEOUT)
            if not ver:
                raise RuntimeError("FFmpeg downloaded but `ffmpeg -version` failed, keeping the existing binary")

            # 6. atomically swap the validated binary into place.
            self.sweep_aside()
            self._swap_in(staged)
        finally:
            zip_tmp.unlink(missing_ok=True)
            staged.unlink(missing_ok=True)

        # 7. record the manifest only after a successful swap, so status() never
        # reports managed/green off a manifest for an install that didn't land.
        manifest = {
            **asdict(release),
            "ffmpeg_version": ver,
            "installed_at": int(time.time()),
        }
        # Staged and swapped like every other shared-folder JSON: a plain
        # open("w") from two instances (or a crash mid-dump) left a corrupt
        # manifest that read back as {}, blanking the recorded version.
        fd_manifest, manifest_tmp = tempfile.mkstemp(
            dir=self.install_dir, prefix=self.manifest_path.name + ".", suffix=".tmp"
        )
        try:
            with os.fdopen(fd_manifest, "w", encoding="utf-8") as fh:
                json.dump(manifest, fh, indent=2)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(manifest_tmp, self.manifest_path)
        except Exception:
            with contextlib.suppress(OSError):
                os.remove(manifest_tmp)
            raise
        _log(f"installed ffmpeg {ver}")
        return self.status()

    def remove(self) -> dict:
        """Delete the managed binary and its manifest, and report.

        The status dict gains ``remove_error`` (plain wording) when the binary
        could not be removed: on Windows a running ``ffmpeg.exe`` (a FLAC
        extraction or a preview remux in flight) cannot be deleted, only
        renamed, so it is moved aside and swept at the next launch; when even
        that fails the caller gets a message instead of an exception from a
        GUI slot.
        """
        error = ""
        try:
            if self.os_key == "windows" and self.binary_path.exists():
                self._move_aside(self.binary_path)
            self.binary_path.unlink(missing_ok=True)
        except OSError:
            logger.warning("ffmpeg: could not remove the managed binary", exc_info=True)
            error = "FFmpeg is in use right now; try again once downloads and previews have finished."
        try:
            self.manifest_path.unlink(missing_ok=True)
        except OSError:
            logger.debug("ffmpeg: could not remove the manifest", exc_info=True)
        self.sweep_aside()
        status = self.status()
        if error:
            status["remove_error"] = error
        return status

    #: Suffix of a managed ``ffmpeg.exe`` that was renamed out of the way while
    #: it was running (Windows lets a running executable be renamed, never
    #: replaced or deleted). Swept at the next launch, see :meth:`sweep_aside`.
    _ASIDE_SUFFIX = ".old-"

    def _move_aside(self, path: Path) -> Path:
        aside = path.with_name(f"{path.name}{self._ASIDE_SUFFIX}{os.getpid()}")
        with contextlib.suppress(OSError):
            aside.unlink(missing_ok=True)
        os.replace(path, aside)
        return aside

    def _swap_in(self, staged: Path) -> None:
        """Promote the validated staged binary to ``binary_path``.

        On Windows ``os.replace`` over a running ``ffmpeg.exe`` fails with
        access denied (python-ffmpeg spawns the managed binary for every FLAC
        extraction, and a preview remux can be in flight), so the live file is
        first renamed aside, which Windows allows, and the aside copy is
        deleted best-effort at the next launch. Elsewhere the rename over a
        running binary is fine. If the promote itself then fails, the aside
        copy is moved back, so a failed update keeps the working binary
        instead of leaving it for the next sweep to delete.
        """
        aside = None
        if self.os_key == "windows" and self.binary_path.exists():
            try:
                aside = self._move_aside(self.binary_path)
            except OSError:
                logger.debug("ffmpeg: could not move the running binary aside", exc_info=True)
        try:
            os.replace(staged, self.binary_path)
        except OSError:
            if aside is not None and not self.binary_path.exists():
                try:
                    os.replace(aside, self.binary_path)
                except OSError:
                    logger.debug("ffmpeg: could not move the previous binary back", exc_info=True)
            raise

    def sweep_aside(self) -> None:
        """Delete binaries an earlier install or remove renamed aside.

        Best-effort and quiet: one that is still running (a Waves that has not
        exited yet, or a straggling ffmpeg child) stays until the next sweep.
        Only the manager's own ``<exe>.old-<pid>`` files under its ``bin``
        folder are touched, never anything of the user's.
        """
        pattern = f"{_exe_name(self.os_key)}{self._ASIDE_SUFFIX}*"
        try:
            leftovers = list(self.install_dir.glob(pattern))
        except OSError:
            return
        for path in leftovers:
            try:
                path.unlink()
            except OSError:
                logger.debug("ffmpeg: an aside binary is still in use, left for the next sweep")

    # ----- internals ----------------------------------------------------- #
    def _download(self, sess, url: str, dest: Path, progress_cb, abort: Event | None) -> None:
        with sess.get(url, stream=True, timeout=_TIMEOUT) as resp:
            resp.raise_for_status()
            total = int(resp.headers.get("Content-Length") or 0)
            done = 0
            with open(dest, "wb") as fh:
                for chunk in resp.iter_content(chunk_size=_CHUNK):
                    if abort is not None and abort.is_set():
                        raise FfmpegCancelled()
                    if not chunk:
                        continue
                    fh.write(chunk)
                    done += len(chunk)
                    if progress_cb and total:
                        progress_cb(min(100.0, done / total * 100.0))
            if progress_cb and not total:
                progress_cb(100.0)

    def _fetch_sha256(self, sess, sha_url: str | None, name: str | None = None) -> str | None:
        if not sha_url:
            return None
        try:
            resp = sess.get(sha_url, timeout=_TIMEOUT)
            resp.raise_for_status()
            text = resp.text
        except Exception:
            logger.debug("could not fetch sha256 from %s", sha_url, exc_info=True)
            return None
        if name:
            # Combined manifest (e.g. BtbN's checksums.sha256, many lines): take OUR
            # asset's line, never blindly the first, that would be the hash of an
            # unrelated file. Missing line → None → install fails closed.
            for line in text.splitlines():
                parts = line.split()
                if len(parts) >= 2 and os.path.basename(parts[-1].lstrip("*")) == name:
                    return parts[0] or None
            return None
        # Single-file sidecar: "<hex>  ffmpeg.zip"  (sha256sum style).
        parts = text.split()
        return parts[0] if parts else None

    def _macos_verify(self, path: Path) -> None:
        """Verify the advertised code signature of a staged macOS binary.

        The martin-riedl macOS builds are signed + notarized; we verify that
        rather than laundering an unverified download past Gatekeeper. If the
        platform's own tools confirm the signature (``codesign --verify`` and,
        when available, ``spctl --assess``) we trust it; otherwise we leave the
        staged binary in place and let the smoke test / caller decide, but we
        never ad-hoc re-sign or strip quarantine to force it to run.
        """
        p = str(path)
        verify = subprocess.run(
            ["codesign", "--verify", "--deep", "--strict", p], capture_output=True, text=True, check=False
        )
        if verify.returncode != 0:
            # Not fatal here, the smoke test still gates promotion, but record
            # that the signature could not be verified so we make no false trust
            # claim. We deliberately do NOT re-sign to make it "work".
            # codesign's stderr names the staged path (under the home folder),
            # so only its exit status is logged.
            logger.warning(
                "ffmpeg-manager: could not verify code signature of downloaded binary (codesign exit %d)",
                verify.returncode,
            )
            return
        # Gatekeeper assessment is best-effort: `spctl` may be unavailable or
        # decline in headless/CI contexts; a failure here is logged, not fatal.
        subprocess.run(["spctl", "--assess", "--type", "execute", p], capture_output=True, check=False)


def _safe_target() -> tuple[str, str]:
    try:
        return target()
    except FfmpegUnsupportedPlatform:
        return "", ""


def _which_ffmpeg(os_key: str, *, retry_slow: bool = False) -> str:
    from waves.helper.system_ffmpeg import find_system_ffmpeg

    # Only one that answers: a broken install left in a package manager's
    # folder must not read as installed (with no version). One that was only
    # slow (a snap cold start, a first-run virus scan) stays a candidate,
    # but as a fallback in a second pass: a copy that may be hung must not
    # shadow a working one further down the list.
    name = _exe_name(os_key)
    hit = find_system_ffmpeg(name, works=lambda p: bool(_probe_version(p)))
    return hit or find_system_ffmpeg(name, works=_probe_retry_slow if retry_slow else _probe_was_slow)


def working_system_ffmpeg(*, retry_slow: bool = False) -> str:
    """The user's own FFmpeg that a download, a preview or the FLAC check runs:
    the one status() names, so Settings never shows one system binary while
    a job runs another (a broken first candidate is passed over by both; one
    too slow to answer the probe is kept as a fallback). ``retry_slow`` lets
    a worker-thread caller give a slow binary a fresh probe once its pause is
    over, so a cold start that has warmed up gets its version back; the GUI
    thread (status()) never re-forks on one."""
    return _which_ffmpeg(_safe_target()[0], retry_slow=retry_slow)


# Probing a binary means fork+exec+wait on it, which can block for the whole
# timeout when the file lives on a hung/stale mount. status() runs on the GUI
# thread (it's the synchronous ffmpegStatus slot), so keep the timeout short and
# memoize by (path, size, mtime): the first probe pays the cost, repeated
# status() calls (Settings open, every ffmpegStatusChanged) reuse the result and
# never re-fork. A different binary at the same path (size/mtime change) evicts.
_PROBE_TIMEOUT = 4
# The install's smoke test runs on a worker and must outlast a first-run
# virus scan of the staged binary, so it gets a far longer leash.
_SMOKE_TIMEOUT = 60
_probe_cache: dict[str, tuple[float, int, str]] = {}
# A binary that did not answer within the timeout (a cold start from snap, a
# virus scan of a large ffmpeg.exe on its first run) is not known to be
# broken: it stays a candidate (a fallback one, see _which_ffmpeg), and a
# worker may probe it again once its pause is over. (mtime, size, monotonic
# deadline for the next probe.)
_PROBE_SLOW_RETRY_S = 60.0
_probe_slow: dict[str, tuple[float, int, float]] = {}


def _probe_was_slow(path: str) -> bool:
    try:
        st = os.stat(path)
    except OSError:
        return False
    rec = _probe_slow.get(path)
    return rec is not None and rec[:2] == (st.st_mtime, st.st_size)


def _probe_retry_slow(path: str) -> bool:
    """(worker thread) A slow binary, probed again once its pause is over: a
    cold start that warmed up answers (the cached empty version is replaced,
    so status() shows the real one from then on), while one that keeps
    hanging earns another pause and stays a last resort."""
    try:
        st = os.stat(path)
    except OSError:
        return False
    rec = _probe_slow.get(path)
    if rec is None or rec[:2] != (st.st_mtime, st.st_size):
        return False
    if time.monotonic() < rec[2]:
        return True
    _probe_cache.pop(path, None)  # the timeout's cached "" would answer instead
    _probe_slow.pop(path, None)  # a fresh timeout below re-adds it
    return bool(_probe_version(path)) or _probe_was_slow(path)


def _probe_version(path: str, timeout: float = _PROBE_TIMEOUT) -> str:
    """Return the ``ffmpeg version <x>`` token, or "" if the binary won't run.

    Memoized per (path, mtime, size) so a repeated ``status()`` on the GUI
    thread doesn't re-fork a subprocess. A short timeout bounds the worst case
    (hung/stale-mount binary) instead of freezing the UI.
    """
    try:
        st = os.stat(path)
        key = (st.st_mtime, st.st_size)
    except OSError:
        _probe_cache.pop(path, None)
        return ""
    cached = _probe_cache.get(path)
    if cached is not None and cached[:2] == key:
        return cached[2]
    try:
        out = subprocess.run(
            [path, "-version"], capture_output=True, text=True, timeout=timeout, creationflags=proc.NO_WINDOW
        )
    except subprocess.TimeoutExpired:
        _probe_slow[path] = (key[0], key[1], time.monotonic() + _PROBE_SLOW_RETRY_S)
        _probe_cache[path] = (key[0], key[1], "")
        return ""
    except Exception:
        return ""
    if out.returncode != 0:
        ver = ""
    else:
        first = (out.stdout or "").splitlines()[0] if out.stdout else ""
        m = re.search(r"ffmpeg version (\S+)", first)
        ver = m.group(1) if m else (first.strip() or "")
    _probe_cache[path] = (key[0], key[1], ver)
    return ver


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(_CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def _extract_ffmpeg(zip_path: Path, dest: Path, os_key: str) -> None:
    """Extract the ffmpeg executable from ``zip_path`` to ``dest``.

    Handles both flat zips (martin-riedl: ``ffmpeg`` at root) and nested ones
    (BtbN: ``ffmpeg-…/bin/ffmpeg.exe``) by matching the member basename.
    """
    exe = _exe_name(os_key)
    with zipfile.ZipFile(zip_path) as zf:
        members = [n for n in zf.namelist() if not n.endswith("/")]
        cand = [n for n in members if os.path.basename(n) == exe]
        if not cand:
            raise FileNotFoundError(f"no '{exe}' inside {zip_path.name}")
        # Prefer one under a bin/ dir (BtbN), else the shallowest path.
        cand.sort(key=lambda n: (0 if "/bin/" in f"/{n}" else 1, n.count("/")))
        member = cand[0]
        with zf.open(member) as src, open(dest, "wb") as out:
            while True:
                chunk = src.read(_CHUNK)
                if not chunk:
                    break
                out.write(chunk)
