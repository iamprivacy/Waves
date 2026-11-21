"""Rewrite a cover as baseline JPEG for players that cannot decode progressive.

TIDAL serves every resized cover as a progressive JPEG (several scans). Small
decoders, Rockbox among them, read only the plain kind: one frame, 8 bits a
sample, Huffman coded, a single scan carrying every component. This module
turns the first kind into the second with the JPEG code Qt already ships, so
the opt-in costs nothing for anyone who leaves it off.

The contract is narrow on purpose. Only bytes that parse as a complete JPEG
and are not already baseline are touched; everything else goes back as it
came, and so does the input whenever the rewrite fails or its result does not
check out. The option can therefore never leave a worse cover than "off".

Nothing here imports Qt at module level: the marker walk is plain Python, and
PySide6 loads inside the one function that needs it.
"""

from __future__ import annotations

from typing import NamedTuple

# Quality 85 is what the covers are served at, so a rewrite at 85 reuses the
# same quantization tables and loses next to nothing. Qt's writer stores color
# at half resolution (4:2:0) up to quality 90 and at full resolution (4:4:4)
# from 91, with no other control over it, so keeping full-resolution color
# means writing at a quality past that line.
_QUALITY_SAME_TABLES = 85
_QUALITY_FULL_COLOR = 95

_SOF_BASELINE = 0xC0
# Every start-of-frame marker: C0..CF without DHT (C4), JPG (C8) and DAC (CC).
_SOF_MARKERS = frozenset(range(0xC0, 0xD0)) - {0xC4, 0xC8, 0xCC}
# Markers that stand alone, with no length field after them.
_STANDALONE = frozenset(range(0xD0, 0xD8)) | {0x01}


class _Malformed(ValueError):
    """A JPEG header that cannot be read; scan() answers None for it."""


class JpegInfo(NamedTuple):
    sof: int  # the start-of-frame marker byte (0xC0 baseline, 0xC2 progressive)
    precision: int  # bits per sample
    components: int
    luma_sampling: tuple[int, int]  # (horizontal, vertical) of the first component
    scans: int
    complete: bool  # the data runs to an end-of-image marker


class Result(NamedTuple):
    data: bytes
    # "converted", or why the input came back untouched: "not-jpeg",
    # "already-baseline", "truncated", "unknown-mode", "decode-failed",
    # "write-failed", "bad-output", "error".
    outcome: str


def scan(data) -> JpegInfo | None:
    """Walk the JPEG markers of ``data``. None when it is not a JPEG whose
    frame header can be read; never raises."""
    try:
        return _scan(data)
    except Exception:
        return None


def _scan(data) -> JpegInfo | None:
    """The marker walk behind scan(). A malformed header raises _Malformed,
    which scan() turns into None."""
    if not isinstance(data, bytes | bytearray) or len(data) < 4 or data[0] != 0xFF or data[1] != 0xD8:
        return None
    frame = None
    scans = 0
    complete = False
    i = 2
    while i + 1 < len(data):
        if data[i] != 0xFF:
            raise _Malformed
        marker = data[i + 1]
        if marker == 0xD9:
            complete = True
            break
        if marker == 0xFF or marker in _STANDALONE:  # a fill byte, or no length field
            i += 1 if marker == 0xFF else 2
            continue
        length = _segment_length(data, i)
        if length is None:
            break
        if marker in _SOF_MARKERS:
            frame = _read_frame(data, i, marker, frame)
        i += 2 + length
        if marker == 0xDA:
            scans += 1
            i = _skip_entropy(data, i)
    return JpegInfo(*frame, scans, complete) if frame else None


def _segment_length(data, i: int) -> int | None:
    """Length field of the segment whose marker is at ``i``; None when the
    data stops before it."""
    if i + 3 >= len(data):
        return None
    length = (data[i + 2] << 8) | data[i + 3]
    if length < 2:
        raise _Malformed
    return length


def _read_frame(data, i: int, marker: int, seen) -> tuple:
    """(sof, precision, components, luma sampling) of the frame header at ``i``."""
    if seen is not None:
        raise _Malformed
    if i + 11 >= len(data) or data[i + 9] < 1:
        raise _Malformed
    return marker, data[i + 4], data[i + 9], (data[i + 11] >> 4, data[i + 11] & 0x0F)


def _skip_entropy(data, i: int) -> int:
    """Index of the first real marker after the entropy-coded data at ``i``:
    a stuffed zero (FF 00) and the restart markers belong to the scan.
    bytes.find keeps this off the per-byte Python path, since an original
    size cover runs to megabytes."""
    size = len(data)
    while True:
        found = data.find(b"\xff", i)
        if found < 0 or found + 1 >= size:
            return size
        after = data[found + 1]
        if after == 0x00 or 0xD0 <= after <= 0xD7:
            i = found + 2
        elif after == 0xFF:
            i = found + 1
        else:
            return found


def is_baseline(info: JpegInfo | None) -> bool:
    """One complete baseline frame in a single scan: what a small decoder reads."""
    return (
        info is not None
        and info.sof == _SOF_BASELINE
        and info.precision == 8
        and info.components in (1, 3)
        and info.scans == 1
        and info.complete
    )


def to_baseline(data, mode: str) -> Result:
    """``data`` as a baseline JPEG, or ``data`` itself with the reason why not.

    ``mode`` is "small" (color at half resolution, about the size of the
    source or smaller) or "faithful" (full-resolution color kept where the
    source has it, at a larger size). Never raises.
    """
    try:
        return _to_baseline(data, str(mode))
    except Exception:
        return Result(data, "error")


def _to_baseline(data, mode: str) -> Result:
    if mode not in ("small", "faithful"):
        return Result(data, "unknown-mode")
    info = scan(data)
    if info is None:
        return Result(data, "not-jpeg")
    if is_baseline(info):
        return Result(data, "already-baseline")
    if not info.complete:
        # Qt decodes a cut-off file into a partly gray picture without a
        # word; rewriting that would hide the damage behind a valid file.
        return Result(data, "truncated")
    full_color = mode == "faithful" and info.luma_sampling == (1, 1) and info.components == 3
    out, outcome = _rewrite(bytes(data), _QUALITY_FULL_COLOR if full_color else _QUALITY_SAME_TABLES)
    if out is None:
        return Result(data, outcome)
    if not is_baseline(scan(out)):
        return Result(data, "bad-output")
    return Result(out, "converted")


def _rewrite(data: bytes, quality: int) -> tuple[bytes | None, str]:
    """Decode ``data`` with Qt and write it back as a single-scan JPEG."""
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice
    from PySide6.QtGui import QImage, QImageReader, QImageWriter

    source = QBuffer()
    source.setData(QByteArray(data))
    if not source.open(QIODevice.OpenModeFlag.ReadOnly):
        return None, "decode-failed"
    reader = QImageReader(source, b"jpeg")
    reader.setAutoTransform(True)
    image = reader.read()
    if image.isNull() or image.width() < 1 or image.height() < 1:
        return None, "decode-failed"
    image = image.convertToFormat(
        QImage.Format.Format_Grayscale8 if image.isGrayscale() else QImage.Format.Format_RGB888
    )
    target = QBuffer()
    if image.isNull() or not target.open(QIODevice.OpenModeFlag.WriteOnly):
        return None, "write-failed"
    writer = QImageWriter(target, b"jpeg")
    writer.setQuality(quality)
    writer.setProgressiveScanWrite(False)
    writer.setOptimizedWrite(False)
    if not writer.write(image):
        return None, "write-failed"
    return bytes(target.data().data()), "converted"
