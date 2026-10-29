import hashlib
import pathlib
import shutil
import subprocess
from threading import Event, Lock
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from ffmpeg import FFmpegError
from mutagen.flac import FLAC
from tidalapi import Track

from waves import download as download_mod
from waves import flac_verify
from waves.constants import DownsampleTarget
from waves.download import Download

# What a build without libsoxr prints when an aresample filter asks for it.
_SOXR_STDERR = [
    "[Parsed_aresample_0 @ 0x1] [SWR @ 0x2] Requested resampling engine is unavailable",
    "[af#0:0 @ 0x3] Error reinitializing filters!",
    "[out#0/flac @ 0x4] Nothing was written into output file, because at least one of its streams received no packets.",
]


def _ffmpeg_runs(ffmpeg_mock: MagicMock, runs: list) -> "_Sequence":
    """Side effects for a mocked FFmpeg's execute, one per run.

    Each entry is None (the run succeeds) or the stderr lines of a failed run:
    they are fed to the handler the code registered with ``on("stderr", ...)``
    and the raised error carries only the last one, as python-ffmpeg does.
    """

    def run(lines: list[str] | None):
        def execute() -> None:
            if lines is None:
                return
            for call in ffmpeg_mock.on.call_args_list:
                if call.args[0] == "stderr":
                    for line in lines:
                        call.args[1](line)
            raise FFmpegError(lines[-1], ["ffmpeg"])

        return execute

    return _Sequence([run(lines) for lines in runs])


class _Sequence:
    """A side_effect callable that runs the next scripted step on each call."""

    def __init__(self, steps: list) -> None:
        self._steps = iter(steps)

    def __call__(self, *args, **kwargs):
        return next(self._steps)()


@pytest.fixture(autouse=True)
def _fresh_soxr_cache():
    """Every test starts knowing nothing about which ffmpeg lacks soxr."""
    download_mod._SOXR_MISSING.clear()
    yield
    download_mod._SOXR_MISSING.clear()


@pytest.fixture
def download_instance() -> Download:
    """Create a Download instance for file operation tests.

    Returns:
        Download: Configured download instance.
    """
    downloader = Download.__new__(Download)
    downloader.fn_logger = MagicMock()
    downloader._FILE_OPERATION_RETRIES = 2
    downloader._FILE_OPERATION_RETRY_DELAY_SEC = 0
    downloader._dirs_ensured = set()
    # The in-flight name claims every destination now goes through, whether or
    # not skipping is on, and the names this run has already written (see
    # _perform_actual_download).
    downloader._names_reserved = {}
    downloader._names_written = {}
    downloader._names_reserved_lock = Lock()

    return downloader


def test_move_file_retries_transient_replace_failure(download_instance: Download, tmp_path: pathlib.Path) -> None:
    """Verify overwrite moves are retried when the destination is temporarily locked.

    Args:
        download_instance (Download): Download instance under test.
        tmp_path (pathlib.Path): Temporary test directory.
    """
    source_path: pathlib.Path = tmp_path / "source.flac"
    destination_path: pathlib.Path = tmp_path / "destination.flac"
    source_path.write_text("new", encoding="utf-8")
    destination_path.write_text("old", encoding="utf-8")

    replace_original = pathlib.Path.replace
    replace_calls: int = 0

    def replace_once_locked(self: pathlib.Path, target: pathlib.Path) -> pathlib.Path:
        nonlocal replace_calls
        replace_calls += 1
        if replace_calls == 1:
            raise PermissionError(32, "The process cannot access the file because it is being used by another process")
        return replace_original(self, target)

    with patch.object(pathlib.Path, "replace", replace_once_locked):
        result: bool = download_instance._move_file(source_path, destination_path, overwrite=True)

    assert result is True
    assert destination_path.read_text(encoding="utf-8") == "new"
    assert not source_path.exists()
    assert replace_calls == 2


def test_move_file_skip_existing_keeps_destination(download_instance: Download, tmp_path: pathlib.Path) -> None:
    """Verify shared album extras are skipped when another track already wrote them.

    Args:
        download_instance (Download): Download instance under test.
        tmp_path (pathlib.Path): Temporary test directory.
    """
    source_path: pathlib.Path = tmp_path / "cover-source.jpg"
    destination_path: pathlib.Path = tmp_path / "cover.jpg"
    source_path.write_bytes(b"new-cover")
    destination_path.write_bytes(b"existing-cover")

    result: bool = download_instance._move_file(
        source_path,
        destination_path,
        overwrite=False,
        skip_if_exists=True,
    )

    assert result is True
    assert destination_path.read_bytes() == b"existing-cover"
    assert not source_path.exists()


