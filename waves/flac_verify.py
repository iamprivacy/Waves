"""Checking and sealing FLAC audio signatures without ever touching the audio.

A FLAC file opens with a STREAMINFO block that carries, among the stream's
shape, the MD5 of the decoded audio and the total sample count. ``flac -t``
and every checker built on libFLAC decode the file and compare; a header
whose signature is all zeros ("unset") cannot be verified, so those tools
report the file as failing even though every audio frame is intact, and
strict players treat it as corrupt. TIDAL's packaging shipped such headers
for a while and a copied-out extraction keeps the header verbatim, which is
how a Waves library can hold files like that.

This module is the one place both the download engine (right after an
extraction) and the Settings tool (over an existing library) go through.
Three guarantees hold for everything in it:

1. A scan reads. The header scan opens each file for reading and reads a few
   dozen bytes; the deep check additionally decodes the audio through
   ffmpeg, which reads too. Nothing in a music folder is written; what a
   scan found may be remembered under the config folder (see
   :class:`VerdictCache`), so the next scan reads only what changed.
2. The fix writes exactly the 34-byte STREAMINFO block, in place, through a
   single ``write`` of those 34 bytes. The file never changes size, no byte
   outside the block is written, the block's shape fields (block and frame
   sizes, sample rate, channels, bit depth) are carried over byte for byte,
   and the bytes on disk are compared with what the scan read right before
   the write and read back right after it.
3. The fix only ever fills in an UNSET signature, and only after the audio
   decoded cleanly (every frame's checksum verified, the file ending on a
   whole frame), to the length the header states (or, when the header
   states none, to the length the file's last frame proves). A signature that is
   set and does not match the audio is reported and left alone, and so is a
   file whose audio decodes shorter or longer than its header says: either
   the audio or the header is damaged, and writing the hash of whatever the
   audio now is over it would only hide that. A file that changed between
   the scan and the fix is left alone.

Qt-free on purpose: the bridge mixin (``waves.waves_ui.bridge_flac``) wraps
it in threads and signals, the download engine calls :func:`seal_file`
directly, and the tests drive it with plain files.
"""

from __future__ import annotations

import contextlib
import csv
import hashlib
import logging
import os
import pathlib
import re
import sqlite3
import subprocess
import threading
import time
from collections import deque
from collections.abc import Callable, Iterable, Iterator
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field, replace

from waves.constants import CHUNK_SIZE
from waves.ownership import path_under
from waves.waves_ui.proc import NO_WINDOW

logger = logging.getLogger("waves.flac")

STREAMINFO_SIZE = 34
_MARKER = b"fLaC"
_MD5_UNSET = bytes(16)
_TOTAL_SAMPLES_MASK = (1 << 36) - 1
# The PCM layouts whose MD5 libFLAC's definition and ffmpeg's output agree
# on: each sample as wide as its bit depth, little endian, interleaved. A
# depth that is not a whole number of bytes (20-bit, say) decodes to a
# wider, shifted sample, so its hash would come out different; such files
# are reported as unsupported and never written.
_PCM_FORMATS = {16: "s16le", 24: "s24le", 32: "s32le"}
# A file written more recently than this is left for the next run: it may
# still be arriving (a copy in progress has a complete header long before it
# has complete audio, and a signature computed over a partial file would be a
# damaged header of our own making).
SETTLE_SECONDS = 60.0
# A decode that has not finished after this long is stopped and reported, so
# a share that stopped answering cannot hold a worker forever: a fixed floor
# plus the time the file would take at a crawl (a healthy decode runs at tens
# of megabytes a second, so only a stalled read ever reaches the limit).
DECODE_MIN_SECONDS = 120.0
DECODE_MIN_BYTES_PER_SECOND = 128 * 1024
# How often the decode's watchdog looks at the cancel flag and the clock.
_WATCH_POLL_S = 0.25

# Row statuses. The first group comes out of a scan, the second out of a fix.
UNSET = "unset"  # no signature in the header: the case the fix is for
SET = "set"  # a signature is present (header scan only, not verified)
OK = "ok"  # deep check or seal: the signature matches the decoded audio
MISMATCH = "mismatch"  # a signature is present and does not match; reported, never rewritten
UNREADABLE = "unreadable"  # not a FLAC stream this checker can parse
UNSUPPORTED = "unsupported"  # a bit depth whose hash this checker cannot reproduce
DECODE_FAILED = "decode_failed"  # ffmpeg could not decode the audio cleanly
LENGTH_DIFFERS = "length_differs"  # the audio length differs from the header, or nothing proves it; left alone
SEALED = "sealed"  # the signature was unset, computed and written
CHANGED = "changed"  # the file changed since the scan; left alone
BUSY = "busy"  # written too recently to be trusted complete; left alone
WRITE_FAILED = "write_failed"  # the header could not be written (permissions, a read-only share)
CANCELLED = "cancelled"  # the run was canceled before this file was finished
# Not a file's status: a report line for a folder the walk could not open or
# list, so the files in it were never checked. Never in a scan's rows.
FOLDER_UNREADABLE = "folder_unreadable"

# What a row's status means, in the user's words (the report and the UI).
STATUS_LABELS = {
    UNSET: "no signature",
    SET: "signature present",
    OK: "verified",
    MISMATCH: "signature does not match the audio",
    UNREADABLE: "not a readable FLAC file",
    UNSUPPORTED: "bit depth not supported by this check",
    DECODE_FAILED: "audio did not decode cleanly",
    LENGTH_DIFFERS: "audio length differs from the header or cannot be confirmed, left alone",
    SEALED: "signature written",
    CHANGED: "changed since the scan, left alone",
    BUSY: "written less than a minute ago, left alone",
    WRITE_FAILED: "header could not be written",
    CANCELLED: "not checked (canceled)",
    FOLDER_UNREADABLE: "folder could not be opened, its files were not checked",
}

REPORT_COLUMNS = (
    "status",
    "meaning",
    "file",
    "detail",
    "bit_depth",
    "sample_rate",
    "channels",
    "samples_in_header",
    "samples_decoded",
    "md5_in_header",
    "md5_of_audio",
    # "yes" when the verdict is an earlier scan's (the file has not changed
    # since); "" when the file was read in this run.
    "from_earlier_check",
)


class FlacHeaderError(ValueError):
    """The file does not start with a STREAMINFO block this checker can read."""


_NOT_FLAC = "not a native FLAC stream"
_TRUNCATED_HEADER = "truncated before the first metadata block"
_NOT_FIRST = "STREAMINFO is not the first metadata block"
_TRUNCATED_BLOCK = "truncated STREAMINFO block"
_BAD_MD5_LEN = "an MD5 signature is 16 bytes"
_BAD_BLOCK_LEN = "STREAMINFO must stay 34 bytes"
_SHAPE_FIELDS = "the block and frame size fields are never rewritten"
_HEADER_MOVED = "the header on disk is not the one the scan read"
_READ_BACK = "the header read back differently from what was written"
_NOT_WHOLE = "the file ends partway through an audio frame (cut off, or bytes after the last frame)"
_UNPROVEN = (
    "the header gives no sample count and the end of the file does not prove the audio is complete"
    " (it may be cut off on a frame boundary)"
)


