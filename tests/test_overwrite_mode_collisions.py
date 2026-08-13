"""Colliding tracks stay distinct even when the run is allowed to overwrite.

The issue-15 name claim only ran when "skip existing files" was on. Two other
paths run with it off and so bypassed it entirely:

- the setting itself turned off, which means "re-download what I already have",
  not "throw one of two distinct tracks away";
- a quality upgrade, which turns skipping off for one track on one thread
  (``_TrackedDownload._force_download``) so the old copy is replaced in place.

Both then moved with overwrite on and no claim, so a sibling track whose name
sanitizes the same way could be overwritten by them, or overwrite them.

The claim is now taken in every mode. What differs is only what a name is
compared against: an on-disk file blocks a name when skipping is on and is
meant to be replaced when it is off, while a name another download is holding
in flight is never available to anybody.
"""

import pathlib
import threading
from unittest.mock import MagicMock

import pytest
from tidalapi.media import Track

from tidaler.download import Download


@pytest.fixture(autouse=True)
def _identity_from_content(monkeypatch):
    """Let a file answer who it is without building a tagged FLAC per track.

    The engine asks metadata.read_item_id, which reads the item id the download
    wrote into the file's tags (exercised against real tags in
    test_issue15_atmos_and_duplicates). The stand-in downloads here write that
    id as the file's whole content, so identity is read back from it. Anything
    else reads as untagged, which is what a pre-id library file is.
    """

    def _read(path_file) -> str:
        try:
            raw: bytes = pathlib.Path(path_file).read_bytes()
        except OSError:
            return ""

        return raw[3:].decode() if raw.startswith(b"id-") and raw[3:].isdigit() else ""

    monkeypatch.setattr("tidaler.download.read_item_id", _read)


def _make_download(tmp_path: pathlib.Path, skip_existing: bool, cls: type[Download] = Download) -> Download:
    dl = cls(
        tidal_obj=MagicMock(),
        skip_existing=skip_existing,
        path_base=str(tmp_path),
        fn_logger=MagicMock(),
        progress=MagicMock(),
    )
    dl.settings = MagicMock()
    dl.settings.data.video_convert_mp4 = False
    dl.settings.data.extract_flac = False
    dl.settings.data.downsample_enabled = False
    dl.settings.data.path_binary_ffmpeg = ""
    dl.event_abort = threading.Event()
    dl.event_run = threading.Event()
    dl.event_run.set()

    def _download(media, stream_manifest, path_file, event_stop=None):
        path_file.write_bytes(b"id-" + str(media.id).encode())

        return True, path_file

    dl._download = _download

    return dl


def _track(track_id: int) -> Track:
    t = Track.__new__(Track)
    t.id = track_id
    t.audio_modes = []
    t.artists = []
    t.name = "Song"
    t.version = None

    return t


def _run_pair(dl: Download, destination: pathlib.Path, track_ids: tuple[int, int]) -> dict[int, tuple]:
    """Run two downloads at the same destination, both held in the claim window."""
    barrier = threading.Barrier(2, timeout=10)

    def _extras(*args, **kwargs):
        barrier.wait()

    dl._handle_metadata_and_extras = _extras

    results: dict[int, tuple] = {}

    def _run(track_id: int) -> None:
        results[track_id] = dl._perform_actual_download(
            media=_track(track_id),
            path_media_dst=destination,
            stream_manifest=MagicMock(),
            do_flac_extract=False,
            is_parent_album=False,
            media_stream=None,
        )

    threads = [threading.Thread(target=_run, args=(track_id,)) for track_id in track_ids]

    for thread in threads:
        thread.start()

    for thread in threads:
        thread.join(timeout=20)

    return results


