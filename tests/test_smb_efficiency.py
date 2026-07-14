"""Network-mount efficiency behavior of the download write path and the
ownership cache (the 2026-07-14 art/SMB audit fixes).

Covered here:
  * _ensure_directory memoizes per instance, so the same album directory is
    created once instead of several times per track, and self-heals when the
    directory vanishes mid-download (the memo entry is evicted and the retry
    recreates it).
  * _copy_file_contents copies bytes with a large buffer and no copystat tail.
  * A skip_if_exists move that loses the rename race to a sibling worker
    (EEXIST from rename-over-existing, seen on macOS SMB writing cover.jpg)
    counts as success instead of a retry storm.
  * The per-track segment fan-out is clamped to the shared connection pool
    size: extra workers can never hold a socket, they only cost threads.
  * ownershipOf stops re-statting the (download) volume for stale cache hits
    while downloads are running; misses still refresh immediately.
  * _record_ownership resolves paths with zero filesystem calls unless
    symlink-to-track mode actually needs realpath.

Download-side tests build the instance with __new__ (skipping the heavy
network __init__); bridge-side tests follow the test_ownership_bridge.py
pattern of binding real WavesBridge methods onto a bare stub.
"""

from __future__ import annotations

import os
import pathlib
import time
from concurrent import futures
from threading import Lock
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from tidaler.download import Download
from tidaler.waves_ui.backend import WavesBridge


@pytest.fixture
def downloader() -> Download:
    dl = Download.__new__(Download)
    dl.fn_logger = MagicMock()
    dl._FILE_OPERATION_RETRIES = 3
    dl._FILE_OPERATION_RETRY_DELAY_SEC = 0
    dl._dirs_ensured = set()
    return dl


def test_ensure_directory_creates_once_per_instance(downloader: Download, tmp_path: pathlib.Path) -> None:
    # A direct child: makedirs recurses through the mocked name for missing
    # parents, which would inflate the count without meaning extra ensures.
    target = tmp_path / "Album"
    with patch("tidaler.download.os.makedirs", wraps=os.makedirs) as makedirs_mock:
        for _ in range(4):  # audio + lyrics + cover + explicit pre-ensure
            downloader._ensure_directory(target)
    assert makedirs_mock.call_count == 1
    assert target.is_dir()


def test_move_file_recreates_vanished_directory(downloader: Download, tmp_path: pathlib.Path) -> None:
    # The memo says the album dir exists, then the user deletes it mid-album:
    # the failed copy must evict the memo entry so the retry recreates the dir.
    album_dir = tmp_path / "Album"
    downloader._ensure_directory(album_dir)
    album_dir.rmdir()

    source = tmp_path / "track.flac"
    source.write_bytes(b"audio")
    replace_original = pathlib.Path.replace

    def replace_no_cross_device(self: pathlib.Path, target: pathlib.Path) -> pathlib.Path:
        if self == source:
            raise OSError("Invalid cross-device link")
        return replace_original(self, target)

    with patch.object(pathlib.Path, "replace", replace_no_cross_device):
        result = downloader._move_file(source, album_dir / "track.flac", overwrite=True)

    assert result is True
    assert (album_dir / "track.flac").read_bytes() == b"audio"


def test_copy_file_contents_uses_large_buffer_without_copystat(downloader: Download, tmp_path: pathlib.Path) -> None:
    source = tmp_path / "src.bin"
    destination = tmp_path / "dst.bin"
    source.write_bytes(b"x" * 1024)

    with patch("tidaler.download.shutil.copyfileobj", wraps=None) as copy_mock:
        downloader._copy_file_contents(source, destination)

    (_, _), kwargs = copy_mock.call_args
    assert kwargs["length"] == Download._COPY_BUFFER_BYTES
    assert Download._COPY_BUFFER_BYTES >= 4 * 1024 * 1024


def test_copy_file_contents_copies_bytes(downloader: Download, tmp_path: pathlib.Path) -> None:
    source = tmp_path / "src.bin"
    destination = tmp_path / "dst.bin"
    payload = os.urandom(64 * 1024)
    source.write_bytes(payload)
    downloader._copy_file_contents(source, destination)
    assert destination.read_bytes() == payload


