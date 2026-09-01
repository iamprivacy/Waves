"""A Dolby Atmos file is never replaced by a download in another audio mode.

TIDAL delivers Atmos as an MP4 (.m4a) at its 320k request tier, the very same
extension stereo AAC uses, so the two resolve to one destination name. Three
doors used to walk through it: the "Skip existing" setting turned off,
REDOWNLOAD (which forces skipping off per track), and a quality upgrade (the
same force). All three land in _claim_destination, so the protection is pinned
there: a fetch whose audio mode differs from the file already holding a name
treats that name as taken and steps aside to a numbered variant, in BOTH
directions (a stereo fetch spares an Atmos file, an Atmos fetch spares a
stereo one). Same mode keeps the historical answer, so a genuine upgrade
still replaces its own copy in place.

The mode of the occupant is read off the disk (waves.download's
_file_audio_mode_is_atmos), never out of a ledger, so a user's own Atmos file
is protected the same as one Waves wrote, tagged or not.
"""

import pathlib
import threading
from unittest.mock import MagicMock

import pytest

import waves.download as download_mod
from waves.download import Download, _file_audio_mode_is_atmos
from waves.waves_ui.backend import _TrackedDownload

ATMOS_MARK = b"|atmos"


@pytest.fixture(autouse=True)
def _identity_from_content(monkeypatch):
    """Same stand-in test_overwrite_mode_collisions uses: a file's id is its
    content, so identity is read back without building tagged audio."""

    def _read(path_file) -> str:
        try:
            raw: bytes = pathlib.Path(path_file).read_bytes()
        except OSError:
            return ""

        raw = raw.removesuffix(ATMOS_MARK)

        return raw[3:].decode() if raw.startswith(b"id-") and raw[3:].isdigit() else ""

    monkeypatch.setattr("waves.download.read_item_id", _read)


@pytest.fixture(autouse=True)
def _mode_from_content(monkeypatch):
    """A file whose content ends in the Atmos mark reads as an Atmos copy.

    The real reader parses the MP4 codec box, which cannot be faked with a
    text file; it has its own tests below. Everything else here exercises the
    decision built on its answer.
    """

    def _mode(path_file) -> bool | None:
        try:
            raw: bytes = pathlib.Path(path_file).read_bytes()
        except OSError:
            return None

        return raw.endswith(ATMOS_MARK)

    monkeypatch.setattr("waves.download._file_audio_mode_is_atmos", _mode)


def _make_download(tmp_path: pathlib.Path, skip_existing: bool, cls: type[Download] = Download) -> Download:
    dl = cls(
        tidal_obj=MagicMock(),
        skip_existing=skip_existing,
        path_base=str(tmp_path),
        fn_logger=MagicMock(),
        progress=MagicMock(),
    )
    dl.settings = MagicMock()
    dl.event_abort = threading.Event()
    dl.event_run = threading.Event()
    dl.event_run.set()

    return dl


def _occupy(path: pathlib.Path, track_id: str, atmos: bool) -> None:
    path.write_bytes(b"id-" + track_id.encode() + (ATMOS_MARK if atmos else b""))


class TestTheModeGateInTheClaim:
    """_claim_destination with skipping off, the overwrite regime itself."""

    def test_a_stereo_fetch_steps_aside_from_an_atmos_occupant(self, tmp_path):
        # Same track id, so the OLD rule said "its own to replace". The mode
        # difference must win: this is the exact Atmos-loss door.
        dl = _make_download(tmp_path, skip_existing=False)
        base = tmp_path / "Song.m4a"
        _occupy(base, "123", atmos=True)

        claimed, reservation = dl._claim_destination(base, "123", fetch_is_atmos=False)

        assert claimed != base, "a stereo download took the Atmos file's name"
        assert claimed.name == "Song_01.m4a"
        assert reservation is not None
        assert base.read_bytes().endswith(ATMOS_MARK), "the Atmos file itself is untouched"

    def test_an_atmos_fetch_steps_aside_from_a_stereo_occupant(self, tmp_path):
        # The other direction: downloading Atmos must not eat the stereo copy.
        dl = _make_download(tmp_path, skip_existing=False)
        base = tmp_path / "Song.m4a"
        _occupy(base, "123", atmos=False)

        claimed, _reservation = dl._claim_destination(base, "123", fetch_is_atmos=True)

        assert claimed.name == "Song_01.m4a"

    def test_a_same_mode_own_copy_is_still_replaced_in_place(self, tmp_path):
        # The point of skipping off, and of a quality upgrade: the fetch lands
        # ON its own older copy, no numbered duplicate.
        dl = _make_download(tmp_path, skip_existing=False)
        base = tmp_path / "Song.m4a"
        _occupy(base, "123", atmos=False)

        claimed, _reservation = dl._claim_destination(base, "123", fetch_is_atmos=False)

        assert claimed == base

    def test_an_untagged_atmos_file_is_protected_too(self, tmp_path):
        # No id at all used to mean "treat as mine". The mode is asked BEFORE
        # the id, so a user's own untagged Atmos file survives a refresh.
        dl = _make_download(tmp_path, skip_existing=False)
        base = tmp_path / "Song.m4a"
        base.write_bytes(b"somebody's untagged audio" + ATMOS_MARK)

        claimed, _reservation = dl._claim_destination(base, "123", fetch_is_atmos=False)

        assert claimed.name == "Song_01.m4a"

    def test_an_unknown_fetch_mode_keeps_the_historical_answer(self, tmp_path):
        # fetch_is_atmos=None means the caller cannot say; the gate must stand
        # down rather than guess, or every legacy call path changes behaviour.
        dl = _make_download(tmp_path, skip_existing=False)
        base = tmp_path / "Song.m4a"
        _occupy(base, "123", atmos=True)

        claimed, _reservation = dl._claim_destination(base, "123", fetch_is_atmos=None)

        assert claimed == base


