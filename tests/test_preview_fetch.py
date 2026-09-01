"""Preview loading: parallel HLS segment fetch + up-front 'now previewing' meta.

Hermetic: real unbound WavesBridge methods bound onto minimal stubs, with a
fake pooled session in place of the network. No Qt, no ffmpeg, no CDN.
"""

from __future__ import annotations

import pathlib
import tempfile
import time
from threading import Lock
from types import SimpleNamespace

import pytest

from waves.waves_ui import backend as backend_mod
from waves.waves_ui.backend import WavesBridge


class _Resp:
    def __init__(self, body: bytes):
        self.content = body

    def raise_for_status(self):
        return None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeHttp:
    """Records every fetch and the peak number of them in flight at once."""

    def __init__(self, delay: float = 0.02, fail: str | None = None):
        self.delay, self.fail = delay, fail
        self.urls: list[str] = []
        self.live = self.peak = 0
        self._lock = Lock()

    def get(self, url, timeout=None):
        with self._lock:
            self.urls.append(url)
            self.live += 1
            self.peak = max(self.peak, self.live)
        try:
            time.sleep(self.delay)
            if self.fail is not None and self.fail in url:
                raise OSError("segment fetch blew up")
            return _Resp(b"seg:" + url.encode())
        finally:
            with self._lock:
                self.live -= 1


def _playlist(count: int, *, dur: float = 4.0, base: str = "https://cdn/seg", extra: str = "") -> str:
    lines = ["#EXTM3U", "#EXT-X-VERSION:3", "#EXT-X-TARGETDURATION:5"]
    if extra:
        lines.append(extra)
    for i in range(count):
        lines += [f"#EXTINF:{dur:.3f},", f"{base}{i}.ts"]
    lines.append("#EXT-X-ENDLIST")
    return "\n".join(lines) + "\n"


class _HlsStub:
    _localise_hls = WavesBridge._localise_hls


@pytest.fixture
def http(monkeypatch):
    fake = _FakeHttp()
    monkeypatch.setattr(backend_mod, "_preview_http", lambda: fake)
    return fake


def test_segments_are_fetched_concurrently_not_one_at_a_time(tmp_path, http):
    """The whole point: ffmpeg opens HLS segments serially, so a whole track is
    dozens of round trips and the wait is latency-bound. Fetching them
    ourselves must actually overlap."""
    out = _HlsStub()._localise_hls(_playlist(24), whole=True, work_dir=str(tmp_path))

    assert out is not None
    assert len(http.urls) == 24
    assert http.peak > 1, "serial fetching is the bug this exists to fix"
    assert http.peak <= backend_mod._PREVIEW_SEG_WORKERS


def test_the_local_playlist_lists_every_fetched_segment_on_disk(tmp_path, http):
    out = _HlsStub()._localise_hls(_playlist(3), whole=True, work_dir=str(tmp_path))

    body = pathlib.Path(out).read_text(encoding="utf-8")
    paths = [ln for ln in body.splitlines() if not ln.startswith("#")]
    assert len(paths) == 3
    assert all(pathlib.Path(p).exists() for p in paths), "ffmpeg reads these off disk"
    assert "https://" not in body, "a localised playlist must need no network protocols"
    assert body.startswith("#EXTM3U") and "#EXT-X-ENDLIST" in body


def test_local_segments_keep_the_remote_extension(tmp_path, http):
    """ffmpeg's HLS demuxer hard-blocks unrecognised segment extensions (and
    since 8.x probes that the extension matches the content), so the local
    copies must carry the CDN's own extension, query string stripped."""
    pl = _playlist(2, base="https://cdn/media/seg")  # seg0.ts, seg1.ts
    pl = pl.replace("seg1.ts", "seg1.m4s?token=abc")
    out = _HlsStub()._localise_hls(pl, whole=True, work_dir=str(tmp_path))

    paths = [ln for ln in pathlib.Path(out).read_text(encoding="utf-8").splitlines() if not ln.startswith("#")]
    assert paths[0].endswith(".ts") and paths[1].endswith(".m4s")


def test_an_extensionless_segment_url_falls_back(tmp_path, http):
    """No usable extension means ffmpeg would reject whatever name we invent;
    the serial https fallback is the only correct move."""
    pl = _playlist(2).replace("seg1.ts", "seg1")

    assert _HlsStub()._localise_hls(pl, whole=True, work_dir=str(tmp_path)) is None