def test_move_file_lost_cover_race_counts_as_success(downloader: Download, tmp_path: pathlib.Path) -> None:
    # Two tracks of one album race to land cover.jpg; some network filesystems
    # answer rename-over-existing with EEXIST. The loser must report success
    # (the cover exists), not burn retries.
    source = tmp_path / "cover-src.jpg"
    destination = tmp_path / "cover.jpg"
    source.write_bytes(b"cover")

    def replace_racing(self: pathlib.Path, target: pathlib.Path) -> pathlib.Path:
        if self == source:
            raise OSError("Invalid cross-device link")
        raise FileExistsError(17, "File exists")

    with patch.object(pathlib.Path, "replace", replace_racing):
        result = downloader._move_file(source, destination, overwrite=False, skip_if_exists=True)

    assert result is True
    assert not source.exists()
    assert list(tmp_path.glob(".*.tmp")) == []


def test_segment_fanout_clamped_to_connection_pool(downloader: Download) -> None:
    downloader.settings = SimpleNamespace(data=SimpleNamespace(downloads_simultaneous_per_track_max=20))
    downloader.progress = MagicMock()
    downloader.progress.tasks = {0: SimpleNamespace(total=None, finished=False)}
    downloader.event_abort = SimpleNamespace(is_set=lambda: False)

    captured: dict = {}
    real_executor = futures.ThreadPoolExecutor

    def capturing_executor(*args, **kwargs):
        captured["max_workers"] = kwargs.get("max_workers", args[0] if args else None)
        return real_executor(*args, **kwargs)

    with patch("tidaler.download.futures.ThreadPoolExecutor", capturing_executor):
        downloader._download_segments([], pathlib.Path("."), None, 0, False, None)

    assert captured["max_workers"] == Download._HTTP_POOL_MAXSIZE


class _Pool:
    def __init__(self):
        self.started = 0

    def start(self, worker):
        self.started += 1


class _Stub:
    """Bare bridge stand-in for the ownership TTL and realpath-gate tests."""

    def __init__(self, downloads_running: bool, symlink_to_track: bool = False):
        self._own_lock = Lock()
        self._own_cache: dict = {}
        self._own_pending: set = set()
        self._own_pool = _Pool()
        self._OWN_TTL = WavesBridge._OWN_TTL
        self._OWN_TTL_BUSY = WavesBridge._OWN_TTL_BUSY
        self._downloads_running = lambda: downloads_running
        self._target_quality_rank = lambda: 0
        self._ownership = MagicMock()
        self.ownershipChanged = MagicMock()
        self.settings = SimpleNamespace(
            data=SimpleNamespace(quality_audio="LOSSLESS", symlink_to_track=symlink_to_track)
        )
        for name in ("ownershipOf", "_record_ownership"):
            setattr(self, name, getattr(WavesBridge, name).__get__(self, _Stub))

    def seed_stale(self, tid: str) -> None:
        # Older than _OWN_TTL, far younger than _OWN_TTL_BUSY.
        self._own_cache[tid] = (time.monotonic() - (WavesBridge._OWN_TTL + 2.0), {"owned": True, "quality_rank": 1})


def test_ownership_stale_hits_do_not_restat_during_downloads() -> None:
    stub = _Stub(downloads_running=True)
    stub.seed_stale("101")
    answer = stub.ownershipOf("101")
    assert answer["owned"] is True
    assert stub._own_pool.started == 0


def test_ownership_stale_hits_refresh_when_idle() -> None:
    stub = _Stub(downloads_running=False)
    stub.seed_stale("101")
    stub.ownershipOf("101")
    assert stub._own_pool.started == 1


def test_ownership_misses_still_refresh_during_downloads() -> None:
    # Backoff must only slow re-checks of known answers; a row the cache has
    # never seen still gets its badge promptly mid-download.
    stub = _Stub(downloads_running=True)
    stub.ownershipOf("999")
    assert stub._own_pool.started == 1


def test_record_ownership_skips_realpath_without_symlink_mode() -> None:
    stub = _Stub(downloads_running=False, symlink_to_track=False)
    ev = {"id": 7, "path": "/library/Artist/Album/track.flac", "quality": {"tier": "LOSSLESS"}}
    with patch("tidaler.waves_ui.backend.os.path.realpath") as realpath_mock:
        stub._record_ownership(ev)
    realpath_mock.assert_not_called()
    recorded_path = stub._ownership.record.call_args[0][1]
    assert recorded_path == "/library/Artist/Album/track.flac"


def test_record_ownership_resolves_realpath_in_symlink_mode() -> None:
    stub = _Stub(downloads_running=False, symlink_to_track=True)
    ev = {"id": 7, "path": "/library/link.flac", "quality": {"tier": "LOSSLESS"}}
    with patch("tidaler.waves_ui.backend.os.path.realpath", return_value="/real/track.flac") as realpath_mock:
        stub._record_ownership(ev)
    realpath_mock.assert_called_once_with("/library/link.flac")
    assert stub._ownership.record.call_args[0][1] == "/real/track.flac"