def test_retry_file_operation_does_not_sleep_after_final_attempt(download_instance: Download) -> None:
    """Verify file operation retries do not delay after the final failed attempt.

    Args:
        download_instance (Download): Download instance under test.
    """

    def operation() -> bool:
        raise PermissionError(32, "The process cannot access the file because it is being used by another process")

    with patch("waves.download.time.sleep") as sleep_mock:
        result: bool = download_instance._retry_file_operation(operation, "locked operation")

    assert result is False
    assert sleep_mock.call_count == 1


def test_media_move_and_symlink_skips_symlink_when_unlink_fails(
    download_instance: Download,
    tmp_path: pathlib.Path,
) -> None:
    """Verify symlink creation is skipped when the original source cannot be removed.

    Args:
        download_instance (Download): Download instance under test.
        tmp_path (pathlib.Path): Temporary test directory.
    """
    source_path: pathlib.Path = tmp_path / "source.flac"
    source_path.write_text("audio", encoding="utf-8")

    download_instance.path_base = str(tmp_path / "library")
    download_instance.skip_existing = False
    download_instance.settings = SimpleNamespace(
        data=SimpleNamespace(
            filename_delimiter_artist=", ",
            filename_delimiter_album_artist=", ",
            format_track="Tracks/{artist_name} - {track_title}",
            use_primary_album_artist=False,
        ),
    )

    media = MagicMock()
    media.id = 111
    media.waves_identity_id = None
    # The plain download path records the fresh file under its id before
    # post-processing; the symlink step refuses a source without that evidence.
    download_instance._record_name_written(source_path, "111")

    with (
        patch("waves.download.format_path_media", return_value="Tracks/Artist - Title"),
        patch.object(download_instance, "_move_file", return_value=True),
        patch.object(download_instance, "_unlink_with_retry", return_value=False),
        patch.object(pathlib.Path, "symlink_to") as symlink_to_mock,
    ):
        result_path: pathlib.Path = download_instance.media_move_and_symlink(
            media,
            source_path,
            ".flac",
        )

    assert result_path == tmp_path / "library" / "Tracks" / "Artist - Title.flac"
    symlink_to_mock.assert_not_called()
    download_instance.fn_logger.error.assert_called_once()


def test_downsample_audio_raises_when_output_move_fails(
    download_instance: Download,
    tmp_path: pathlib.Path,
) -> None:
    """Verify downsample replacement failures are propagated.

    Args:
        download_instance (Download): Download instance under test.
        tmp_path (pathlib.Path): Temporary test directory.
    """
    source_path: pathlib.Path = tmp_path / "source.flac"
    source_path.write_text("audio", encoding="utf-8")
    download_instance.settings = SimpleNamespace(
        data=SimpleNamespace(
            downsample_target=DownsampleTarget.BIT16_48,
            path_binary_ffmpeg="ffmpeg",
        ),
    )
    flac_mock: MagicMock = MagicMock()
    flac_mock.info.sample_rate = 96000
    flac_mock.info.bits_per_sample = 24
    ffmpeg_mock: MagicMock = MagicMock()
    ffmpeg_mock.option.return_value = ffmpeg_mock
    ffmpeg_mock.input.return_value = ffmpeg_mock
    ffmpeg_mock.output.return_value = ffmpeg_mock

    with (
        patch("waves.download.FLAC", return_value=flac_mock),
        patch("waves.download.FFmpeg", return_value=ffmpeg_mock),
        patch.object(download_instance, "_move_file", return_value=False),
        pytest.raises(OSError),
    ):
        download_instance._downsample_audio(source_path)


def test_move_file_fresh_destination_is_atomic_and_clean(
    download_instance: Download,
    tmp_path: pathlib.Path,
) -> None:
    """Verify a fresh move places the complete file and leaves no temp artifacts.

    Args:
        download_instance (Download): Download instance under test.
        tmp_path (pathlib.Path): Temporary test directory.
    """
    source_path: pathlib.Path = tmp_path / "source.flac"
    destination_path: pathlib.Path = tmp_path / "dest.flac"
    source_path.write_bytes(b"audio-data")

    result: bool = download_instance._move_file(source_path, destination_path, overwrite=True)

    assert result is True
    assert destination_path.read_bytes() == b"audio-data"
    assert not source_path.exists()
    assert list(tmp_path.glob(".*.tmp")) == []