def test_a_taste_fetches_only_the_segments_it_will_play(tmp_path, http):
    """30s of 4s segments is 8, not the whole 25-segment track."""
    _HlsStub()._localise_hls(_playlist(25, dur=4.0), whole=False, work_dir=str(tmp_path))

    assert len(http.urls) == 8


def test_an_fmp4_init_segment_comes_along(tmp_path, http):
    pl = _playlist(2, extra='#EXT-X-MAP:URI="https://cdn/init.mp4"')
    out = _HlsStub()._localise_hls(pl, whole=True, work_dir=str(tmp_path))

    assert "https://cdn/init.mp4" in http.urls, "without it the local playlist is undecodable"
    body = pathlib.Path(out).read_text(encoding="utf-8")
    assert "#EXT-X-MAP:" in body and "https://" not in body


@pytest.mark.parametrize(
    ("playlist", "why"),
    [
        (_playlist(0), "an empty playlist has nothing to localise"),
        (_playlist(2, base="seg"), "relative URIs need the playlist's own base URL"),
        (
            _playlist(2, extra='#EXT-X-KEY:METHOD=AES-128,URI="https://cdn/k"'),
            "keyed segments belong to the download+decrypt path",
        ),
        ("not a playlist at all", "unparsable input"),
    ],
)
def test_unsupported_playlists_fall_back_instead_of_failing(tmp_path, http, playlist, why):
    assert _HlsStub()._localise_hls(playlist, whole=True, work_dir=str(tmp_path)) is None, why


def test_a_failed_segment_falls_back_rather_than_half_a_clip(tmp_path, monkeypatch):
    fake = _FakeHttp(fail="seg7.")
    monkeypatch.setattr(backend_mod, "_preview_http", lambda: fake)

    assert _HlsStub()._localise_hls(_playlist(12), whole=True, work_dir=str(tmp_path)) is None


