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

from tidalapi.media import Track

from tidaler.download import Download


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
