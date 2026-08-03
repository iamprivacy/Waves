"""Filesystem, download-integrity and privacy fixes from the 2026-08-02 audit.

Findings 23-28: staging names over NAME_MAX, playlist symlinks into a missing
directory, the bare-requests sizing probe, the Path.home() relocation on an
over-long path, factory reset leaving diagnostic bundles behind, and the
page-cache save racing live caches.
"""

from __future__ import annotations

import json
import pathlib
import threading
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from pathvalidate.error import ErrorReason, ValidationError

from tidaler.download import Download
from tidaler.helper.path import _shorten_to_valid_length, path_file_sanitize
from tidaler.waves_ui.backend import WavesBridge


def _downloader(tmp_path: pathlib.Path) -> Download:
    d = Download.__new__(Download)
    d.fn_logger = MagicMock()
    d._FILE_OPERATION_RETRIES = 2
    d._FILE_OPERATION_RETRY_DELAY_SEC = 0
    d._dirs_ensured = set()
    d.path_base = str(tmp_path)
    d.skip_existing = True
    d.settings = SimpleNamespace(
        data=SimpleNamespace(
            format_track="Tracks/{artist_name} - {track_title}",
            filename_delimiter_artist=", ",
            filename_delimiter_album_artist=", ",
            use_primary_album_artist=False,
            symlink_to_track=True,
        )
    )
    return d


# Finding 23: the staging name must fit NAME_MAX even for a maximal track name.


def test_a_maximal_destination_name_still_stages_and_swaps(tmp_path: pathlib.Path) -> None:
    d = _downloader(tmp_path)
    src = tmp_path / "src.flac"
    src.write_text("audio", encoding="utf-8")
    dst = tmp_path / ("x" * 250 + ".flac")

    assert d._stage_and_swap(src, dst, skip_if_exists=False) is True
    assert dst.read_text(encoding="utf-8") == "audio"
    assert not src.exists(), "the source is consumed on success"
    leftovers = [p.name for p in tmp_path.iterdir() if p.name.endswith(".tmp")]
    assert leftovers == [], "no staging file may be left behind"


# Finding 24: a playlist symlink for an already-owned track must not crash the job.


def test_a_symlink_into_a_missing_playlist_dir_creates_the_dir(tmp_path: pathlib.Path) -> None:
    d = _downloader(tmp_path)
    dst = tmp_path / "Tracks" / "song.flac"
    dst.parent.mkdir()
    dst.write_text("audio", encoding="utf-8")
    src = tmp_path / "Playlists" / "Road Songs" / "song.flac"  # parent never created

    with patch("tidaler.download.format_path_media", return_value="Tracks/song"):
        out = d.media_move_and_symlink(SimpleNamespace(), src, ".flac")

    assert out == dst
    assert src.is_symlink() and src.resolve() == dst.resolve()


def test_a_failed_symlink_is_logged_and_never_raises(tmp_path: pathlib.Path) -> None:
    d = _downloader(tmp_path)
    dst = tmp_path / "Tracks" / "song.flac"
    dst.parent.mkdir()
    dst.write_text("audio", encoding="utf-8")
    src = tmp_path / "Playlists" / "PL" / "song.flac"

    with (
        patch("tidaler.download.format_path_media", return_value="Tracks/song"),
        patch.object(pathlib.Path, "symlink_to", side_effect=OSError("no symlinks here")),
    ):
        out = d.media_move_and_symlink(SimpleNamespace(), src, ".flac")

    assert out == dst
    assert d.fn_logger.error.called, "the failure is diagnosable"


# Finding 25: the sizing probe rides the pooled session and never fails the track.


def _probe(tmp_path: pathlib.Path) -> Download:
    d = _downloader(tmp_path)
    d.progress = MagicMock()
    d.progress.add_task.return_value = 7
    return d


def test_the_sizing_probe_uses_the_pooled_session_and_follows_redirects(tmp_path: pathlib.Path) -> None:
    d = _probe(tmp_path)
    response = MagicMock()
    response.headers = {"content-length": str(4 * 1048576)}
    session = MagicMock()
    session.head.return_value = response

    with patch.object(Download, "_shared_http", return_value=session):
        p_task, total, block = d._setup_progress("Song", ["https://cdn.example/x"], False)

    assert p_task == 7 and total == 4.0 and block == 1048576
    assert session.head.call_args.kwargs.get("allow_redirects") is True


def test_a_failed_probe_degrades_to_indeterminate_progress(tmp_path: pathlib.Path) -> None:
    d = _probe(tmp_path)
    session = MagicMock()
    session.head.side_effect = OSError("blip")

    with patch.object(Download, "_shared_http", return_value=session):
        _p_task, total, block = d._setup_progress("Song", ["https://cdn.example/x"], False)

    assert total is None, "an unsizable download still downloads"
    assert block == 1048576
    assert d.fn_logger.error.called, "the blip is logged, not swallowed"


# Finding 26: an over-long path shortens in place instead of moving to home.