class TestTheRedownloadDoor:
    """REDOWNLOAD and a quality upgrade force skipping off per thread through
    _TrackedDownload._force_download; the mode gate must hold inside it. This
    also pins the thread-local override itself on the real _TrackedDownload,
    which no test observed before (the old ones built a bare Download)."""

    def test_the_override_is_per_thread_and_restored(self, tmp_path):
        dl = _make_download(tmp_path, skip_existing=True, cls=_TrackedDownload)
        assert dl.skip_existing is True

        seen_on_other_thread: list[bool] = []

        with dl._force_download():
            assert dl.skip_existing is False, "the override did not reach the property"

            worker = threading.Thread(target=lambda: seen_on_other_thread.append(dl.skip_existing))
            worker.start()
            worker.join()

        assert seen_on_other_thread == [True], "the force leaked onto a sibling thread"
        assert dl.skip_existing is True, "the override outlived its context"

    def test_a_forced_redownload_cannot_take_an_atmos_name(self, tmp_path):
        # The sharpest door: Atmos downloaded, setting later off, REDOWNLOAD
        # clicked. The forced stereo fetch must land beside the Atmos file.
        dl = _make_download(tmp_path, skip_existing=True, cls=_TrackedDownload)
        base = tmp_path / "Song.m4a"
        _occupy(base, "123", atmos=True)

        with dl._force_download():
            claimed, _reservation = dl._claim_destination(base, "123", fetch_is_atmos=False)

        assert claimed.name == "Song_01.m4a"
        assert base.read_bytes().endswith(ATMOS_MARK)

    def test_a_forced_same_mode_upgrade_still_replaces_in_place(self, tmp_path):
        # The force exists so an upgrade overwrites its own copy; same mode
        # must keep doing exactly that.
        dl = _make_download(tmp_path, skip_existing=True, cls=_TrackedDownload)
        base = tmp_path / "Song.m4a"
        _occupy(base, "123", atmos=False)

        with dl._force_download():
            claimed, _reservation = dl._claim_destination(base, "123", fetch_is_atmos=False)

        assert claimed == base


class TestTheOnDiskModeReader:
    """_file_audio_mode_is_atmos itself, unpatched."""

    def test_a_non_mp4_extension_is_stereo_without_reading(self, tmp_path):
        # Answered from the suffix alone: the path does not even exist.
        assert _file_audio_mode_is_atmos(tmp_path / "Song.flac") is False
        assert _file_audio_mode_is_atmos(tmp_path / "Song.mp3") is False

    def test_an_unreadable_m4a_answers_unknown(self, tmp_path):
        broken = tmp_path / "Song.m4a"
        broken.write_bytes(b"not an mp4 container")

        assert _file_audio_mode_is_atmos(broken) is None

    def test_a_missing_m4a_answers_unknown(self, tmp_path):
        assert _file_audio_mode_is_atmos(tmp_path / "gone.m4a") is None

    @pytest.mark.parametrize(
        ("codec", "verdict"),
        [
            ("ec-3", True),  # E-AC-3 JOC, how TIDAL ships Atmos
            ("ac-4", True),  # the other Atmos coding
            ("mp4a.40.2", False),  # plain stereo AAC
            ("alac", False),
            ("", False),
        ],
    )
    def test_the_codec_decides(self, tmp_path, monkeypatch, codec, verdict):
        path = tmp_path / "Song.m4a"
        path.write_bytes(b"stand-in")

        class _Info:
            pass

        class _MP4:
            def __init__(self, _path):
                self.info = _Info()
                self.info.codec = codec

        monkeypatch.setattr(download_mod, "MP4", _MP4)

        assert _file_audio_mode_is_atmos(path) is verdict