def test_a_failed_segment_does_not_wait_out_the_queue_behind_it(tmp_path, monkeypatch):
    """The fallback has to START quickly. Once one segment fails the rest are
    pointless, and letting the queue drain (each with its own 20s timeout) held
    the preview for minutes before ffmpeg was even handed the https playlist.
    """
    seg, delay = 48, 0.2
    fake = _FakeHttp(delay=delay, fail="seg0.")
    monkeypatch.setattr(backend_mod, "_preview_http", lambda: fake)

    t0 = time.monotonic()
    assert _HlsStub()._localise_hls(_playlist(seg), whole=True, work_dir=str(tmp_path)) is None
    elapsed = time.monotonic() - t0

    waves = -(-seg // backend_mod._PREVIEW_SEG_WORKERS)  # what draining would cost
    assert elapsed < waves * delay * 0.5, f"gave up after {elapsed:.2f}s, the whole queue costs {waves * delay:.2f}s"
    assert len(fake.urls) < seg, "the segments queued behind the failure were fetched anyway"


def test_the_segment_fetches_are_registered_with_diagnostics_once(tmp_path, http, monkeypatch):
    """Project diagnostics convention: new pools report their saturation to
    the perf sampler. The pool itself is per preview (an abandoned clip must
    not leave its queue in front of the next one) and the sampler holds what
    it is given for the whole run, so exactly one gauge is registered, however
    many clips are played."""
    seen: list = []
    monkeypatch.setattr(backend_mod.diagnostics, "register_pool", lambda name, pool: seen.append((name, pool)))
    monkeypatch.setattr(backend_mod, "_preview_seg_registered", False)
    # Another test's abandoned burst may still be finishing; wait it out so the
    # baseline is a genuine idle.
    deadline = time.monotonic() + 5
    while backend_mod._preview_seg_busy and time.monotonic() < deadline:
        time.sleep(0.02)
    idle = backend_mod._preview_seg_busy

    # Sample the gauge from inside a fetch: the reading has to move.
    readings: list[int] = []
    real_get = http.get
    http.get = lambda url, timeout=None: (readings.append(seen[0][1].activeThreadCount()), real_get(url, timeout))[1]

    for _ in range(3):
        _HlsStub()._localise_hls(_playlist(4), whole=True, work_dir=str(tmp_path))

    assert len(seen) == 1, "one entry per preview would pile up over a run that lasts weeks"
    name, gauge = seen[0]
    assert name == "preview"
    assert max(readings) > idle, "the gauge never saw a fetch in flight"
    assert gauge.activeThreadCount() == idle, "a finished burst must leave the count where it found it"
    assert gauge.maxThreadCount() == backend_mod._PREVIEW_SEG_WORKERS


def test_the_remux_error_keeps_ffmpegs_words_and_none_of_the_paths(tmp_path, monkeypatch, caplog):
    """ffmpeg's last stderr lines are worth logging; the CDN URLs in them carry
    auth, and the temp directory it names runs through the user's home on
    Windows. Neither belongs in a log the user may hand to a stranger."""
    import logging
    import subprocess

    class _Stub:
        _remux_preview = WavesBridge._remux_preview

    tmp = tempfile.gettempdir()
    stderr = (
        f"[hls] Opening 'https://cdn.example/seg0.ts?auth=SECRET' for reading\n"
        f"Error opening output file {tmp}/waves_preview_ab12.m4a.\n"
        "Invalid data found when processing input\n"
    ).encode()

    def boom(*args, **kwargs):
        raise subprocess.CalledProcessError(1, ["ffmpeg"], stderr=stderr)

    monkeypatch.setattr(backend_mod.subprocess, "run", boom)
    with caplog.at_level(logging.ERROR), pytest.raises(subprocess.CalledProcessError):
        _Stub()._remux_preview("ffmpeg", "https://cdn.example/track.flac", None, whole=False)

    line = "\n".join(r.getMessage() for r in caplog.records)
    assert "Invalid data found when processing input" in line, "the diagnosis is the point of the line"
    assert "cdn.example" not in line and "SECRET" not in line
    assert tmp not in line and "<tmp>" in line


# --- The 'now previewing' label must not wait on the resolve ---


class _Emitter:
    def __init__(self, log, name):
        self.log, self.name = log, name

    def emit(self, *args):
        self.log.append((self.name, args))


class _ImmediatePool:
    """Runs the queued Worker inline so the test sees its ordering."""

    @staticmethod
    def start(worker):
        worker.run()


class _PreviewStub:
    previewTrack = WavesBridge.previewTrack
    _emit_preview_meta = WavesBridge._emit_preview_meta

    def __init__(self, track):
        self.log: list = []
        self._objs = {"track": {"t1": track}}
        self.previewMeta = _Emitter(self.log, "meta")
        self.previewReady = _Emitter(self.log, "ready")
        self.previewState = _Emitter(self.log, "state")
        self.threadpool = _ImmediatePool()

    def _preview_source(self, track, whole=False):
        self.log.append(("resolve", (whole,)))
        return "file:///clip.m4a"


@pytest.fixture
def track(monkeypatch):
    obj = SimpleNamespace(id="t1", album=SimpleNamespace(id="al1"))
    monkeypatch.setattr(backend_mod, "name_builder_title", lambda t: "Title")
    monkeypatch.setattr(backend_mod, "name_builder_artist", lambda t: "Artist")
    monkeypatch.setattr(backend_mod, "_image", lambda t, n: "art")
    monkeypatch.setattr(backend_mod, "_artist_id", lambda t: "ar1")
    monkeypatch.setattr(backend_mod, "_artists_list", lambda t: [])
    return obj


def test_the_label_is_published_before_the_multi_second_resolve(track):
    """Everything in the label comes off the track object we already hold;
    waiting for the source left the card empty for the whole load."""
    stub = _PreviewStub(track)

    stub.previewTrack("t1")

    order = [name for name, _ in stub.log]
    assert order == ["state", "meta", "resolve", "ready"]


def test_the_label_still_carries_the_full_credit_payload(track):
    stub = _PreviewStub(track)

    stub.previewTrack("t1")

    kind, ident, title, artist, art, artist_id, album_id, track_id, artists = next(
        args for name, args in stub.log if name == "meta"
    )
    assert (kind, ident, track_id) == ("track", "t1", "t1")
    assert (title, artist, art) == ("Title", "Artist", "art")
    assert (artist_id, album_id, artists) == ("ar1", "al1", [])