def test_move_file_cross_filesystem_completes_via_staged_copy(
    download_instance: Download,
    tmp_path: pathlib.Path,
) -> None:
    """Verify a fresh move still completes when a same-filesystem rename is impossible.

    Args:
        download_instance (Download): Download instance under test.
        tmp_path (pathlib.Path): Temporary test directory.
    """
    source_path: pathlib.Path = tmp_path / "source.flac"
    destination_path: pathlib.Path = tmp_path / "dest.flac"
    source_path.write_bytes(b"hi-res")

    replace_original = pathlib.Path.replace

    def replace_no_cross_device(self: pathlib.Path, target: pathlib.Path) -> pathlib.Path:
        # Force the direct source -> destination rename to fail like a cross-device
        # move, but allow the temp-sibling -> destination swap (same filesystem).
        if self == source_path:
            raise OSError("Invalid cross-device link")
        return replace_original(self, target)

    with patch.object(pathlib.Path, "replace", replace_no_cross_device):
        result: bool = download_instance._move_file(source_path, destination_path, overwrite=True)

    assert result is True
    assert destination_path.read_bytes() == b"hi-res"
    assert not source_path.exists()
    assert list(tmp_path.glob(".*.tmp")) == []


def test_move_file_interrupted_cross_filesystem_copy_leaves_no_partial(
    download_instance: Download,
    tmp_path: pathlib.Path,
) -> None:
    """Verify a crash mid-copy never leaves a half-written file under the real name.

    This is the guarantee that stops an interrupted download from blocking its own
    re-download: the destination must not exist, the source must survive for the
    retry, and the staged temp file must be cleaned up.

    Args:
        download_instance (Download): Download instance under test.
        tmp_path (pathlib.Path): Temporary test directory.
    """
    source_path: pathlib.Path = tmp_path / "source.flac"
    destination_path: pathlib.Path = tmp_path / "dest.flac"
    source_path.write_bytes(b"hi-res-audio")

    replace_original = pathlib.Path.replace

    def replace_no_cross_device(self: pathlib.Path, target: pathlib.Path) -> pathlib.Path:
        if self == source_path:
            raise OSError("Invalid cross-device link")
        return replace_original(self, target)

    def copy_crash(src: pathlib.Path, dst: pathlib.Path) -> None:
        # Write a partial temp file, then simulate a crash / power loss mid-copy.
        dst.write_bytes(b"hi-r")
        raise OSError("simulated interruption during copy")

    with (
        patch.object(pathlib.Path, "replace", replace_no_cross_device),
        patch.object(download_instance, "_copy_file_contents", copy_crash),
    ):
        result: bool = download_instance._move_file(source_path, destination_path, overwrite=True)

    assert result is False
    assert not destination_path.exists()
    assert source_path.read_bytes() == b"hi-res-audio"
    assert list(tmp_path.glob(".*.tmp")) == []


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")
def test_extract_flac_seals_unset_md5(download_instance: Download, tmp_path: pathlib.Path) -> None:
    """An extracted FLAC whose header came with no MD5 leaves with the real one.

    TIDAL's fragmented-MP4 packaging carries a STREAMINFO without MD5 or sample
    count, and the copy-out extraction keeps that header verbatim, so every
    extracted file failed ``flac -t``. Build that exact shape (FLAC with a zeroed
    header, packaged as DASH segments, concatenated like the segment merge does),
    run the real extraction, and check the header now matches the decoded PCM.
    """
    ffmpeg = shutil.which("ffmpeg")

    def run(*args: str) -> bytes:
        # argv is ffmpeg from PATH plus literal test arguments.
        return subprocess.run([ffmpeg, "-v", "error", *args], capture_output=True, check=True).stdout  # noqa: S603

    download_instance.settings = SimpleNamespace(data=SimpleNamespace(path_binary_ffmpeg=ffmpeg))
    src = tmp_path / "src.flac"
    run(
        "-y",
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=440:duration=2",
        "-ac",
        "2",
        "-ar",
        "48000",
        "-sample_fmt",
        "s32",
        "-bits_per_raw_sample",
        "24",
        "-c:a",
        "flac",
        str(src),
    )
    pcm = run("-i", str(src), "-f", "s24le", "-")
    md5_real = int.from_bytes(hashlib.md5(pcm).digest(), "big")  # noqa: S324
    assert FLAC(src).info.md5_signature == md5_real, "ffmpeg's own encoder writes a sound MD5"

    # The header TIDAL's packager ships: no signature, no sample count.
    zeroed = FLAC(src)
    zeroed.info.md5_signature = 0
    zeroed.info.total_samples = 0
    zeroed.save()
    dash = tmp_path / "dash"
    dash.mkdir()
    run("-y", "-i", str(src), "-c", "copy", "-f", "dash", "-seg_duration", "1", str(dash / "m.mpd"))
    merged = tmp_path / "merged"
    with merged.open("wb") as out:
        for part in [dash / "init-stream0.m4s", *sorted(dash.glob("chunk-stream0-*.m4s"))]:
            out.write(part.read_bytes())

    extracted = download_instance._extract_flac(merged)

    info = FLAC(extracted).info
    assert info.md5_signature == md5_real
    assert info.total_samples == len(pcm) // (3 * 2)
    assert info.bits_per_sample == 24

    # A header that already matches is left alone: a second pass writes nothing.
    download_instance.fn_logger.reset_mock()
    download_instance._flac_seal_streaminfo(extracted)
    download_instance.fn_logger.info.assert_not_called()

    # A signature that is set but wrong is damage, reported and never papered
    # over with the hash of whatever the audio now is.
    wrong = FLAC(extracted)
    wrong.info.md5_signature = md5_real ^ 1
    wrong.save()
    download_instance.fn_logger.reset_mock()
    download_instance._flac_seal_streaminfo(extracted)
    download_instance.fn_logger.warning.assert_called_once()
    assert FLAC(extracted).info.md5_signature == md5_real ^ 1


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")
@pytest.mark.parametrize("target", list(DownsampleTarget))
def test_downsample_audio_real_ffmpeg_writes_a_sound_flac(
    download_instance: Download, tmp_path: pathlib.Path, target: DownsampleTarget
) -> None:
    """Downsampling works on a build without soxr and the result verifies.

    The ffmpeg builds Waves ships with carry no libsoxr, where the soxr-only
    filter failed every downsample. Whatever this host's ffmpeg has, the
    output must land at the target, with the MD5 of its own audio.
    """
    ffmpeg = shutil.which("ffmpeg")

    def run(*args: str) -> bytes:
        # argv is ffmpeg from PATH plus literal test arguments.
        return subprocess.run([ffmpeg, "-v", "error", *args], capture_output=True, check=True).stdout  # noqa: S603

    download_instance.settings = SimpleNamespace(
        data=SimpleNamespace(path_binary_ffmpeg=ffmpeg, downsample_target=target.value)
    )
    src = tmp_path / "hires.flac"
    run(
        "-y",
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=440:duration=2",
        "-ac",
        "2",
        "-ar",
        "96000",
        "-sample_fmt",
        "s32",
        "-bits_per_raw_sample",
        "24",
        "-c:a",
        "flac",
        str(src),
    )

    assert download_instance._downsample_audio(src) == src

    info = FLAC(src).info
    assert (info.bits_per_sample, info.sample_rate) == (target.bit_depth, 48000)
    fmt = "s16le" if target.bit_depth == 16 else "s24le"
    pcm = run("-i", str(src), "-f", fmt, "-")
    assert info.md5_signature == int.from_bytes(hashlib.md5(pcm).digest(), "big")  # noqa: S324
    assert info.total_samples == len(pcm) // (2 * (target.bit_depth // 8))

    # A build without soxr was recognized by its own error and remembered.
    has_soxr = b"libsoxr" in run("-hide_banner", "-buildconf")
    assert download_mod._soxr_known_missing(ffmpeg) is not has_soxr