class DecodeFailed(RuntimeError):
    """ffmpeg did not decode the audio cleanly."""


class Unsupported(RuntimeError):
    """A bit depth whose MD5 this checker cannot reproduce."""


class HeaderChanged(RuntimeError):
    """The header bytes on disk are not the ones the scan read."""


class Cancelled(RuntimeError):
    """The caller asked to stop."""


@dataclass(frozen=True)
class StreamInfo:
    """The STREAMINFO block as read, plus where it sits in the file."""

    offset: int  # file offset of the 34 block bytes
    raw: bytes  # those 34 bytes, exactly as read
    sample_rate: int
    channels: int
    bits_per_sample: int
    total_samples: int
    md5: bytes  # 16 bytes

    @property
    def md5_unset(self) -> bool:
        return self.md5 == _MD5_UNSET


@dataclass(frozen=True)
class Row:
    """One file's verdict. ``info`` is kept so a fix can prove the header is
    still the one the scan saw; ``size``/``mtime_ns`` so it can prove the
    file is."""

    path: str
    status: str
    detail: str = ""
    info: StreamInfo | None = None
    size: int = -1
    mtime_ns: int = -1
    md5_audio: bytes | None = None
    samples_decoded: int = -1
    # The verdict came from an earlier scan of the unchanged file (see
    # VerdictCache); the file itself was only stat'ed this time.
    cached: bool = field(default=False, compare=False)


# ---- the header ---------------------------------------------------------------


def _syncsafe(raw: bytes) -> int:
    return (raw[0] << 21) | (raw[1] << 14) | (raw[2] << 7) | raw[3]


def read_streaminfo(path: str | os.PathLike) -> StreamInfo:
    """Read the STREAMINFO block (a few dozen bytes; nothing else is read).

    An ID3v2 tag in front of the stream (some taggers write one) is stepped
    over. The block must be the first metadata block, as the format
    requires; anything else raises :class:`FlacHeaderError`.
    """
    with open(path, "rb") as f:
        head = f.read(10)
        pos = 0
        if head[:3] == b"ID3" and len(head) == 10:
            footer = 10 if head[5] & 0x10 else 0
            pos = 10 + _syncsafe(head[6:10]) + footer
            f.seek(pos)
            head = f.read(4)
        if head[:4] != _MARKER:
            raise FlacHeaderError(_NOT_FLAC)
        pos += 4
        f.seek(pos)
        block_header = f.read(4)
        if len(block_header) != 4:
            raise FlacHeaderError(_TRUNCATED_HEADER)
        block_type = block_header[0] & 0x7F
        length = int.from_bytes(block_header[1:4], "big")
        if block_type != 0 or length != STREAMINFO_SIZE:
            raise FlacHeaderError(_NOT_FIRST)
        offset = pos + 4
        raw = f.read(STREAMINFO_SIZE)
    return _parse_streaminfo(offset, raw)


def _parse_streaminfo(offset: int, raw: bytes) -> StreamInfo:
    """The STREAMINFO fields of the 34 block bytes found at ``offset``."""
    if len(raw) != STREAMINFO_SIZE:
        raise FlacHeaderError(_TRUNCATED_BLOCK)
    packed = int.from_bytes(raw[10:18], "big")
    return StreamInfo(
        offset=offset,
        raw=raw,
        sample_rate=packed >> 44,
        channels=((packed >> 41) & 0x7) + 1,
        bits_per_sample=((packed >> 36) & 0x1F) + 1,
        total_samples=packed & _TOTAL_SAMPLES_MASK,
        md5=raw[18:34],
    )


def sealed_bytes(info: StreamInfo, md5: bytes, total_samples: int) -> bytes:
    """The 34 block bytes with the signature and sample count filled in.

    Bytes 0..9 (block sizes, frame sizes) and the shape bits of the packed
    field (sample rate, channels, bit depth) are carried over unchanged. A
    sample count the 36-bit field cannot hold keeps the header's own.
    """
    if len(md5) != 16:
        raise ValueError(_BAD_MD5_LEN)
    packed = int.from_bytes(info.raw[10:18], "big")
    if 0 <= total_samples <= _TOTAL_SAMPLES_MASK:
        packed = (packed & ~_TOTAL_SAMPLES_MASK) | total_samples
    out = info.raw[:10] + packed.to_bytes(8, "big") + md5
    if len(out) != STREAMINFO_SIZE:  # pragma: no cover (arithmetic above fixes the length)
        raise ValueError(_BAD_BLOCK_LEN)
    return out


def write_streaminfo(path: str | os.PathLike, info: StreamInfo, new_raw: bytes) -> None:
    """Replace the 34 STREAMINFO bytes in place, and nothing else.

    Refuses (:class:`HeaderChanged`) when the bytes on disk are not the ones
    ``info`` was read from, so a file rewritten since the scan is never
    touched on stale knowledge. One ``write`` call of exactly 34 bytes at an
    offset inside the file: the size cannot change. The bytes are read back
    and compared, and a difference is raised as :class:`OSError`.
    """
    if len(new_raw) != STREAMINFO_SIZE:
        raise ValueError(_BAD_BLOCK_LEN)
    if new_raw[:10] != info.raw[:10]:
        raise ValueError(_SHAPE_FIELDS)
    with open(path, "r+b") as f:
        f.seek(info.offset)
        current = f.read(STREAMINFO_SIZE)
        if current != info.raw:
            raise HeaderChanged(_HEADER_MOVED)
        f.seek(info.offset)
        f.write(new_raw)
        f.flush()
        # A share that cannot fsync has still taken the write; the read-back
        # below is the check that counts.
        with contextlib.suppress(OSError):
            os.fsync(f.fileno())
        f.seek(info.offset)
        back = f.read(STREAMINFO_SIZE)
    if back != new_raw:
        raise OSError(_READ_BACK)


# ---- the audio ----------------------------------------------------------------


def _ensure_alive(should_continue: Callable[[], bool] | None) -> None:
    if should_continue is not None and not should_continue():
        raise Cancelled


def _crc_table(poly: int, width: int) -> tuple[int, ...]:
    top, mask = 1 << (width - 1), (1 << width) - 1
    table = []
    for byte in range(256):
        crc = byte << (width - 8)
        for _ in range(8):
            crc = ((crc << 1) ^ poly) & mask if crc & top else (crc << 1) & mask
        table.append(crc)
    return tuple(table)


# The format's two checksums: CRC-8 (x^8+x^2+x+1) closes every frame header,
# CRC-16 (x^16+x^15+x^2+1) closes every frame. Both start at 0 with no final
# xor, so a frame run through CRC-16 together with its own footer gives 0.
_CRC8 = _crc_table(0x07, 8)
_CRC16 = _crc_table(0x8005, 16)


def _crc8(data: bytes) -> int:
    crc = 0
    for byte in data:
        crc = _CRC8[crc ^ byte]
    return crc