def _run_batches(dl: Download, destination: pathlib.Path, batches: list[tuple[int, ...]]) -> dict[int, tuple]:
    """Run each batch of tracks at one destination, a batch at a time.

    What a real album does: downloads_concurrent_max tracks run together, and
    the next ones only start once those have landed. Within a batch the tracks
    are held in the claim window together, exactly as _run_pair holds two.
    """
    results: dict[int, tuple] = {}

    for batch in batches:
        barrier = threading.Barrier(len(batch), timeout=10)

        def _extras(*args, _barrier=barrier, **kwargs) -> None:
            # No return value: what comes back from here is the sidecar tuple.
            _barrier.wait()

        dl._handle_metadata_and_extras = _extras

        def _run(track_id: int) -> None:
            results[track_id] = dl._perform_actual_download(
                media=_track(track_id),
                path_media_dst=destination,
                stream_manifest=MagicMock(),
                do_flac_extract=False,
                is_parent_album=False,
                media_stream=None,
            )

        threads = [threading.Thread(target=_run, args=(track_id,)) for track_id in batch]

        for thread in threads:
            thread.start()

        for thread in threads:
            thread.join(timeout=20)

    return results


class TestOverwriteModeStillKeepsBothTracks:
    def test_two_colliding_tracks_both_land_with_skipping_off(self, tmp_path):
        # "Skip existing files" off means replace what is already in the
        # library, not discard one of two tracks that share a name.
        dl = _make_download(tmp_path, skip_existing=False)

        results = _run_pair(dl, tmp_path / "Song.flac", (111, 222))

        assert all(ok for ok, _ in results.values()), "a finished download must never be dropped"

        paths = [path for _, path in results.values()]
        assert {p.name for p in paths} == {"Song.flac", "Song_01.flac"}
        assert {p.read_bytes() for p in paths} == {b"id-111", b"id-222"}
        assert dl._names_reserved == set(), "claims are released once the files are in place"

    def test_an_existing_file_is_still_replaced_with_skipping_off(self, tmp_path):
        # The setting's whole purpose: a file already there is overwritten,
        # not sidestepped with a numbered copy.
        dl = _make_download(tmp_path, skip_existing=False)
        destination = tmp_path / "Song.flac"
        destination.write_bytes(b"the old copy")

        ok, path = dl._perform_actual_download(
            media=_track(111),
            path_media_dst=destination,
            stream_manifest=MagicMock(),
            do_flac_extract=False,
            is_parent_album=False,
            media_stream=None,
        )

        assert ok is True
        assert path == destination
        assert destination.read_bytes() == b"id-111"
        assert list(tmp_path.iterdir()) == [destination]


