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

    The library's ``create_subprocess`` sets only ``CREATE_NEW_PROCESS_GROUP``
    (needed for graceful CTRL_BREAK termination); wrap it to add
    ``CREATE_NO_WINDOW`` as well. ``ffmpeg.ffmpeg`` imports the function by
    name, so both module attributes are patched. No-op off Windows or if the
    package layout changes.
    """
    if os.name != "nt":
        return
    try:
        import ffmpeg.ffmpeg
        import ffmpeg.utils
    except Exception:
        return

    original = ffmpeg.utils.create_subprocess

    def create_subprocess(*args, **kwargs):
        kwargs["creationflags"] = kwargs.get("creationflags", 0) | NO_WINDOW
        return original(*args, **kwargs)

    ffmpeg.utils.create_subprocess = create_subprocess
    ffmpeg.ffmpeg.create_subprocess = create_subprocess