def test_an_over_long_path_stays_inside_the_download_base(tmp_path: pathlib.Path) -> None:
    base = tmp_path / "Library"
    over_long = base / ("a" * 250) / ("b" * 250) / ("c" * 250) / ("d" * 250) / "01. Song.flac"

    result = path_file_sanitize(over_long, adapt=True)

    assert result != pathlib.Path.home() / over_long.name
    assert str(result).startswith(str(base)), "the track never leaves the library"
    assert result.name == "01. Song.flac"


def test_shortening_is_deterministic_per_album_folder(tmp_path: pathlib.Path) -> None:
    parent = tmp_path / ("a" * 250) / ("b" * 250) / ("c" * 250) / ("d" * 250)
    one = path_file_sanitize(parent / "01. One.flac", adapt=True)
    two = path_file_sanitize(parent / "02. Two.flac", adapt=True)
    assert one.parent == two.parent, "every track of the album lands in one folder"


def test_shorten_helper_halves_then_drops_components() -> None:
    def cap_20(p: pathlib.Path) -> pathlib.Path:
        if len(str(p)) > 20:
            raise ValidationError(reason=ErrorReason.INVALID_LENGTH, description="too long")
        return p

    out = _shorten_to_valid_length(pathlib.Path("/base/artistartistartist/albumalbumalbum"), cap_20)
    assert len(str(out)) <= 20
    assert str(out).startswith("/base"), "shallow components (the base) survive"


# Finding 27: factory reset erases exported diagnostic bundles.


def test_factory_reset_wipes_diagnostic_bundles(tmp_path: pathlib.Path, monkeypatch) -> None:
    bundle = tmp_path / "waves-diagnostics-20260802-121314-123.txt"
    bundle.write_text("scrubbed diagnostics", encoding="utf-8")
    foreign = tmp_path / "waves-diagnostics-notes.txt"
    foreign.write_text("the user's own notes", encoding="utf-8")

    stub = SimpleNamespace(_ownership=SimpleNamespace(close=lambda: None))
    monkeypatch.setattr("tidaler.waves_ui.backend.path_config_base", lambda: str(tmp_path))
    monkeypatch.setattr("tidaler.waves_ui.backend.OwnershipStore", lambda _p: SimpleNamespace())
    monkeypatch.setattr("tidaler.waves_ui.backend.diagnostics.detach_disk_log", lambda: None)
    monkeypatch.setattr("tidaler.waves_ui.backend.QtCore.QSettings", lambda: MagicMock())
    WavesBridge.factoryReset(stub)

    assert not bundle.exists(), "the export contains breadcrumbs and must go with the reset"
    assert foreign.exists(), "only the exact timestamped shape may match"


# Finding 28: a racing page-cache save skips the save, never crashes its caller.


class _RacingDict(dict):
    """A library cache whose iteration blows up like a dict mutated mid-scan."""

    def items(self):
        raise RuntimeError("dictionary changed size during iteration")


class _SaveStub:
    _save_page_cache = WavesBridge._save_page_cache

    def __init__(self, path: pathlib.Path, lib=None):
        self._logged_in = True
        self._factory_reset = False
        self._lib_cache = {"albums": {"items": [{"id": "a1"}], "more": False}} if lib is None else lib
        self._lib_sort = {}
        self._browse_root_cache = {}
        self._browse_pages = {}
        self._artist_cache = {}
        self._home_cache = {}
        self._page_cache_lock = threading.Lock()
        self._page_cache_path = str(path)

    def _cache_user_id(self):
        return "u"


def test_a_cache_mutating_mid_save_never_escapes_the_worker(tmp_path: pathlib.Path) -> None:
    stub = _SaveStub(tmp_path / "page_cache.json", lib=_RacingDict())
    stub._save_page_cache()  # must not raise: an escape latches the busy spinner on
    assert not (tmp_path / "page_cache.json").exists(), "a torn snapshot is never written"


def test_a_normal_save_still_writes_valid_json(tmp_path: pathlib.Path) -> None:
    stub = _SaveStub(tmp_path / "page_cache.json")
    stub._save_page_cache()
    data = json.loads((tmp_path / "page_cache.json").read_text(encoding="utf-8"))
    assert data["library"]["albums"]["items"] == [{"id": "a1"}]


def test_the_save_serializes_one_shot_never_incrementally(tmp_path: pathlib.Path) -> None:
    """json.dump's pure-Python encoder yields between dict items and can watch
    a cache change size mid-encode; json.dumps' one-shot C encoder cannot. The
    save must use dumps."""
    stub = _SaveStub(tmp_path / "page_cache.json")
    with patch("tidaler.waves_ui.backend.json.dump", side_effect=AssertionError("dump() must not be used")):
        stub._save_page_cache()
    assert (tmp_path / "page_cache.json").exists()


def test_factory_reset_pattern_cannot_match_a_user_file() -> None:
    from tidaler.waves_ui.backend import _FACTORY_WIPE_LOG_PATTERNS

    bundle_pat = _FACTORY_WIPE_LOG_PATTERNS[-1]
    assert bundle_pat.match("waves-diagnostics-20260802-121314-123.txt")
    for name in (
        "waves-diagnostics-20260802-121314-123.txt.bak",
        "my-waves-diagnostics-20260802-121314-123.txt",
        "waves-diagnostics-2026-08-02.txt",
        "waves-diagnostics-.txt",
    ):
        assert not bundle_pat.match(name), name
