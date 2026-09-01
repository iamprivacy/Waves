"""Child-process spawn flags for a console-less Windows GUI build.

The packaged Windows app runs with ``--windows-console-mode=disable``, so any
child process (ffmpeg remux/probe) allocates a brand-new console window that
flashes over the UI. ``CREATE_NO_WINDOW`` suppresses it; on other platforms the
flag is 0 and everything is unchanged.
"""

from __future__ import annotations

import os
import subprocess

#: Extra ``creationflags`` for every ffmpeg/child spawn (0 outside Windows).
NO_WINDOW: int = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


def silence_python_ffmpeg() -> None:
    """Make the ``python-ffmpeg`` package spawn without a console window.

    The library's ``create_subprocess`` sets ``CREATE_NEW_PROCESS_GROUP``
    (needed for graceful CTRL_BREAK termination), but it does so with a plain
    ``kwargs["creationflags"] = CREATE_NEW_PROCESS_GROUP`` assignment that
    OVERWRITES whatever the caller passed. So the obvious wrapper (set
    ``creationflags |= NO_WINDOW`` then delegate to the original) is a no-op: the
    original throws our ``CREATE_NO_WINDOW`` away and a console still flashes for
    every ffmpeg spawn. We therefore spawn directly instead of delegating, ORing
    both flags together so CTRL_BREAK termination keeps working AND no console
    window appears. ``ffmpeg.ffmpeg`` imports the function by name, so both module
    attributes are patched. No-op off Windows or if the package layout changes.
    """
    if os.name != "nt":
        return
    try:
        import ffmpeg.ffmpeg
        import ffmpeg.utils
    except Exception:
        return

    def create_subprocess(*args, **kwargs):
        # ffmpeg argv here is built by python-ffmpeg, not user input, so S603 is a false positive.
        kwargs["creationflags"] = kwargs.get("creationflags", 0) | subprocess.CREATE_NEW_PROCESS_GROUP | NO_WINDOW
        return subprocess.Popen(*args, **kwargs)  # noqa: S603

    ffmpeg.utils.create_subprocess = create_subprocess
    ffmpeg.ffmpeg.create_subprocess = create_subprocess