def _crc16(data: bytes) -> int:
    crc = 0
    for byte in data:
        crc = ((crc << 8) & 0xFFFF) ^ _CRC16[(crc >> 8) ^ byte]
    return crc


def _frame_header_len(buf: bytes, i: int) -> int:
    """The length of the frame header at ``buf[i]`` (sync code through its
    CRC-8), or 0 when no valid one starts there."""
    if i + 5 > len(buf) or buf[i] != 0xFF or buf[i + 1] not in (0xF8, 0xF9):
        return 0
    block_code, rate_code = buf[i + 2] >> 4, buf[i + 2] & 0x0F
    if block_code == 0 or rate_code == 0x0F or buf[i + 3] & 0x01:
        return 0
    # The frame or sample number, UTF-8 style: the lead byte's run of high
    # ones is the length (0xFE leads the longest, 7 bytes).
    lead = buf[i + 4]
    if lead < 0x80:
        coded = 1
    elif 0xC0 <= lead < 0xFF:
        coded = 8 - (~lead & 0xFF).bit_length()
    else:
        return 0
    length = 4 + coded + {6: 1, 7: 2}.get(block_code, 0) + {12: 1, 13: 2, 14: 2}.get(rate_code, 0)
    if i + length >= len(buf) or _crc8(buf[i : i + length]) != buf[i + length]:
        return 0
    return length + 1


def _frame_fields(head: bytes) -> tuple[bool, int, int]:
    """A valid frame header (see :func:`_frame_header_len`) read into
    (variable blocking, frame or sample number, block size in samples)."""
    variable = bool(head[1] & 0x01)
    block_code = head[2] >> 4
    lead = head[4]
    coded = 1 if lead < 0x80 else 8 - (~lead & 0xFF).bit_length()
    number = lead if coded == 1 else lead & ((1 << (7 - coded)) - 1)
    for byte in head[5 : 4 + coded]:
        number = (number << 6) | (byte & 0x3F)
    pos = 4 + coded
    if block_code == 1:
        block = 192
    elif block_code <= 5:
        block = 576 << (block_code - 2)
    elif block_code == 6:
        block = head[pos] + 1
    elif block_code == 7:
        block = int.from_bytes(head[pos : pos + 2], "big") + 1
    else:
        block = 256 << (block_code - 8)
    return variable, number, block


def _last_frame_header(path: str | os.PathLike, info: StreamInfo) -> bytes | None:
    """The header of the whole frame the file ends on, or None when the file
    does not end on one. Reads only the tail (at most the largest frame the
    header allows); see :func:`ends_on_whole_frame`."""
    max_frame = int.from_bytes(info.raw[7:10], "big")
    max_block = int.from_bytes(info.raw[2:4], "big") or 65535
    # The largest frame the format allows for this stream: every channel
    # stored verbatim (the side channel one bit wider), plus headers. The
    # header's own figure is taken when larger, within reason: a damaged
    # header must not turn this into a read of megabytes.
    bound = max_block * info.channels * (info.bits_per_sample + 1) // 8 + 64
    window = max(min(max_frame, 2 * bound), bound)
    with open(path, "rb") as f:
        size = f.seek(0, os.SEEK_END)
        start = max(info.offset + STREAMINFO_SIZE, size - window)
        f.seek(start)
        tail = f.read(size - start)
    i = len(tail) - 2
    while (i := tail.rfind(b"\xff", 0, i)) >= 0:
        head = _frame_header_len(tail, i)
        if head and len(tail) - i >= head + 2 and _crc16(tail[i:]) == 0:
            return tail[i : i + head]
    return None


def ends_on_whole_frame(path: str | os.PathLike, info: StreamInfo) -> bool:
    """Whether the file's last byte closes a whole audio frame. Reads only
    the tail (at most the largest frame the header allows).

    ffmpeg's parser drops a few stray bytes after the last frame without a
    word, which is what a cut a few bytes into a frame leaves behind; the
    decode then comes out clean and short. With a sample count in the header
    the length guard catches that, but a header that says 0 (unknown) has
    nothing to compare against, so the end of the file is checked here: some
    frame header near the end must start a run of bytes, up to the file's
    last byte, whose CRC-16 checks out.

    The CRC cannot see zero bytes after a frame (they leave it at 0), so a
    few zeros after the last whole frame pass. That hides no damage: every
    frame before them is whole, and a cut that lost a frame would leave far
    more than a few bytes to fill.

    A cut exactly on a frame boundary also ends on a whole frame; see
    :func:`proven_total_samples` for what can tell that one apart.
    """
    return _last_frame_header(path, info) is not None


def proven_total_samples(path: str | os.PathLike, info: StreamInfo) -> int | None:
    """The stream's length in samples as its last frame proves it, or None
    when the end of the file proves nothing. Reads only the tail.

    A cut on a frame boundary leaves a shorter stream of whole frames that
    decodes cleanly, so with no sample count in the header something else
    has to show the stream is complete. In a fixed-blocksize stream (the
    blocking bit clear, the header's least and largest block equal) every
    frame holds the same number of samples except the stream's very last,
    which holds what is left over: a file that ends on a frame shorter than
    the others ends where the encoder stopped, and the frame's number times
    the block size plus its own size is the length. A last frame of the full
    size (a cut, or a length that happens to be a whole number of blocks), a
    variable-blocksize stream, or a header whose block sizes disagree proves
    nothing, and None is returned: the caller must not sign such a file.
    """
    try:
        head = _last_frame_header(path, info)
    except OSError:
        return None
    if head is None:
        return None
    variable, number, block = _frame_fields(head)
    min_block = int.from_bytes(info.raw[0:2], "big")
    max_block = int.from_bytes(info.raw[2:4], "big")
    if variable or min_block != max_block or not 0 < block < max_block:
        return None
    return number * max_block + block


def decode_time_limit(size: int) -> int:
    """Seconds a decode of a file of ``size`` bytes may take before it is
    stopped (see ``DECODE_MIN_SECONDS``)."""
    return round(DECODE_MIN_SECONDS + size / DECODE_MIN_BYTES_PER_SECOND)


def _watch_decode(
    proc: subprocess.Popen,
    should_continue: Callable[[], bool] | None,
    deadline: float,
    done: threading.Event,
    stopped: list[str],
) -> None:
    """(watchdog thread) Kill the decoder on a cancel or past the deadline,
    noting which in ``stopped``; return once ``done`` is set either way."""
    while not done.wait(_WATCH_POLL_S):
        if should_continue is not None and not should_continue():
            stopped.append("cancelled")
        elif time.monotonic() > deadline:
            stopped.append("timeout")
        else:
            continue
        with contextlib.suppress(OSError):
            proc.kill()
        return