class TestOverwriteModeKeepsTracksItAlreadyWrote:
    """Issue #19: the album of six same-title tracks, downloaded three at a time.

    The claim only spans the window between picking a name and moving the file
    there, which is right for two tracks running side by side and blind to the
    rest of the run: with skipping off nothing looks at the disk either, so the
    fourth track found the first track's name free again the moment that first
    track landed, and replaced it. Six downloads, four files, and a different
    four on every run (the reporter's two attempts kept a different pair).
    """

    def test_six_same_name_tracks_land_as_six_files(self, tmp_path):
        dl = _make_download(tmp_path, skip_existing=False)
        destination = tmp_path / "I Feel You.flac"

        # downloads_concurrent_max is 3 by default: three tracks run together,
        # the next three start once those have landed.
        results = _run_batches(dl, destination, [(1, 2, 3), (4, 5, 6)])

        assert all(ok for ok, _ in results.values()), "a finished download must never be dropped"
        assert len(list(tmp_path.iterdir())) == 6, "six distinct tracks, six files"
        assert {path.read_bytes() for _, path in results.values()} == {
            b"id-1",
            b"id-2",
            b"id-3",
            b"id-4",
            b"id-5",
            b"id-6",
        }, "no track may be overwritten by a later one sharing its name"

    def test_six_same_name_tracks_land_as_six_files_with_skipping_on(self, tmp_path):
        # The same album with the setting left alone, which is the mode the
        # disk check already covered. Pinned so the two modes cannot drift.
        dl = _make_download(tmp_path, skip_existing=True)
        destination = tmp_path / "I Feel You.flac"

        results = _run_batches(dl, destination, [(1, 2, 3), (4, 5, 6)])

        assert all(ok for ok, _ in results.values())
        assert len(list(tmp_path.iterdir())) == 6
        assert {path.read_bytes() for _, path in results.values()} == {
            b"id-1",
            b"id-2",
            b"id-3",
            b"id-4",
            b"id-5",
            b"id-6",
        }

    def test_downloading_the_album_a_second_time_keeps_six_files(self, tmp_path):
        # The reporter's second attempt: the same album again, over the library
        # the first run wrote. A fresh run holds no ledger, so the six files
        # have to answer for themselves.
        destination = tmp_path / "I Feel You.flac"
        _run_batches(_make_download(tmp_path, skip_existing=False), destination, [(1, 2, 3), (4, 5, 6)])

        dl = _make_download(tmp_path, skip_existing=False)
        results = _run_batches(dl, destination, [(1, 2, 3), (4, 5, 6)])

        assert all(ok for ok, _ in results.values())
        assert len(list(tmp_path.iterdir())) == 6
        assert {path.read_bytes() for _, path in results.values()} == {
            b"id-1",
            b"id-2",
            b"id-3",
            b"id-4",
            b"id-5",
            b"id-6",
        }

    def test_the_same_track_twice_replaces_its_own_file(self, tmp_path):
        # A playlist listing one track twice. The ledger is keyed by item, so
        # this is still one song in one file: numbering it would hand the user
        # a duplicate the setting never asked for.
        dl = _make_download(tmp_path, skip_existing=False)
        destination = tmp_path / "I Feel You.flac"

        _run_batches(dl, destination, [(1,), (1,)])

        assert [p.name for p in tmp_path.iterdir()] == ["I Feel You.flac"]

    def test_a_track_downloaded_alone_steps_around_a_stranger(self, tmp_path):
        # Nothing in flight and nothing this run wrote: the single track the
        # user re-downloads carries the whole answer on disk. Its own older
        # copy is at the numbered name (that is where the collision put it),
        # while the base name holds a different track that may not be lost.
        dl = _make_download(tmp_path, skip_existing=False)
        (tmp_path / "I Feel You.flac").write_bytes(b"id-1")
        (tmp_path / "I Feel You_01.flac").write_bytes(b"id-2")

        ok, path = dl._perform_actual_download(
            media=_track(2),
            path_media_dst=tmp_path / "I Feel You.flac",
            stream_manifest=MagicMock(),
            do_flac_extract=False,
            is_parent_album=False,
            media_stream=None,
        )

        assert ok is True
        assert path.name == "I Feel You_01.flac", "an upgrade replaces its own copy, wherever that sits"
        assert (tmp_path / "I Feel You.flac").read_bytes() == b"id-1", "the stranger at the base name stays"
        assert path.read_bytes() == b"id-2"


class _PerThreadSkip(Download):
    """The shape ``_TrackedDownload`` gives a quality upgrade, and nothing else.

    A run downloads with skipping on, and turns it off for exactly the track
    being upgraded, on exactly that pool thread, so the old copy is replaced in
    place while its siblings keep skipping normally.
    """

    def __init__(self, *args, **kwargs) -> None:
        self._tls = threading.local()
        self._skip_existing_base = True
        super().__init__(*args, **kwargs)

    @property
    def skip_existing(self) -> bool:
        override = getattr(self._tls, "skip_existing", None)

        return self._skip_existing_base if override is None else override

    @skip_existing.setter
    def skip_existing(self, value: bool) -> None:
        self._skip_existing_base = bool(value)