def test_downsample_audio_falls_back_to_swr_when_soxr_fails(
    download_instance: Download, tmp_path: pathlib.Path
) -> None:
    """The soxr attempt failing makes exactly one more attempt, on swr."""
    source_path = tmp_path / "source.flac"
    source_path.write_text("audio", encoding="utf-8")
    download_instance.settings = SimpleNamespace(
        data=SimpleNamespace(downsample_target=DownsampleTarget.BIT16_48, path_binary_ffmpeg="ffmpeg"),
    )
    flac_mock = MagicMock()
    flac_mock.info.sample_rate = 96000
    flac_mock.info.bits_per_sample = 24
    ffmpeg_mock = MagicMock()
    ffmpeg_mock.option.return_value = ffmpeg_mock
    ffmpeg_mock.input.return_value = ffmpeg_mock
    ffmpeg_mock.output.return_value = ffmpeg_mock
    ffmpeg_mock.execute.side_effect = _ffmpeg_runs(ffmpeg_mock, [_SOXR_STDERR, None])

    with (
        patch("waves.download.FLAC", return_value=flac_mock),
        patch("waves.download.FFmpeg", return_value=ffmpeg_mock),
        patch.object(download_instance, "_move_file", return_value=True),
    ):
        download_instance._downsample_audio(source_path)

    filters = [call.kwargs["af"] for call in ffmpeg_mock.output.call_args_list]
    assert len(filters) == 2
    assert filters[0].startswith("aresample=resampler=soxr:precision=33:osr=48000")
    assert filters[1] == "aresample=resampler=swr:osr=48000:osf=s16:dither_method=triangular"

    # The binary is now known to lack soxr: the next track spawns once, on swr.
    ffmpeg_mock.output.reset_mock()
    ffmpeg_mock.execute.side_effect = _ffmpeg_runs(ffmpeg_mock, [None])
    with (
        patch("waves.download.FLAC", return_value=flac_mock),
        patch("waves.download.FFmpeg", return_value=ffmpeg_mock),
        patch.object(download_instance, "_move_file", return_value=True),
    ):
        download_instance._downsample_audio(source_path)
    filters = [call.kwargs["af"] for call in ffmpeg_mock.output.call_args_list]
    assert filters == ["aresample=resampler=swr:osr=48000:osf=s16:dither_method=triangular"]

    # A different binary is asked afresh.
    download_instance.settings.data.path_binary_ffmpeg = "/other/ffmpeg"
    ffmpeg_mock.output.reset_mock()
    ffmpeg_mock.execute.side_effect = _ffmpeg_runs(ffmpeg_mock, [None])
    with (
        patch("waves.download.FLAC", return_value=flac_mock),
        patch("waves.download.FFmpeg", return_value=ffmpeg_mock),
        patch.object(download_instance, "_move_file", return_value=True),
    ):
        download_instance._downsample_audio(source_path)
    assert ffmpeg_mock.output.call_args_list[0].kwargs["af"].startswith("aresample=resampler=soxr")


