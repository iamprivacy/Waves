"""Spectrum levels for the logo equalizer (Settings > Appearance).

While a preview plays, the logo in the top bar moves with the music. A
``QAudioBufferOutput`` on the one preview player hands over the decoded audio
as it plays; ``Levels`` turns the slice under the play head into 16 band
levels, once per tick of the logo's own 20 Hz clock. Pure Python: the app
ships without numpy, and one tick costs about 2 ms.

The tap is attached the first time a preview plays, never at launch, and
``LogoEq.levels`` answers with an empty list until audio really arrives, so
where the tap does not work the logo stays as it is.
"""

from __future__ import annotations

import cmath
import logging
import math
import time
from array import array
from collections import deque

log = logging.getLogger("waves.logoeq")

N = 2048  # window: 46 ms at 44.1 kHz, about 21 Hz per bin
BANDS = 16
LO_HZ, HI_HZ = 50.0, 14000.0
RANGE_DB = 42.0  # a band this far under the loudest recent one reads 0
KEEP_S = 1.5  # how much decoded audio is kept around the play head
FRESH_S = 0.5  # no buffer for this long while playing = no audio to show

_HANN = [0.5 - 0.5 * math.cos(2 * math.pi * i / (N - 1)) for i in range(N)]
_BITS = N.bit_length() - 1
_REV = [int(f"{i:0{_BITS}b}"[::-1], 2) for i in range(N)]
_TW = [cmath.exp(-2j * math.pi * k / N) for k in range(N // 2)]
_EDGES = [LO_HZ * (HI_HZ / LO_HZ) ** (i / BANDS) for i in range(BANDS + 1)]
# +3 dB per octave so cymbals register next to the kick
_TILT = [3.0 * math.log2(_EDGES[i] / LO_HZ) for i in range(BANDS)]
_TYPECODES = {"Int16": ("h", 32768.0), "Int32": ("i", 2147483648.0), "Float": ("f", 1.0)}


def fft(x: list) -> list:
    """Iterative radix-2 FFT of a length-N real list."""
    a = [complex(x[r]) for r in _REV]
    size = 2
    while size <= N:
        half, step = size // 2, N // size
        for start in range(0, N, size):
            for k in range(half):
                t = _TW[k * step] * a[start + k + half]
                u = a[start + k]
                a[start + k] = u + t
                a[start + k + half] = u - t
        size *= 2
    return a


def to_samples(raw: bytes, fmt: str) -> tuple[array, float]:
    """Interleaved PCM as a typed array plus its full-scale value. No
    per-sample work here: only the window under the play head is ever read."""
    if fmt in _TYPECODES:
        code, scale = _TYPECODES[fmt]
        usable = len(raw) - len(raw) % array(code).itemsize
        return array(code, raw[:usable]), scale
    return array("f", (b - 128 for b in raw)), 128.0  # UInt8


class Levels:
    """Decoded buffers in, smoothed band levels out."""

    def __init__(self):
        # (start_us, rate, channels, scale, interleaved samples), the last KEEP_S of audio
        self.bufs: deque = deque()
        self.levels = [0.0] * BANDS
        self._ref_db = -20.0  # loudest recent band, follows the song

    def reset(self):
        self.bufs.clear()
        self._ref_db = -20.0  # a quiet song after a loud one fills the logo at once

    def feed(self, start_us: int, rate: int, channels: int, scale: float, samples: array):
        if rate <= 0 or channels <= 0:
            return
        if self.bufs and (start_us < self.bufs[-1][0] or rate != self.bufs[-1][1] or channels != self.bufs[-1][2]):
            self.bufs.clear()  # a seek or a new song
        self.bufs.append((start_us, rate, channels, scale, samples))
        # By time, not by count: a codec's buffers can be 4096 frames or 1024,
        # and they arrive ahead of the play head, which must stay inside them.
        while len(self.bufs) > 1 and start_us - self.bufs[0][0] > KEEP_S * 1e6:
            self.bufs.popleft()

    def _window(self, pos_us: int):
        """The N mono samples ending at the play head. A stream's clock can
        sit outside what is buffered; then the newest N samples stand in."""
        if not self.bufs:
            return None, 0
        _, rate, ch, scale, _ = self.bufs[-1]
        first = self.bufs[0][0]
        total = sum(len(b[4]) for b in self.bufs) // ch
        end = int((pos_us - first) * rate / 1e6)
        if end < N or end > total:
            end = total
        if end < N:
            return None, rate
        cut, offset = self.bufs[0][4][:0], 0
        for b in self.bufs:  # copy only the part of each buffer the window covers
            n = len(b[4]) // ch
            lo, hi = max(end - N, offset), min(end, offset + n)
            if lo < hi:
                cut.extend(b[4][(lo - offset) * ch : (hi - offset) * ch])
            offset += n
        if ch == 1:
            return [v / scale for v in cut], rate
        k = ch * scale
        return [sum(f) / k for f in zip(*(cut[c::ch] for c in range(ch)), strict=False)], rate

    def frame(self, pos_us: int, live: bool) -> list:
        """Advance one 20 Hz tick: instant attack, each level then falls by a fifth per tick."""
        win, rate = self._window(pos_us) if live else (None, 0)
        if win is None:
            self.levels = [v * 0.78 if v > 0.004 else 0.0 for v in self.levels]
            return self.levels
        mags = [abs(c) for c in fft([v * w for v, w in zip(win, _HANN, strict=False)])[: N // 2]]
        bw = rate / N
        dbs = []
        for i in range(BANDS):
            a = min(N // 2 - 1, int(_EDGES[i] / bw))
            b = min(N // 2, max(a + 1, int(_EDGES[i + 1] / bw)))
            dbs.append(20 * math.log10(max(mags[a:b]) * 4 / N + 1e-9) + _TILT[i])
        top = max(dbs)
        self._ref_db = top if top > self._ref_db else self._ref_db - 0.15
        floor = self._ref_db - RANGE_DB
        new = [min(1.0, max(0.0, (d - floor) / RANGE_DB)) ** 1.6 for d in dbs]
        self.levels = [max(n, o * 0.80) for n, o in zip(new, self.levels, strict=False)]
        return self.levels


class LogoEq:
    """The tap on the preview player. Qt is imported on attach, so the
    module's math stays importable (and testable) without it."""

    def __init__(self):
        self.lv = Levels()
        self.player = None
        self.tap = None
        self._last_feed = 0.0
        self._last_read = 0.0
        self._format_logged = False
        self._failed = False

    def attach(self, player) -> bool:
        """Tap ``player`` (a QMediaPlayer). Safe to call again; False when the tap cannot be made."""
        if self._failed:
            return False
        if self.player is player and self.tap is not None:
            return True
        why = "not a media player"
        try:
            from PySide6.QtMultimedia import QAudioBufferOutput, QMediaPlayer

            if isinstance(player, QMediaPlayer):
                tap = QAudioBufferOutput(player)
                tap.audioBufferReceived.connect(self._buffer)
                player.setAudioBufferOutput(tap)
                player.sourceChanged.connect(self._source_changed)
                self.tap, self.player, why = tap, player, ""
        except Exception as exc:  # a missing backend feature must never break playback
            why = type(exc).__name__
        if why:
            self._failed = True
            self.tap = self.player = None
            log.warning("logo equalizer: audio tap unavailable (%s)", why)
            return False
        log.info("logo equalizer: audio tap attached")
        return True

    def _source_changed(self, *_):
        self.lv.reset()

    def _buffer(self, buf):
        # Nobody is asking for levels (the look is Off, the window is off
        # screen, or the tap stopped): skip the copy, and drop the audio kept
        # so far, which would sit next to the next buffer with a gap between.
        if self._failed or time.monotonic() - self._last_read > FRESH_S:
            self.lv.bufs.clear()
            return
        try:
            data = buf.constData()
            if data is None or not buf.isValid():  # an empty buffer around a seek
                return
            f = buf.format()
            fmt = f.sampleFormat().name
            samples, scale = to_samples(bytes(data), fmt)
            self.lv.feed(buf.startTime(), f.sampleRate(), f.channelCount(), scale, samples)
            self._last_feed = time.monotonic()
            if not self._format_logged:
                self._format_logged = True
                log.info("logo equalizer: first audio, %d Hz, %d ch, %s", f.sampleRate(), f.channelCount(), fmt)
        except Exception as exc:
            if not self._failed:
                self._failed = True
                log.warning("logo equalizer: audio tap stopped (%s)", type(exc).__name__)

    def levels(self) -> list:
        """This tick's band levels; an empty list when there is nothing to
        show (no tap, no audio arriving), so the logo keeps its normal look."""
        if self.player is None or self._failed:
            return []
        self._last_read = time.monotonic()
        try:
            from PySide6.QtMultimedia import QMediaPlayer

            playing = self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
            live = playing and time.monotonic() - self._last_feed < FRESH_S
            out = self.lv.frame(self.player.position() * 1000, live)
        except Exception as exc:
            self._failed = True
            log.warning("logo equalizer: stopped (%s)", type(exc).__name__)
            return []
        return out if live or any(out) else []