def decode_md5(
    path: str | os.PathLike,
    ffmpeg: str,
    info: StreamInfo,
    should_continue: Callable[[], bool] | None = None,
    shared: int = 1,
) -> tuple[bytes, int]:
    """Decode the file through ffmpeg and hash the PCM the way libFLAC
    defines the signature. Returns the 16-byte digest and the sample count.

    Reads only. The decoder child is killed on cancel and on any error.

    The decode has to be clean, not merely finished: left to itself ffmpeg
    skips a damaged or missing frame, carries on and exits 0, so a truncated
    file would hand back a shorter stream and a hash of it. ``-xerror`` makes
    the first decoder error fatal (a bad frame, a cut-off tail, bytes after
    the last frame), and ``-err_detect crccheck+explode`` makes the decoder
    verify each frame's CRC-16 and stop at a mismatch: with ``crccheck``
    alone a mismatch is only logged and the frame's damaged samples are
    decoded and hashed like any other, which is how one flipped bit in the
    middle of a file used to get signed. The few stray bytes a cut can leave
    after the last whole frame, which the parser drops silently, are caught
    by :func:`ends_on_whole_frame` before the decode starts. Such a file is
    reported and never signed.

    A watchdog thread stops the decoder on a cancel or once the time limit
    for the file's size has passed (see ``DECODE_MIN_SECONDS``), so a read
    stalled on a share that stopped answering ends the run's wait too: the
    pipe closes, the row comes back canceled or failed, and the thread
    ends with the call.
    """
    pcm_fmt = _PCM_FORMATS.get(info.bits_per_sample)
    if pcm_fmt is None or info.channels < 1:
        raise Unsupported(f"{info.bits_per_sample}-bit")
    if not ends_on_whole_frame(path, info):
        raise DecodeFailed(_NOT_WHOLE)
    argv = [
        str(ffmpeg),
        "-hide_banner",
        "-nostdin",
        "-loglevel",
        "error",
        "-xerror",
        "-err_detect",
        "crccheck+explode",
        "-i",
        os.fspath(path),
        "-f",
        pcm_fmt,
        "-",
    ]
    # ``shared`` decodes run at once split the link between them, so each
    # gets that many times as long before it counts as stuck.
    limit = decode_time_limit(os.path.getsize(path)) * max(1, shared)
    digest = hashlib.md5()  # noqa: S324 (the FLAC format mandates MD5)
    n_bytes = 0
    spawn = {"stdout": subprocess.PIPE, "stderr": subprocess.DEVNULL, "creationflags": NO_WINDOW}
    # argv is the configured ffmpeg path plus one file under the user's folders.
    with subprocess.Popen(argv, **spawn) as proc:  # noqa: S603
        done = threading.Event()
        stopped: list[str] = []
        watchdog = threading.Thread(
            target=_watch_decode,
            args=(proc, should_continue, time.monotonic() + limit, done, stopped),
            name="flac-decode-watchdog",
            daemon=True,
        )
        watchdog.start()
        try:
            while proc.stdout is not None and (chunk := proc.stdout.read(CHUNK_SIZE)):
                _ensure_alive(should_continue)
                digest.update(chunk)
                n_bytes += len(chunk)
        except BaseException:
            proc.kill()
            raise
        finally:
            done.set()
            watchdog.join()
    if stopped == ["cancelled"]:
        raise Cancelled
    if stopped:
        raise DecodeFailed(f"the decoder did not finish within {limit} seconds")  # noqa: TRY003
    if proc.returncode != 0 or n_bytes == 0:
        raise DecodeFailed(f"the decoder reported an error (ffmpeg exit {proc.returncode})")  # noqa: TRY003
    sample_bytes = info.bits_per_sample // 8
    return digest.digest(), n_bytes // (sample_bytes * info.channels)


# ---- one file -----------------------------------------------------------------


def _stat(path: str) -> tuple[int, int]:
    st = os.stat(path)
    return st.st_size, st.st_mtime_ns


def _read_row(path: str) -> Row:
    """The header read into a row: ``SET`` or ``UNSET``, or ``UNREADABLE``
    with the reason (then ``info`` is None)."""
    size, mtime_ns = -1, -1
    try:
        size, mtime_ns = _stat(path)
        info = read_streaminfo(path)
    except FlacHeaderError as exc:
        # What the file holds, not a failure to read it: the stat is kept so
        # the verdict can be remembered against it (see VerdictCache).
        return Row(path, UNREADABLE, str(exc), size=size, mtime_ns=mtime_ns)
    except OSError as exc:
        return Row(path, UNREADABLE, exc.strerror or str(exc))
    return Row(path, UNSET if info.md5_unset else SET, info=info, size=size, mtime_ns=mtime_ns)


def _decode_row(row: Row, ffmpeg: str, should_continue: Callable[[], bool] | None, shared: int = 1) -> Row:
    """The row with the audio decoded and hashed, or with the status that
    says why it could not be (the status is unchanged on success)."""
    assert row.info is not None  # noqa: S101 (callers pass a header row)
    try:
        md5, samples = decode_md5(row.path, ffmpeg, row.info, should_continue, shared)
    except Cancelled:
        return replace(row, status=CANCELLED)
    except Unsupported as exc:
        return replace(row, status=UNSUPPORTED, detail=str(exc))
    except (DecodeFailed, OSError) as exc:
        return replace(row, status=DECODE_FAILED, detail=str(exc))
    return replace(row, md5_audio=md5, samples_decoded=samples)


def check_file(
    path: str,
    ffmpeg: str | None = None,
    *,
    deep: bool = False,
    should_continue: Callable[[], bool] | None = None,
    shared: int = 1,
) -> Row:
    """Read one file's verdict. Header only by default; ``deep`` decodes the
    audio and compares (``shared``: how many decodes run at once, see
    :func:`decode_md5`). Never writes."""
    row = _read_row(path)
    if row.info is None or not deep or not ffmpeg:
        return row
    row = _decode_row(row, ffmpeg, should_continue, shared)
    if row.md5_audio is None or row.info.md5_unset:
        return row
    if row.md5_audio != row.info.md5:
        return replace(row, status=MISMATCH)
    detail = "" if row.info.total_samples in (0, row.samples_decoded) else "sample count in the header differs"
    return replace(row, status=OK, detail=detail)


def seal_file(
    path: str | os.PathLike,
    ffmpeg: str,
    *,
    expected: StreamInfo | None = None,
    fill_sample_count: bool = False,
    should_continue: Callable[[], bool] | None = None,
) -> Row:
    """Verify one file and fill in an unset signature.

    The only writing entry point. The audio is decoded first; a set
    signature that matches is left alone (``OK``), one that does not is
    reported (``MISMATCH``) and never rewritten; an unset one is written
    together with the sample count (``SEALED``), but only when the audio
    decoded to the length the header states. A shorter or longer decode
    means the file is cut off or damaged: it is reported (``LENGTH_DIFFERS``)
    and left alone, header count included, since that count is what shows
    the damage. A header count of 0 means unknown, and then the file's own
    end has to prove the length (see :func:`proven_total_samples`); when it
    cannot, nothing shows whether a cut on a frame boundary took the end of
    the audio, so the file is reported the same way and never signed (fail
    closed: signing it would make the cut look verified). With
    ``fill_sample_count`` a matching signature beside a wrong
    sample count has the count written too (the download engine's case: the
    file is one it just made, and the matching signature proves the audio
    complete). ``expected`` is the header a scan read earlier; a header that
    no longer matches it is ``CHANGED`` and left alone.
    """
    row = _read_row(os.fspath(path))
    info = row.info
    if info is None:
        return row
    if expected is not None and (info.raw != expected.raw or info.offset != expected.offset):
        return replace(row, status=CHANGED)
    row = _decode_row(row, ffmpeg, should_continue)
    md5, samples = row.md5_audio, row.samples_decoded
    if md5 is None:
        return row
    if not info.md5_unset:
        if md5 != info.md5:
            return replace(row, status=MISMATCH)
        if not fill_sample_count or info.total_samples == samples:
            return replace(row, status=OK)
        detail = "sample count written"
    elif refused := _length_refusal(row.path, info, samples):
        return replace(row, status=LENGTH_DIFFERS, detail=refused)
    else:
        detail = "signature and sample count written" if info.total_samples == 0 else "signature written"
    return _write_row(row, md5, samples, detail)