def test_downsample_audio_other_failure_raises_without_a_second_run(
    download_instance: Download, tmp_path: pathlib.Path
) -> None:
    """Only a missing soxr moves on to swr; any other ffmpeg failure is the real one."""
    source_path = tmp_path / "source.flac"
    source_path.write_text("audio", encoding="utf-8")
    download_instance.settings = SimpleNamespace(
        data=SimpleNamespace(downsample_target=DownsampleTarget.BIT16_48, path_binary_ffmpeg="ffmpeg"),
    )
    flac_mock = MagicMock()
    flac_mock.info.sample_rate = 96000
    flac_mock.info.bits_per_sample = 24
    ffmpeg_mock = MagicMock()
    ffmpeg_mock.option.return_value = ffmpeg_mock
    ffmpeg_mock.input.return_value = ffmpeg_mock
    ffmpeg_mock.output.return_value = ffmpeg_mock
    disk_full = ["Error writing trailer: No space left on device"]
    ffmpeg_mock.execute.side_effect = _ffmpeg_runs(ffmpeg_mock, [disk_full, None])

    with (
        patch("waves.download.FLAC", return_value=flac_mock),
        patch("waves.download.FFmpeg", return_value=ffmpeg_mock),
        patch.object(download_instance, "_move_file", return_value=True),
        pytest.raises(FFmpegError, match="No space left on device"),
    ):
        download_instance._downsample_audio(source_path)

    assert ffmpeg_mock.output.call_count == 1
    assert not download_mod._soxr_known_missing("ffmpeg")