class TestAQualityUpgradeKeepsItsSiblings:
    def test_the_upgrade_lands_in_place_and_the_sibling_beside_it(self, tmp_path):
        # The upgrade replaces the copy at its own name; the new track sharing
        # that name has to land beside it, not under it.
        dl = _make_download(tmp_path, skip_existing=True, cls=_PerThreadSkip)

        destination = tmp_path / "Song.flac"
        destination.write_bytes(b"the low quality copy")

        barrier = threading.Barrier(2, timeout=10)

        def _extras(*args, **kwargs):
            barrier.wait()

        dl._handle_metadata_and_extras = _extras

        results: dict[int, tuple] = {}

        def _run(track_id: int, upgrade: bool) -> None:
            if upgrade:
                dl._tls.skip_existing = False
            results[track_id] = dl._perform_actual_download(
                media=_track(track_id),
                path_media_dst=destination,
                stream_manifest=MagicMock(),
                do_flac_extract=False,
                is_parent_album=False,
                media_stream=None,
            )

        threads = [
            threading.Thread(target=_run, args=(111, True)),
            threading.Thread(target=_run, args=(222, False)),
        ]

        for thread in threads:
            thread.start()

        for thread in threads:
            thread.join(timeout=20)

        assert all(ok for ok, _ in results.values())
        assert results[111][1] == destination, "an upgrade has to replace the copy it upgrades"
        assert destination.read_bytes() == b"id-111"
        assert results[222][1].name == "Song_01.flac"
        assert results[222][1].read_bytes() == b"id-222"
        assert dl._names_reserved == set()

    def test_upgrading_two_colliding_tracks_loses_neither(self, tmp_path):
        # The album's two same-name mixes are both in the library, the second
        # as the numbered copy the collision made, and both are being upgraded.
        # Each upgrade computes the SAME base name (the numbered spelling is
        # not recoverable from the track), so with skipping off on both threads
        # they aimed at one file: one mix overwrote the other and the loser's
        # old low-quality copy stayed behind under its numbered name.
        dl = _make_download(tmp_path, skip_existing=True, cls=_PerThreadSkip)
        (tmp_path / "Song.flac").write_bytes(b"old id-111")
        (tmp_path / "Song_01.flac").write_bytes(b"old id-222")

        barrier = threading.Barrier(2, timeout=10)

        def _extras(*args, **kwargs):
            barrier.wait()

        dl._handle_metadata_and_extras = _extras

        results: dict[int, tuple] = {}

        def _run(track_id: int) -> None:
            dl._tls.skip_existing = False
            results[track_id] = dl._perform_actual_download(
                media=_track(track_id),
                path_media_dst=tmp_path / "Song.flac",
                stream_manifest=MagicMock(),
                do_flac_extract=False,
                is_parent_album=False,
                media_stream=None,
            )

        threads = [threading.Thread(target=_run, args=(track_id,)) for track_id in (111, 222)]

        for thread in threads:
            thread.start()

        for thread in threads:
            thread.join(timeout=20)

        assert all(ok for ok, _ in results.values())
        landed = {path.read_bytes() for _, path in results.values()}
        assert landed == {b"id-111", b"id-222"}, "an upgrade may not overwrite the mix beside it"
        assert {p.name for _, p in results.values()} == {"Song.flac", "Song_01.flac"}
        assert dl._names_reserved == set()


class TestTrackedDownloadForcesSkippingOffPerThread:
    def test_force_download_is_thread_local(self):
        # The link the tests above stand on: the upgrade context manager flips
        # skipping for the calling thread only.
        from tidaler.waves_ui.backend import _TrackedDownload

        dl = _TrackedDownload.__new__(_TrackedDownload)
        dl._tls = threading.local()
        dl._skip_existing_base = True

        seen: dict[str, bool] = {}

        def _sibling() -> None:
            seen["sibling"] = dl.skip_existing

        with dl._force_download():
            seen["upgrading"] = dl.skip_existing
            thread = threading.Thread(target=_sibling)
            thread.start()
            thread.join(timeout=10)

        assert seen == {"upgrading": False, "sibling": True}
        assert dl.skip_existing is True, "the override is restored on exit"