def _length_refusal(path: str, info: StreamInfo, samples: int) -> str:
    """Why an unset signature must not be written over a decode of
    ``samples`` samples, or "" when it may: the header's count must match
    it, or, when the header has none, the length the end of the file
    proves must (see :func:`proven_total_samples`)."""
    if info.total_samples == 0:
        proven = proven_total_samples(path, info)
        if proven is None:
            return _UNPROVEN
        if proven != samples:
            return f"decoded {samples} samples, the last frame ends at {proven}"
    elif info.total_samples != samples:
        return f"decoded {samples} samples, the header says {info.total_samples}"
    return ""


def _write_row(row: Row, md5: bytes, samples: int, detail: str) -> Row:
    """The write itself, after the decode proved the audio: the file must
    still be the one that was hashed, and the header the one that was read."""
    info = row.info
    assert info is not None  # noqa: S101 (seal_file passes a decoded header row)
    try:
        if _stat(row.path) != (row.size, row.mtime_ns):
            return replace(row, status=CHANGED, detail="modified while it was being decoded")
        write_streaminfo(row.path, info, sealed_bytes(info, md5, samples))
    except HeaderChanged:
        return replace(row, status=CHANGED)
    except OSError as exc:
        return replace(row, status=WRITE_FAILED, detail=exc.strerror or str(exc))
    return replace(row, status=SEALED, detail=detail)


# ---- many files ---------------------------------------------------------------


def effective_roots(roots: Iterable[str]) -> list[str]:
    """The folders to walk: blanks dropped, duplicates and a folder that lies
    inside another dropped (the download folder inside the library is one
    walk, not two). Spelling is kept as given; nothing is stat'ed here."""
    out: list[str] = []
    for root in roots:
        r = (root or "").strip()
        if not r or any(path_under(r, kept) for kept in out):
            continue
        out = [kept for kept in out if not path_under(kept, r)]
        out.append(r)
    return out


def _list_folder(folder: str, real_roots: list[str], unreadable: list[str]) -> tuple[list[str], list[str], list[str]]:
    """One folder's subfolders, ``.flac`` files and ``.flac`` links (only
    those resolving under a root), by the walk's rules. A folder that cannot
    be opened or listed, and an entry whose type cannot be read, goes into
    ``unreadable``; what was listed before a failure is kept."""
    dirs: list[str] = []
    files: list[str] = []
    links: list[str] = []
    try:
        with os.scandir(folder) as it:
            for entry in it:
                name = entry.name
                if name.startswith("."):
                    continue
                try:
                    if entry.is_dir(follow_symlinks=False):
                        dirs.append(entry.path)
                        continue
                    if not name.casefold().endswith(".flac"):
                        continue
                    if entry.is_symlink():
                        real = os.path.realpath(entry.path)
                        if any(path_under(real, rr) for rr in real_roots) and entry.is_file():
                            links.append(entry.path)
                    elif entry.is_file():
                        files.append(entry.path)
                except OSError:
                    unreadable.append(entry.path)
    except OSError:
        unreadable.append(folder)
    return dirs, files, links


def _file_id(path: str) -> tuple[int, int] | None:
    """The file a path leads to (links followed), as (device, inode)."""
    try:
        st = os.stat(path)
    except OSError:
        return None
    return st.st_dev, st.st_ino


# Folders listed at once by the walk, and how far down its stack it lists
# ahead. A network share answers each listing after a round trip (tens of
# milliseconds), so one at a time a large library spends minutes waiting.
_WALK_THREADS = 8
_WALK_AHEAD = 32


def _list_and_stat(
    folder: str, real_roots: list[str], stat: bool
) -> tuple[list[str], list[str], list[str], list[str], dict[str, tuple[int, int]]]:
    """(walk worker) :func:`_list_folder` with its own ``unreadable`` list,
    plus, with ``stat``, each file's size and time taken straight after the
    listing, so a share's round trips for them overlap other folders'."""
    unreadable: list[str] = []
    dirs, files, links = _list_folder(folder, real_roots, unreadable)
    stamps: dict[str, tuple[int, int]] = {}
    if stat:
        for path in files:
            with contextlib.suppress(OSError):
                stamps[path] = _stat(path)
    return dirs, files, links, unreadable, stamps


def iter_flac_files(
    roots: Iterable[str],
    should_continue: Callable[[], bool] | None = None,
    unreadable: list[str] | None = None,
    stamps: dict[str, tuple[int, int]] | None = None,
) -> Iterator[str]:
    """Every ``.flac`` under the roots, sorted within each folder.

    Dot-prefixed names are skipped whatever their type (hidden folders, and
    the AppleDouble ``._Track.flac`` sidecars a share carries). Folder links
    are not followed. A file that is a link is yielded only when it resolves
    to somewhere under one of the roots: the tool's writes must never reach
    a file outside the two folders it was pointed at. Links come last, and
    one that leads to a file the walk already yielded (or another link
    already did) is dropped, so each file is checked and counted once.

    Every folder that could not be opened or listed (a root included: a
    share that is not mounted, a folder the system will not let Waves read)
    is appended to ``unreadable``, so a caller can tell a clean walk from
    one that missed files.

    Folders are listed a few at a time ahead of the one being yielded, in
    the same order a one-at-a-time walk takes. With ``stamps``, each file's
    size and time go there as its folder is listed (a file that cannot be
    stat'ed is left out).
    """
    roots = effective_roots(roots)
    real_roots = [os.path.realpath(r) for r in roots]
    sink = unreadable if unreadable is not None else []
    by_name: dict[str, list[str]] = {}
    links: list[str] = []
    pool = ThreadPoolExecutor(_WALK_THREADS, thread_name_prefix="flac-walk")
    try:
        for root in roots:
            pending = [root]
            listing: dict[str, Future] = {}
            while pending:
                if should_continue is not None and not should_continue():
                    return
                for folder in pending[-_WALK_AHEAD:]:
                    if folder not in listing:
                        listing[folder] = pool.submit(_list_and_stat, folder, real_roots, stamps is not None)
                dirs, files, found, missed, stats = listing.pop(pending.pop()).result()
                sink.extend(missed)
                if stamps is not None:
                    stamps.update(stats)
                pending.extend(sorted(dirs, reverse=True))
                links.extend(sorted(found))
                for path in sorted(files):
                    by_name.setdefault(os.path.basename(path).casefold(), []).append(path)
                    yield path
    finally:
        # A share that stopped answering must not hold up a cancel: listings
        # still running are left to finish on their own.
        pool.shutdown(wait=False, cancel_futures=True)
    yield from _distinct_links(links, by_name, should_continue)


