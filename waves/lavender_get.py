"""Fetch the Lavender installer for this computer from Lavender's latest GitHub release.

The Lavender drawer in Settings offers it when "Ask Lavender first" cannot
work. Everything is decided by the release's SHA256SUMS, and only after its
minisign signature checks out against Lavender's own release key (the key
Lavender's in-app updater trusts): the version, the installer's name and its
hash. Nothing from the unsigned GitHub API is used. The installer lands in
Waves' own folder under its release name and is never deleted (fetching the
same version again writes over it); the caller opens it. A download that is
canceled or does not verify leaves nothing behind.
"""

from __future__ import annotations

import hashlib
import logging
import os
import platform
import re
import shutil
import sys
from pathlib import Path
from threading import Event

import requests

from waves.waves_ui.signing import parse_sha256sums, verify_minisign
from waves.waves_ui.updater import _is_older

logger = logging.getLogger("waves.lavender_get")

RELEASES = "https://github.com/iamprivacy/Lavender/releases"
#: ``plugins.updater.pubkey`` in Lavender's src-tauri/tauri.conf.json.
LAVENDER_PUBLIC_KEY = (
    "dW50cnVzdGVkIGNvbW1lbnQ6IG1pbmlzaWduIHB1YmxpYyBrZXk6IEU1RTBBRTAwMTA5MDk1MzEK"
    "UldReGxaQVFBSzdnNWZFUnM5cnNhK1k5cWdXY2FZNWtCb0NyQjh3dHRRd0kwd3A2YlV2T2VkaUkK"
)
#: The first Lavender with the lyrics worker Waves asks.
MIN_VERSION = "0.1.5"
MAX_BYTES = 400 << 20
_TIMEOUT = 30
_CHUNK = 1 << 16
_TAG = re.compile(r"v\d+(?:\.\d+){1,3}")
_V = r"\d+(?:\.\d+){1,3}"
# What the drawer says when a fetch is refused.
NO_BUILD = "No Lavender build for this computer"
NOT_VERIFIED = "The download did not verify"
NO_RELEASE = "Lavender has no release Waves can use yet"


class LavenderGetError(Exception):
    """A refusal whose message is written for the user."""


class LavenderGetCanceled(Exception):
    pass


def installer_pattern(system: str | None = None, machine: str | None = None, which=shutil.which) -> str | None:
    """The installer this computer can use, as a regex, or None (then the site is offered).

    Only installers that put Lavender where ``lyrics_lavender`` looks for it:
    never the AppImage, which it cannot see.
    """
    system = system or sys.platform
    machine = (machine if machine is not None else platform.machine()).lower()
    arm = "arm" in machine or "aarch64" in machine
    if system == "darwin":
        return rf"Lavender_{_V}_{'aarch64' if arm else 'x64'}\.dmg"
    if system == "win32":
        # The x64 setup also runs on Windows on Arm.
        return rf"Lavender_{_V}_x64-setup\.exe"
    if system.startswith("linux") and not arm:
        if which("dpkg"):
            return rf"Lavender_{_V}_amd64\.deb"
        if which("rpm"):
            return rf"Lavender-{_V}-\d+\.x86_64\.rpm"
    return None


def fetch(dest_dir: Path, session: requests.Session, progress_cb=None, abort: Event | None = None) -> Path:
    """Download and verify the installer into ``dest_dir``; return its path.

    Raises LavenderGetError (with a message for the user) or LavenderGetCanceled.
    """
    pattern = installer_pattern()
    if not pattern:
        raise LavenderGetError(NO_BUILD)
    tag, name, sha = _latest(session, pattern)
    if abort is not None and abort.is_set():
        raise LavenderGetCanceled()
    logger.info("lavender installer: %s verified, downloading", tag)
    dest_dir.mkdir(parents=True, exist_ok=True)
    part, final = dest_dir / f"{name}.part", dest_dir / name
    try:
        if _download(session, f"{RELEASES}/download/{tag}/{name}", part, progress_cb, abort) != sha:
            raise LavenderGetError(NOT_VERIFIED)
        os.replace(part, final)
    finally:
        # Waves' own half-written or unverified download, never a kept installer.
        part.unlink(missing_ok=True)
    logger.info("lavender installer: ready")
    return final


def _latest(session: requests.Session, pattern: str) -> tuple[str, str, str]:
    """(tag, installer name, its sha256) from the latest release's signed SHA256SUMS."""
    logger.info("lavender installer: reading the latest release")
    sums = _get(session, f"{RELEASES}/latest/download/SHA256SUMS").content
    sig = _get(session, f"{RELEASES}/latest/download/SHA256SUMS.sig").text.strip()
    if not verify_minisign(sums, sig, LAVENDER_PUBLIC_KEY):
        raise LavenderGetError(NOT_VERIFIED)
    text = sums.decode("utf-8", "replace")
    m = re.search(r"^# lavender-version: (\S+)$", text, re.MULTILINE)
    tag = m.group(1) if m and _TAG.fullmatch(m.group(1)) else ""
    if not tag or _is_older(tag, MIN_VERSION):
        raise LavenderGetError(NO_RELEASE)
    table = parse_sha256sums(text)
    names = [n for n in table if re.fullmatch(pattern, n)]
    if len(names) != 1:
        raise LavenderGetError(NO_BUILD)
    return tag, names[0], table[names[0]]


def _download(session: requests.Session, url: str, part: Path, progress_cb, abort: Event | None) -> str:
    """Stream ``url`` into ``part``; return its sha256. Capped at MAX_BYTES."""
    digest = hashlib.sha256()
    with _get(session, url, stream=True) as resp:
        total = int(resp.headers.get("Content-Length") or 0)
        if total > MAX_BYTES:
            raise LavenderGetError(NOT_VERIFIED)
        done = 0
        with open(part, "wb") as fh:
            for chunk in resp.iter_content(chunk_size=_CHUNK):
                if abort is not None and abort.is_set():
                    raise LavenderGetCanceled()
                done += len(chunk)
                if done > MAX_BYTES:
                    raise LavenderGetError(NOT_VERIFIED)
                digest.update(chunk)
                fh.write(chunk)
                if progress_cb and total:
                    progress_cb(min(100.0, done / total * 100.0))
    return digest.hexdigest()


def session() -> requests.Session:
    sess = requests.Session()
    sess.headers["User-Agent"] = "Waves-lavender-get"
    return sess


def _get(sess: requests.Session, url: str, stream: bool = False) -> requests.Response:
    resp = sess.get(url, timeout=_TIMEOUT, stream=stream)
    resp.raise_for_status()
    return resp
