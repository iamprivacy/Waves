"""The loudness shape of a preview clip, for the now-playing rail.

A preview is a local, seekable ``.m4a`` the bundled ffmpeg remuxes before
playback starts (``WavesBridge._preview_source``). This module turns that
clip into ``n`` loudness buckets, 0..1 across the whole track: ffmpeg
decodes it to 8 kHz mono 16-bit (about a second for a full song, a few
hundred KB of samples), each bucket is the RMS of its slice, and the whole
set is normalized so the loudest bucket is 1. Loudness, not the loudest
sample: a modern master hits near full scale almost constantly, so a
peak-per-slice shape of a loud song is one solid block, while its RMS
still shows the intro, the breakdown and the bridge. Plain Python over
``array``, no numpy.
"""

from __future__ import annotations

import math
import subprocess
from array import array
from collections.abc import Sequence

PEAKS_N = 600
DECODE_RATE = 8000


def peak_buckets(samples: Sequence[int], n: int = PEAKS_N) -> list[float]:
    """``n`` buckets of the RMS of each slice, normalized to the loudest
    bucket. Silence (or no samples) is ``n`` zeros."""
    total = len(samples)
    if total == 0 or n <= 0:
        return [0.0] * max(0, n)
    out: list[float] = []
    for b in range(n):
        a = b * total // n
        z = max(a + 1, (b + 1) * total // n)
        acc = 0
        for v in samples[a:z]:
            acc += v * v
        out.append(math.sqrt(acc / (z - a)))
    top = max(out)
    if top == 0:
        return [0.0] * n
    return [round(p / top, 3) for p in out]


def peaks_from_clip(
    ffmpeg: str, path: str, n: int = PEAKS_N, *, timeout: float = 60, creationflags: int = 0
) -> list[float]:
    """Decode ``path`` with ``ffmpeg`` and bucket it. Raises on a decode
    failure or timeout; the caller decides what a missing waveform means."""
    cmd = [
        ffmpeg,
        "-v",
        "error",
        "-nostdin",
        "-i",
        path,
        "-vn",
        "-ac",
        "1",
        "-ar",
        str(DECODE_RATE),
        "-f",
        "s16le",
        "-",
    ]
    run_kw = {"check": True, "capture_output": True, "timeout": timeout, "creationflags": creationflags}
    proc = subprocess.run(cmd, **run_kw)  # noqa: S603
    samples = array("h")
    samples.frombytes(proc.stdout[: len(proc.stdout) - (len(proc.stdout) % 2)])
    return peak_buckets(samples, n)
