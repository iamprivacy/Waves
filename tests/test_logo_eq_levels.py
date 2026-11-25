"""The logo equalizer's spectrum math (waves/waves_ui/logo_eq.py).

While a preview plays, the logo in the top bar moves with the music. These
pin what makes that honest: a low tone lights a low band and a high tone a
high one, the slice read is the one under the play head, a stream whose
clock sits outside the buffered audio still shows the newest sound, levels
fall back to nothing when the audio stops, and with no tap there is nothing
to show, so the logo keeps its normal look.
"""

from __future__ import annotations

import math
import time
from array import array

from waves.waves_ui import logo_eq
from waves.waves_ui.logo_eq import BANDS, Levels, LogoEq, N, to_samples

RATE = 44100


def _tone(hz: float, frames: int, channels: int = 2, amp: float = 0.5) -> array:
    out = array("h")
    for i in range(frames):
        v = int(amp * 32767 * math.sin(2 * math.pi * hz * i / RATE))
        out.extend([v] * channels)
    return out


def _settled(lv: Levels, pos_us: int) -> list:
    for _ in range(3):
        lv.frame(pos_us, True)
    return lv.levels


def test_a_low_tone_lights_a_low_band_and_a_high_tone_a_high_one():
    lo, hi = Levels(), Levels()
    lo.feed(0, RATE, 2, 32768.0, _tone(100, 8192))
    hi.feed(0, RATE, 2, 32768.0, _tone(5000, 8192))
    low, high = _settled(lo, 150_000), _settled(hi, 150_000)
    assert len(low) == BANDS
    assert low.index(max(low)) <= 3
    assert high.index(max(high)) >= 11


def test_the_window_is_the_audio_under_the_play_head():
    # 4096 frames of a low tone, then 4096 of a high one, in separate buffers.
    lv = Levels()
    lv.feed(0, RATE, 2, 32768.0, _tone(100, 4096))
    lv.feed(int(4096 / RATE * 1e6), RATE, 2, 32768.0, _tone(5000, 4096))
    early = list(_settled(lv, int(3000 / RATE * 1e6)))
    assert early.index(max(early)) <= 3
    late = Levels()
    late.bufs = lv.bufs
    after = _settled(late, int(8000 / RATE * 1e6))
    assert after.index(max(after)) >= 11


def test_a_window_can_span_two_buffers():
    lv = Levels()
    lv.feed(0, RATE, 1, 32768.0, _tone(5000, 1500, channels=1))
    lv.feed(int(1500 / RATE * 1e6), RATE, 1, 32768.0, _tone(5000, 1500, channels=1))
    win, rate = lv._window(int(2600 / RATE * 1e6))
    assert rate == RATE
    assert len(win) == N


def test_a_play_head_outside_the_buffered_audio_shows_the_newest_sound():
    lv = Levels()
    lv.feed(90_000_000, RATE, 2, 32768.0, _tone(5000, 8192))
    out = _settled(lv, 5_000_000)  # the stream's clock is nowhere near the buffer's
    assert out.index(max(out)) >= 11


def test_small_buffers_far_ahead_of_the_play_head_still_read_under_it():
    # A stream codec hands over 1024-frame buffers and runs 300 ms ahead. The
    # sound under the play head is a low tone; what has just arrived is high.
    lv = Levels()
    switch = int(0.5 * RATE) // 1024  # buffers of low tone, then high from 0.5 s on
    for i in range(int(0.8 * RATE) // 1024):
        lv.feed(int(i * 1024 / RATE * 1e6), RATE, 2, 32768.0, _tone(100 if i < switch else 5000, 1024))
    out = _settled(lv, 400_000)  # the play head, 400 ms behind the newest buffer
    assert out.index(max(out)) <= 3, "read the newest audio instead of the audio being heard"


def test_audio_is_kept_by_time_not_by_count():
    lv = Levels()
    for i in range(400):  # 9 s of 1024-frame buffers
        lv.feed(int(i * 1024 / RATE * 1e6), RATE, 2, 32768.0, array("h", [0]) * 2048)
    span = (lv.bufs[-1][0] - lv.bufs[0][0]) / 1e6
    assert 1.0 < span <= 1.6, span


def test_levels_fall_to_nothing_when_the_audio_stops():
    lv = Levels()
    lv.feed(0, RATE, 2, 32768.0, _tone(1000, 8192))
    assert max(_settled(lv, 150_000)) > 0.5
    for _ in range(60):
        lv.frame(150_000, False)
    assert not any(lv.levels)


def test_a_seek_backward_drops_the_old_audio():
    lv = Levels()
    lv.feed(5_000_000, RATE, 2, 32768.0, _tone(100, 4096))
    lv.feed(1_000_000, RATE, 2, 32768.0, _tone(5000, 4096))
    assert len(lv.bufs) == 1


def test_sample_formats():
    s, scale = to_samples(array("h", [32767, -32768]).tobytes(), "Int16")
    assert (s[0] / scale, s[1] / scale) == (32767 / 32768, -1.0)
    s, scale = to_samples(array("f", [0.5]).tobytes(), "Float")
    assert s[0] / scale == 0.5
    s, scale = to_samples(bytes([128, 255]), "UInt8")
    assert s[0] / scale == 0.0
    # a ragged tail (a torn buffer) is dropped, never an error
    assert len(to_samples(b"\x00\x01\x02", "Int16")[0]) == 1


def test_with_no_tap_there_is_nothing_to_show():
    eq = LogoEq()
    assert eq.levels() == []
    assert eq.attach(object()) is False  # not a media player: no tap, and no second try
    assert eq.levels() == []


def test_one_tick_is_cheap():
    lv = Levels()
    lv.feed(0, RATE, 2, 32768.0, _tone(440, 8192))
    lv.frame(150_000, True)
    t = time.perf_counter()
    for _ in range(10):
        lv.frame(150_000, True)
    per_tick = (time.perf_counter() - t) / 10
    # 20 ticks a second on the GUI thread: a generous ceiling, the real cost is about 2 ms
    assert per_tick < 0.02, f"{per_tick * 1000:.1f} ms"
    print(f"tick {per_tick * 1000:.2f} ms", logo_eq.N)


def test_a_quiet_song_after_a_loud_one_fills_the_logo_at_once():
    lv = Levels()
    lv.feed(0, RATE, 2, 32768.0, _tone(1000, 8192, amp=0.9))
    _settled(lv, 150_000)
    lv.reset()  # the next song
    lv.feed(0, RATE, 2, 32768.0, _tone(1000, 8192, amp=0.05))
    assert max(_settled(lv, 150_000)) > 0.9


class _Fmt:
    def sampleFormat(self):
        return type("F", (), {"name": "Int16"})()

    def sampleRate(self):
        return RATE

    def channelCount(self):
        return 2


class _Buf:
    def __init__(self, start_us):
        self.start_us = start_us

    def constData(self):
        return _tone(1000, 1024).tobytes()

    def isValid(self):
        return True

    def format(self):
        return _Fmt()

    def startTime(self):
        return self.start_us


def test_audio_is_only_kept_while_someone_reads_the_levels():
    eq = LogoEq()
    eq._buffer(_Buf(0))  # the look is Off or the window is hidden: nothing reads
    assert not eq.lv.bufs
    eq._last_read = time.monotonic()  # the logo asks again
    eq._buffer(_Buf(0))
    assert len(eq.lv.bufs) == 1
    eq._failed = True  # a stopped tap does no more work
    eq._buffer(_Buf(30_000))
    assert not eq.lv.bufs