def _extract_settings(**extra) -> SimpleNamespace:
    return SimpleNamespace(data=SimpleNamespace(path_binary_ffmpeg="ffmpeg", **extra))


def _ffmpeg_ok() -> MagicMock:
    ffmpeg_mock = MagicMock()
    ffmpeg_mock.option.return_value = ffmpeg_mock
    ffmpeg_mock.input.return_value = ffmpeg_mock
    ffmpeg_mock.output.return_value = ffmpeg_mock
    return ffmpeg_mock


@pytest.mark.parametrize(
    ("enabled", "rate", "depth", "sealed"),
    [
        (True, 96000, 24, False),  # both drop: the encoder writes the header
        (True, 44100, 24, False),  # depth drops only
        (True, 44100, 16, True),  # already at the target: the copy stays as is
        (False, 96000, 24, True),  # downsampling off
    ],
)
def test_extract_flac_seals_only_when_the_downsample_keeps_the_copy(
    download_instance: Download, tmp_path: pathlib.Path, enabled: bool, rate: int, depth: int, sealed: bool
) -> None:
    """A track the downsample stage re-encodes is not decoded once more for a seal."""
    download_instance.settings = _extract_settings(
        downsample_enabled=enabled, downsample_target=DownsampleTarget.BIT16_48
    )
    flac_mock = MagicMock()
    flac_mock.info.sample_rate = rate
    flac_mock.info.bits_per_sample = depth

    with (
        patch("waves.download.FFmpeg", return_value=_ffmpeg_ok()),
        patch("waves.download.FLAC", return_value=flac_mock),
        patch("waves.download.seal_file") as seal_mock,
    ):
        seal_mock.return_value = SimpleNamespace(status=flac_verify.OK, detail="")
        out = download_instance._extract_flac(tmp_path / "merged")

    assert out == tmp_path / "merged.flac"
    assert seal_mock.called is sealed


def test_extract_flac_seal_unreadable_header_still_seals(download_instance: Download, tmp_path: pathlib.Path) -> None:
    """When the header cannot be read the seal runs, which is always safe."""
    download_instance.settings = _extract_settings(downsample_enabled=True, downsample_target=DownsampleTarget.BIT16_48)
    with (
        patch("waves.download.FFmpeg", return_value=_ffmpeg_ok()),
        patch("waves.download.FLAC", side_effect=OSError("unreadable")),
        patch("waves.download.seal_file") as seal_mock,
    ):
        seal_mock.return_value = SimpleNamespace(status=flac_verify.OK, detail="")
        download_instance._extract_flac(tmp_path / "merged")

    seal_mock.assert_called_once()


def test_extract_flac_seal_follows_the_items_stop(download_instance: Download, tmp_path: pathlib.Path) -> None:
    """The seal's decode is told to stop with the item, and a stopped seal is quiet."""
    download_instance.settings = _extract_settings()
    download_instance.event_abort = Event()
    event_stop = Event()
    seen: list[bool] = []

    def seal(path, ffmpeg, *, fill_sample_count, should_continue):
        assert fill_sample_count is True
        seen.append(should_continue())
        event_stop.set()
        seen.append(should_continue())
        return SimpleNamespace(status=flac_verify.CANCELLED, detail="")

    with (
        patch("waves.download.FFmpeg", return_value=_ffmpeg_ok()),
        patch("waves.download.seal_file", side_effect=seal),
    ):
        download_instance._extract_flac(tmp_path / "merged", event_stop)

    assert seen == [True, False]
    download_instance.fn_logger.warning.assert_not_called()
    download_instance.fn_logger.exception.assert_not_called()
    download_instance.fn_logger.info.assert_not_called()

    # The whole run's abort stops it too.
    download_instance.event_abort.set()
    calls: list[bool] = []

    def seal_abort(path, ffmpeg, *, fill_sample_count, should_continue):
        calls.append(should_continue())
        return SimpleNamespace(status=flac_verify.CANCELLED, detail="")

    with (
        patch("waves.download.FFmpeg", return_value=_ffmpeg_ok()),
        patch("waves.download.seal_file", side_effect=seal_abort),
    ):
        download_instance._extract_flac(tmp_path / "merged", Event())
    assert calls == [False]


