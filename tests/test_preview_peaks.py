"""The now-playing rail's waveform: loudness buckets from a preview clip."""

from __future__ import annotations

import math
import os
import shutil
import stat
import sys
import wave
from array import array
from types import SimpleNamespace

import pytest

from waves.waves_ui import preview_peaks
from waves.waves_ui.backend import WavesBridge


def test_buckets_are_normalized_and_cover_the_whole_track():
    # A quiet first half and a loud second half, 1000 samples.
    samples = [100] * 500 + [20000] * 500
    peaks = preview_peaks.peak_buckets(samples, 10)
    assert len(peaks) == 10
    assert peaks[:5] == [0.005] * 5
    assert peaks[5:] == [1.0] * 5


def test_negative_samples_count_by_their_size():
    peaks = preview_peaks.peak_buckets([-30000, 3, -3, 30], 2)
    assert peaks == [1.0, 0.001]


def test_buckets_measure_loudness_not_the_loudest_sample():
    # A slice with one full-scale click and silence around it is much
    # quieter than a slice that is loud throughout: a peak-per-slice shape
    # would call them equal.
    click = [32000] + [0] * 99
    steady = [20000, -20000] * 50
    peaks = preview_peaks.peak_buckets(click + steady, 2)
    assert peaks[1] == 1.0
    assert peaks[0] < 0.2


def test_silence_and_short_input_never_divide_by_zero():
    assert preview_peaks.peak_buckets([], 4) == [0.0] * 4
    assert preview_peaks.peak_buckets([0, 0, 0], 4) == [0.0] * 4
    # Fewer samples than buckets: every bucket still has one sample.
    assert len(preview_peaks.peak_buckets([5, 9], 6)) == 6


@pytest.mark.skipif(sys.platform == "win32", reason="the stand-in ffmpeg is a shell script")
def test_a_clip_decodes_through_ffmpeg(tmp_path):
    """A stand-in ffmpeg that ignores its input and writes a known sine, so the
    test proves the pipe shape (s16le on stdout) without needing a real decoder."""
    pcm = array("h", (int(8000 * math.sin(i / 7)) for i in range(16000)))
    raw = tmp_path / "sine.raw"
    raw.write_bytes(pcm.tobytes())
    fake = tmp_path / "ffmpeg"
    fake.write_text(f'#!/bin/sh\ncat "{raw}"\n')
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    peaks = preview_peaks.peaks_from_clip(str(fake), str(tmp_path / "clip.m4a"), 20)
    assert len(peaks) == 20
    assert max(peaks) == 1.0
    assert min(peaks) > 0.9  # a steady sine is loud everywhere


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs a real ffmpeg")
def test_a_real_wav_gives_the_shape_of_its_loudness(tmp_path):
    path = tmp_path / "clip.wav"
    rate = 8000
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        frames = array("h", (int((20000 if i >= rate else 2000) * math.sin(i / 3)) for i in range(2 * rate)))
        w.writeframes(frames.tobytes())
    peaks = preview_peaks.peaks_from_clip(shutil.which("ffmpeg"), str(path), 8)
    assert all(p < 0.2 for p in peaks[:4])
    assert all(p > 0.8 for p in peaks[4:])


class _Stub:
    _emit_preview_peaks = WavesBridge._emit_preview_peaks
    _PREVIEW_PEAKS_N = 4

    def __init__(self, ffmpeg):
        self._preview_peaks = {}
        self.sent = []
        self.ffmpeg = ffmpeg
        self.previewPeaks = SimpleNamespace(emit=lambda *a: self.sent.append(a))

    def _preview_ffmpeg_bin(self):
        return self.ffmpeg


def test_peaks_are_emitted_once_per_clip_and_cached(monkeypatch):
    calls = []

    def fake_decode(ffmpeg, path, n, **kw):
        calls.append(path)
        return [0.1, 0.5, 1.0, 0.2]

    monkeypatch.setattr(preview_peaks, "peaks_from_clip", fake_decode)
    stub = _Stub("ffmpeg")
    url = "file:///tmp/waves_preview_abc.m4a"
    stub._emit_preview_peaks("track", "t1", url)
    stub._emit_preview_peaks("track", "t1", url)
    assert len(calls) == 1
    assert stub.sent == [("track", "t1", [0.1, 0.5, 1.0, 0.2])] * 2
    assert os.path.basename(calls[0]) == "waves_preview_abc.m4a"


def test_a_failed_decode_or_a_stream_url_sends_an_empty_list(monkeypatch):
    def boom(*a, **kw):
        raise RuntimeError("no decoder")

    monkeypatch.setattr(preview_peaks, "peaks_from_clip", boom)
    stub = _Stub("ffmpeg")
    stub._emit_preview_peaks("track", "t1", "file:///tmp/x.m4a")
    stub._emit_preview_peaks("track", "t1", "https://cdn.example/x.m3u8")
    # Empty means "no waveform coming": the rail drops its waiting ghost.
    assert stub.sent == [("track", "t1", [])] * 2
    assert stub._preview_peaks == {}


def test_no_ffmpeg_means_no_waveform_and_no_error():
    stub = _Stub(None)
    stub._emit_preview_peaks("track", "t1", "file:///tmp/x.m4a")
    assert stub.sent == [("track", "t1", [])]