def _distinct_links(
    links: list[str], by_name: dict[str, list[str]], should_continue: Callable[[], bool] | None
) -> Iterator[str]:
    """The walk's links, each only when the walk found no other name for its
    file (``by_name``: the files it yielded, by name). Only files sharing the
    target's name are stat'ed to tell, so a library with no links costs
    nothing extra."""
    seen: set[tuple[int, int]] = set()
    for link in links:
        if should_continue is not None and not should_continue():
            return
        key = _file_id(link)
        if key is not None:
            if key in seen:
                continue
            seen.add(key)
            name = os.path.basename(os.path.realpath(link)).casefold()
            if any(_file_id(p) == key for p in by_name.get(name, ())):
                continue
        yield link


# ---- what earlier scans found -------------------------------------------------

# The file under the config folder that remembers verdicts between runs.
CACHE_NAME = "flac-check.sqlite3"
_CACHE_VERSION = 1
# Commit the remembered verdicts at least this often, so a run that is
# canceled or cut short by a quit still keeps what it read.
_CACHE_COMMIT_ROWS = 500
_CACHE_COMMIT_S = 5.0
# Deep verdicts worth keeping: each says what the audio is. A failed decode is
# never kept (a share that stopped answering fails one too), nor a cancel.
_DEEP_KEPT = frozenset({OK, MISMATCH, UNSUPPORTED, UNSET})


class VerdictCache:
    """What earlier scans found, per file, so scanning a library that has not
    changed reads nothing but each file's size and times.

    A verdict is reused only while the file's size and modification time
    are the ones it was read at. The change time is left out on purpose: a
    share can report it unreliably, which would turn the reuse off for the
    very libraries that need it most, and a tagger that keeps the
    modification time leaves the header and audio alone anyway. A file
    modified within ``SETTLE_SECONDS`` of its read is never remembered: on a
    volume with coarse timestamps a second write in the same tick would leave
    the time as it was.

    Kept per file: the header (the 34 bytes and where they sit, so a fix
    still proves the file is the one the scan saw) and, after a deep check,
    the audio's verdict. Nothing here ever decides a write on its own: a fix
    re-reads, decodes and compares every file it touches (see
    :func:`fix_rows`). A cache that cannot be opened or read is dropped and
    the run carries on reading every file.

    One connection, used by the one thread a scan runs on.
    """

    def __init__(self, path: str, now: Callable[[], float] = time.time) -> None:
        self._now = now
        # Set when a read or write found the file damaged ("malformed", not
        # merely locked): the caller deletes it after close, and the next
        # scan starts it over instead of tripping on it every run.
        self.damaged = False
        self._pending = 0
        self._last_commit = time.monotonic()
        self._conn: sqlite3.Connection | None = sqlite3.connect(path)
        try:
            if self._conn.execute("PRAGMA user_version").fetchone()[0] != _CACHE_VERSION:
                self._conn.execute("DROP TABLE IF EXISTS verdicts")
                self._conn.execute("""CREATE TABLE verdicts (
                           path        TEXT PRIMARY KEY,
                           size        INTEGER NOT NULL,
                           mtime_ns    INTEGER NOT NULL,
                           status      TEXT NOT NULL,
                           detail      TEXT NOT NULL,
                           offset      INTEGER,
                           raw         BLOB,
                           deep_status TEXT,
                           deep_detail TEXT,
                           md5_audio   BLOB,
                           samples     INTEGER
                       )""")
                self._conn.execute(f"PRAGMA user_version = {_CACHE_VERSION}")
                self._conn.commit()
        except sqlite3.Error:
            self._conn.close()
            raise

    def _drop(self, exc: BaseException | None = None) -> None:
        if type(exc) is sqlite3.DatabaseError:
            self.damaged = True
        logger.warning("FLAC check: the remembered verdicts could not be used; every file is read this run")
        if self._conn is not None:
            with contextlib.suppress(sqlite3.Error):
                self._conn.close()
        self._conn = None

    def lookup(self, path: str, deep: bool, reuse: bool = True) -> tuple[Row | None, tuple[int, int] | None]:
        """The remembered verdict for the file as it is now, or None, plus
        the stat it was judged by (None when the file cannot be stat'ed);
        that stat is what :meth:`store` keys a fresh verdict on. Without
        ``reuse`` only the stat is taken: the file is read again, and what
        that read finds replaces what was remembered."""
        try:
            stamp = _stat(path)
        except OSError:
            return None, None
        if self._conn is None or not reuse:
            return None, stamp
        return self.remembered(path, deep, stamp), stamp

    def remembered(self, path: str, deep: bool, stamp: tuple[int, int]) -> Row | None:
        """The verdict remembered for ``path`` at exactly ``stamp`` (its size
        and modification time), or None."""
        if self._conn is None:
            return None
        try:
            rec = self._conn.execute(
                "SELECT size, mtime_ns, status, detail, offset, raw,"
                " deep_status, deep_detail, md5_audio, samples FROM verdicts WHERE path = ?",
                (path,),
            ).fetchone()
        except UnicodeError:
            # A name that is not valid UTF-8 (an old Latin-1 rip on Linux):
            # sqlite cannot hold it, so the file is simply read every scan.
            return None
        except sqlite3.Error as exc:
            self._drop(exc)
            return None
        if rec is None or tuple(rec[:2]) != stamp:
            return None
        status, detail, offset, raw, deep_status, deep_detail, md5_audio, samples = rec[2:]
        size, mtime_ns = stamp
        if status == UNREADABLE:
            return Row(path, UNREADABLE, detail, size=size, mtime_ns=mtime_ns, cached=True)
        try:
            info = _parse_streaminfo(int(offset), bytes(raw))
        except (FlacHeaderError, TypeError, ValueError):
            return None
        if not deep:
            status = UNSET if info.md5_unset else SET
            return Row(path, status, info=info, size=size, mtime_ns=mtime_ns, cached=True)
        if deep_status is None:
            return None
        return Row(
            path,
            deep_status,
            deep_detail or "",
            info=info,
            size=size,
            mtime_ns=mtime_ns,
            md5_audio=bytes(md5_audio) if md5_audio is not None else None,
            samples_decoded=samples if samples is not None else -1,
            cached=True,
        )

    def store(self, row: Row, stamp: tuple[int, int] | None, deep: bool) -> None:
        """Remember a verdict read just now against ``stamp``, the stat taken
        before the read. Skipped when the read saw another size or time (the
        file changed in between) or the file changed too recently to trust."""
        if self._conn is None or stamp is None or (row.size, row.mtime_ns) != stamp:
            return
        if self._now() - stamp[1] / 1e9 < SETTLE_SECONDS:
            return
        info = row.info
        if info is None:
            if row.status != UNREADABLE:
                return
            header = (UNREADABLE, row.detail, None, None)
        else:
            header = (UNSET if info.md5_unset else SET, "", info.offset, info.raw)
        audio = (None, None, None, None)
        if deep and info is not None and row.status in _DEEP_KEPT and (row.status != UNSET or row.md5_audio):
            audio = (row.status, row.detail, row.md5_audio, row.samples_decoded if row.md5_audio else None)
        try:
            self._conn.execute(
                "INSERT OR REPLACE INTO verdicts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (row.path, *stamp, *header, *audio),
            )
            self._pending += 1
            if self._pending >= _CACHE_COMMIT_ROWS or time.monotonic() - self._last_commit >= _CACHE_COMMIT_S:
                self.flush()
        except UnicodeError:
            return  # not valid UTF-8, see remembered
        except sqlite3.Error as exc:
            self._drop(exc)

    def prune(self, roots: Iterable[str], seen: Iterable[str], unreadable: Iterable[str] = ()) -> None:
        """Forget files under ``roots`` that a finished walk no longer found
        (deleted or renamed), except under a folder it could not read."""
        if self._conn is None:
            return
        roots = effective_roots(roots)
        found = set(seen)
        missed = list(unreadable)
        try:
            gone = [
                (p,)
                for (p,) in self._conn.execute("SELECT path FROM verdicts")
                if p not in found and any(path_under(p, r) for r in roots) and not any(path_under(p, u) for u in missed)
            ]
            if gone:
                self._conn.executemany("DELETE FROM verdicts WHERE path = ?", gone)
                self._pending += len(gone)
        except sqlite3.Error as exc:
            self._drop(exc)

    def flush(self) -> None:
        if self._conn is None:
            return
        try:
            self._conn.commit()
        except sqlite3.Error as exc:
            self._drop(exc)
            return
        self._pending = 0
        self._last_commit = time.monotonic()

    def close(self) -> None:
        """Commit what is pending and close."""
        self.flush()
        if self._conn is not None:
            with contextlib.suppress(sqlite3.Error):
                self._conn.close()
            self._conn = None