@pytest.mark.parametrize("stopper", ["none", "item", "abort"])
def test_stop_during_the_seal_lands_nothing(download_instance: Download, tmp_path: pathlib.Path, stopper: str) -> None:
    """A seal cut short by a stop leaves an unsealed header, so the file never moves into place."""
    download_instance.settings = SimpleNamespace(
        data=SimpleNamespace(
            video_convert_mp4=False,
            extract_flac=True,
            downsample_enabled=False,
            path_binary_ffmpeg="ffmpeg",
        )
    )
    download_instance.skip_existing = False
    download_instance.event_abort = Event()
    event_stop = Event()
    media = Track.__new__(Track)
    media.id = "123"
    media.waves_identity_id = None
    dst = tmp_path / "song.flac"

    def extract(path, stop=None):
        # The stop arrives while the seal decodes; the seal reports CANCELED.
        if stopper == "item":
            event_stop.set()
        elif stopper == "abort":
            download_instance.event_abort.set()
        return path

    cls = Download
    with (
        patch.object(cls, "_download", return_value=(True, tmp_path / "raw")),
        patch.object(cls, "_extract_flac", side_effect=extract),
        patch.object(cls, "_already_landed_here", return_value=None),
        patch.object(cls, "_claim_destination", return_value=(dst, "reserved")),
        patch.object(cls, "_release_name"),
        patch.object(cls, "_handle_metadata_and_extras", return_value=None),
        patch.object(cls, "_move_file", return_value=True) as move_mock,
        patch.object(cls, "_record_name_written"),
        patch.object(cls, "_note_dir_filled"),
        patch("waves.download.name_builder_item", return_value="x"),
    ):
        ok, _ = download_instance._perform_actual_download(
            media, dst, None, True, False, SimpleNamespace(is_bts=True), event_stop
        )

    if stopper == "none":
        assert ok is True
        move_mock.assert_called_once()
    else:
        assert ok is False
        move_mock.assert_not_called()


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")
def test_extract_then_downsample_real_ffmpeg_skips_the_seal_and_verifies(
    download_instance: Download, tmp_path: pathlib.Path
) -> None:
    """A hi-res track with a zeroed header, extracted and downsampled, verifies without a seal.

    The extraction skips the seal because the downsample re-encodes the file;
    ffmpeg's FLAC encoder then writes the signature and sample count of the
    new audio itself, which a full decode confirms.
    """
    ffmpeg = shutil.which("ffmpeg")

    def run(*args: str) -> bytes:
        # argv is ffmpeg from PATH plus literal test arguments.
        return subprocess.run([ffmpeg, "-v", "error", *args], capture_output=True, check=True).stdout  # noqa: S603

    download_instance.settings = SimpleNamespace(
        data=SimpleNamespace(
            path_binary_ffmpeg=ffmpeg,
            downsample_enabled=True,
            downsample_target=DownsampleTarget.BIT16_48.value,
        )
    )
    src = tmp_path / "src.flac"
    run(
        "-y",
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=440:duration=2",
        "-ac",
        "2",
        "-ar",
        "96000",
        "-sample_fmt",
        "s32",
        "-bits_per_raw_sample",
        "24",
        "-c:a",
        "flac",
        str(src),
    )
    zeroed = FLAC(src)
    zeroed.info.md5_signature = 0
    zeroed.info.total_samples = 0
    zeroed.save()
    dash = tmp_path / "dash"
    dash.mkdir()
    run("-y", "-i", str(src), "-c", "copy", "-f", "dash", "-seg_duration", "1", str(dash / "m.mpd"))
    merged = tmp_path / "merged"
    with merged.open("wb") as out:
        for part in [dash / "init-stream0.m4s", *sorted(dash.glob("chunk-stream0-*.m4s"))]:
            out.write(part.read_bytes())

    with patch("waves.download.seal_file", wraps=download_mod.seal_file) as seal_spy:
        extracted = download_instance._extract_flac(merged)
    seal_spy.assert_not_called()
    assert FLAC(extracted).info.md5_signature == 0, "the copy is left for the encoder to replace"

    assert download_instance._downsample_audio(extracted) == extracted

    info = FLAC(extracted).info
    assert (info.bits_per_sample, info.sample_rate) == (16, 48000)
    row = flac_verify.check_file(str(extracted), ffmpeg, deep=True)
    assert row.status == flac_verify.OK
    assert row.detail == ""
    assert info.total_samples == row.samples_decoded
