"""Find an FFmpeg the user installed themselves.

An app opened from the Finder, the Dock or a desktop launcher gets a bare PATH
(on macOS only /usr/bin:/bin:/usr/sbin:/sbin), so a Homebrew, MacPorts or
Linuxbrew FFmpeg that the user's terminal finds is invisible to a plain PATH
lookup. After PATH, look in the folders those package managers install into.
Windows installers (winget, Chocolatey, Scoop) put FFmpeg on the PATH that
Explorer hands every app, so PATH alone covers it there.
"""

from __future__ import annotations

import os
import shutil
import sys
from collections.abc import Callable


def _known_dirs() -> tuple[str, ...]:
    if sys.platform == "darwin":
        return ("/opt/homebrew/bin", "/usr/local/bin", "/opt/local/bin")
    if sys.platform.startswith("win"):
        return ()
    return ("/usr/local/bin", "/usr/bin", "/home/linuxbrew/.linuxbrew/bin", "/snap/bin")


def find_system_ffmpeg(name: str = "ffmpeg", works: Callable[[str], bool] | None = None) -> str:
    """Path to an FFmpeg on PATH or in a package manager's folder, else "".
    With ``works``, a candidate it rejects (a broken install left behind) is
    passed over for the next one."""
    found = shutil.which(name)
    candidates = [found] if found else []
    for folder in _known_dirs():
        candidate = os.path.join(folder, name)
        if candidate not in candidates and os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            candidates.append(candidate)
    for candidate in candidates:
        if works is None or works(candidate):
            return candidate
    return ""