def _walk(
    roots: Iterable[str],
    should_continue: Callable[[], bool] | None,
    on_walk: Callable[[int, bool], None] | None,
    unreadable: list[str],
    stamps: dict[str, tuple[int, int]] | None = None,
) -> list[str]:
    """Every file :func:`scan` checks, with ``on_walk`` told as they are found.
    With ``stamps``, each file's size and time are taken the moment its
    folder has been listed and kept there by path (see :func:`scan`)."""
    paths: list[str] = []
    for path in iter_flac_files(roots, should_continue, unreadable, stamps):
        paths.append(path)
        if on_walk is not None:
            on_walk(len(paths), False)
    if on_walk is not None:
        on_walk(len(paths), True)
    return paths


# Files read at once by a scan, a header scan and a deep check. Over a network
# share each header read waits on round trips, so one at a time a large
# library takes most of an hour; a decode also keeps a core busy, so fewer.
_READ_THREADS = 8
_DECODE_THREADS = max(1, min(4, (os.cpu_count() or 2) // 2))


def _read_fresh(
    path: str, ffmpeg: str | None, deep: bool, should_continue: Callable[[], bool] | None, stat: bool, shared: int
) -> tuple[Row, tuple[int, int] | None]:
    """(read worker) A fresh verdict, plus with ``stat`` the size and time
    taken just before the read (what :meth:`VerdictCache.store` keys it on)."""
    stamp = None
    if stat:
        with contextlib.suppress(OSError):
            stamp = _stat(path)
    return check_file(path, ffmpeg, deep=deep, should_continue=should_continue, shared=shared), stamp


def _verdicts(
    paths: list[str],
    ffmpeg: str | None,
    deep: bool,
    cache: VerdictCache | None,
    should_continue: Callable[[], bool] | None,
    stamps: dict[str, tuple[int, int]] | None,
) -> Iterator[Row]:
    """Each file's verdict for :func:`scan`, in ``paths`` order: the
    remembered one while the file is unchanged since the walk's stat
    (``stamps``; None for a recheck), else a fresh read. Reads run a few at
    a time ahead of the row being handed out; the cache is asked and told
    on this thread only, and a fresh verdict is remembered unless the run
    was stopped."""
    workers = _DECODE_THREADS if deep else _READ_THREADS
    ahead = workers * 4
    pending: deque[tuple[str, Row | Future]] = deque()
    todo = iter(paths)
    pool = ThreadPoolExecutor(workers, thread_name_prefix="flac-read")

    def alive() -> bool:
        return should_continue is None or should_continue()

    def fill() -> None:
        while len(pending) < ahead and alive():
            path = next(todo, None)
            if path is None:
                return
            seen = stamps.get(path) if stamps is not None else None
            row = cache.remembered(path, deep, seen) if cache is not None and seen is not None else None
            if row is None:
                row = pool.submit(_read_fresh, path, ffmpeg, deep, should_continue, cache is not None, workers)
            pending.append((path, row))

    try:
        fill()
        while pending:
            _path, item = pending.popleft()
            if isinstance(item, Row):
                yield item
            else:
                row, stamp = item.result()
                if cache is not None and row.status != CANCELLED and alive():
                    cache.store(row, stamp, deep)
                yield row
            fill()
    finally:
        # Reads still running finish on their own (a cancel stops a decode).
        pool.shutdown(wait=False, cancel_futures=True)


def scan(
    roots: Iterable[str],
    ffmpeg: str | None = None,
    *,
    deep: bool = False,
    should_continue: Callable[[], bool] | None = None,
    on_row: Callable[[Row], None] | None = None,
    on_walk: Callable[[int, bool], None] | None = None,
    unreadable: list[str] | None = None,
    cache: VerdictCache | None = None,
    recheck: bool = False,
) -> list[Row]:
    """Check every FLAC under the roots. Never writes a music file.

    The folders are walked first and the files checked after, so the run
    knows its total before the first file is read: ``on_walk(found, done)``
    is called for each file found, then once with ``done`` set. Folders the
    walk could not open or list are appended to ``unreadable`` (see
    :func:`iter_flac_files`).

    With a ``cache``, a file unchanged since an earlier scan takes that
    scan's verdict (the row is marked ``cached``) and each fresh verdict is
    remembered; a walk that finished forgets the files it no longer found.
    ``recheck`` reads every file again (damage that left a file's size and
    time alone shows only that way) and remembers what it finds. The caller
    closes the cache.

    Whether a file is unchanged is judged on the size and time the walk's
    workers take as they list its folder, several folders at once: on a
    network share every stat is a round trip, and one at a time once the
    walk is over they take minutes on a large library. Files read afresh
    are read several at a time too (see :func:`_verdicts`). A file changed
    after its folder was listed is caught by the next scan, and a fix reads
    every file again before it writes, whatever a scan remembered."""
    if unreadable is None:
        unreadable = []
    deep = deep and bool(ffmpeg)
    stamps: dict[str, tuple[int, int]] | None = {} if cache is not None and not recheck else None
    paths = _walk(roots, should_continue, on_walk, unreadable, stamps)

    def alive() -> bool:
        return should_continue is None or should_continue()

    rows: list[Row] = []
    for row in _verdicts(paths, ffmpeg, deep, cache, should_continue, stamps):
        if not alive():
            break
        rows.append(row)
        if on_row is not None:
            on_row(row)
        if row.status == CANCELLED:
            break
    if cache is not None and len(rows) == len(paths) and alive():
        cache.prune(roots, paths, unreadable)
    return rows


def fix_rows(
    rows: Iterable[Row],
    ffmpeg: str,
    *,
    should_continue: Callable[[], bool] | None = None,
    on_row: Callable[[Row], None] | None = None,
    settle_seconds: float = SETTLE_SECONDS,
    now: Callable[[], float] = time.time,
) -> list[Row]:
    """Seal the ``UNSET`` rows of a scan; every other row passes through
    untouched. Each candidate is re-read, re-stat'ed and decoded before its
    header is written (see :func:`seal_file`), and one written less than
    ``settle_seconds`` ago is left for the next run. A cancel leaves the
    rows not reached, and the one it interrupted mid-decode, ``UNSET`` (with
    a note), so the next fix still sees them."""
    out: list[Row] = []
    for row in rows:
        if row.status != UNSET or row.info is None:
            out.append(row)
            continue
        if should_continue is not None and not should_continue():
            # Still UNSET: nothing was done to it, so a later fix picks it up.
            row = replace(row, detail="not reached, the run was canceled")
        else:
            try:
                size, mtime_ns = _stat(row.path)
            except OSError as exc:
                row = replace(row, status=UNREADABLE, detail=exc.strerror or str(exc))
            else:
                if (size, mtime_ns) != (row.size, row.mtime_ns):
                    row = replace(row, status=CHANGED)
                elif now() - mtime_ns / 1e9 < settle_seconds:
                    row = replace(row, status=BUSY)
                else:
                    sealed = seal_file(row.path, ffmpeg, expected=row.info, should_continue=should_continue)
                    # Canceled mid-decode: nothing was written, so it stays
                    # UNSET for the next fix like the rows after it.
                    if sealed.status == CANCELLED:
                        row = replace(row, detail="not reached, the run was canceled")
                    else:
                        row = sealed
        out.append(row)
        if on_row is not None:
            on_row(row)
    return out


def summarize(rows: Iterable[Row]) -> dict[str, int]:
    """Counts per status, plus ``files`` for the total."""
    counts: dict[str, int] = {"files": 0}
    for row in rows:
        counts["files"] += 1
        counts[row.status] = counts.get(row.status, 0) + 1
    return counts


# ---- the report ---------------------------------------------------------------


def write_report(
    path: str | os.PathLike,
    rows: Iterable[Row],
    unreadable: Iterable[str] = (),
    *,
    exclusive: bool = False,
) -> str:
    """Write the rows as a CSV (UTF-8 with a byte-order mark, so a
    spreadsheet opens accented file names correctly), then a line for each
    folder in ``unreadable`` (``FOLDER_UNREADABLE``: its files were never
    checked). Returns the path. ``exclusive`` refuses to replace a file that
    is already there (:class:`FileExistsError`)."""
    out = pathlib.Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    # backslashreplace: a file name that is not valid UTF-8 is written as
    # escapes instead of failing the whole report.
    with out.open("x" if exclusive else "w", encoding="utf-8-sig", errors="backslashreplace", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(REPORT_COLUMNS)
        for row in rows:
            info = row.info
            writer.writerow(
                [
                    row.status,
                    STATUS_LABELS.get(row.status, row.status),
                    row.path,
                    row.detail,
                    info.bits_per_sample if info else "",
                    info.sample_rate if info else "",
                    info.channels if info else "",
                    info.total_samples if info else "",
                    row.samples_decoded if row.samples_decoded >= 0 else "",
                    "" if info is None or info.md5_unset else info.md5.hex(),
                    row.md5_audio.hex() if row.md5_audio else "",
                    "yes" if row.cached else "",
                ]
            )
        for folder in unreadable:
            writer.writerow(
                [FOLDER_UNREADABLE, STATUS_LABELS[FOLDER_UNREADABLE], folder] + [""] * (len(REPORT_COLUMNS) - 3)
            )
    return str(out)


# Reports written in the same second are told apart by a counter, up to this.
_REPORT_SUFFIX_MAX = 999


def report_name(kind: str, when: float | None = None, n: int = 1) -> str:
    """``flac-check-<kind>-YYYYMMDD-HHMMSS.csv`` (kind is ``scan`` or
    ``fix``); a second report in the same second is ``...-HHMMSS-2.csv``,
    and so on (``n``)."""
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(when))
    suffix = f"-{n}" if n > 1 else ""
    return f"flac-check-{kind}-{stamp}{suffix}.csv"


_REPORT_RE = re.compile(r"flac-check-(?:scan|fix)-(\d{8}-\d{6})(?:-(\d+))?\.csv")


def _report_names(folder: str | os.PathLike) -> list[str]:
    try:
        return [n for n in os.listdir(folder) if _REPORT_RE.fullmatch(n)]
    except OSError:
        return []


def has_report(folder: str | os.PathLike) -> bool:
    """Whether the folder holds any report (one listing, no stat)."""
    return bool(_report_names(folder))


def newest_report(folder: str | os.PathLike) -> str:
    """The report written last, by its modification time (the local time in
    its name can run backwards when the clock falls back or the time zone
    changes), then the time and counter in its name; "" when there is none."""
    best: tuple[int, str, int] | None = None
    newest = ""
    for name in _report_names(folder):
        try:
            written = os.stat(os.path.join(folder, name)).st_mtime_ns
        except OSError:
            continue
        m = _REPORT_RE.fullmatch(name)
        assert m is not None  # noqa: S101 (_report_names matched it)
        key = (written, m.group(1), int(m.group(2) or 1))
        if best is None or key > best:
            best, newest = key, name
    return os.path.join(folder, newest) if newest else ""


def write_new_report(
    folder: str | os.PathLike,
    kind: str,
    rows: Iterable[Row],
    unreadable: Iterable[str] = (),
    when: float | None = None,
) -> str:
    """Write a report under a name no earlier report has (see
    :func:`report_name`): one never replaces another. Returns the path."""
    when = time.time() if when is None else when
    rows = list(rows)
    unreadable = list(unreadable)
    for n in range(1, _REPORT_SUFFIX_MAX + 1):
        with contextlib.suppress(FileExistsError):
            return write_report(os.path.join(folder, report_name(kind, when, n)), rows, unreadable, exclusive=True)
    raise FileExistsError(os.path.join(folder, report_name(kind, when, _REPORT_SUFFIX_MAX)))
