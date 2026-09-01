"""The m3u8 lists a playlist's tracks in the playlist's own order (issue #22).

The writer used to glob the finished directory and sort by filename (or file
age), which reconstructs an album's order from numbered filenames but knows
nothing of a playlist's: a 300-song playlist landed in whatever order the names
happened to sort. The collection download now hands the writer its landed file
paths in list order, and the playlist reproduces that order verbatim.

The futures collector is the order's source, so it is pinned too: results are
gathered in submission order, never completion order.
"""

import pathlib
import threading
from concurrent.futures import Future
from unittest.mock import MagicMock

from waves.download import Download


def _make_download(tmp_path: pathlib.Path) -> Download:
    dl = Download(
        tidal_obj=MagicMock(),
        skip_existing=True,
        path_base=str(tmp_path),
        fn_logger=MagicMock(),
        progress=MagicMock(),
    )
    dl.settings = MagicMock()
    dl.settings.data.filename_illegal_replacement = ""
    dl.settings.data.filename_illegal_map = None
    dl.event_abort = threading.Event()
    dl.event_run = threading.Event()
    dl.event_run.set()

    return dl


class TestTheListOrderReachesTheM3u:
    def test_entries_follow_the_given_order_not_the_filenames(self, tmp_path):
        # Names chosen to sort the OPPOSITE way, and the alphabetical flag set,
        # to prove the handed order wins over both.
        dl = _make_download(tmp_path)
        (tmp_path / "Zulu.flac").write_bytes(b"a")
        (tmp_path / "Alpha.flac").write_bytes(b"b")

        written = dl.playlist_populate(
            {tmp_path},
            "My List",
            is_album=False,
            sort_alphabetically=True,
            paths_ordered=[tmp_path / "Zulu.flac", tmp_path / "Alpha.flac"],
        )

        assert written[0].read_text(encoding="utf-8").splitlines() == ["Zulu.flac", "Alpha.flac"]

    def test_a_track_the_playlist_carries_twice_keeps_the_playlist_order(self, tmp_path):
        # Both occurrences of a doubled track land on ONE file (the name
        # ledger lets an item retake its own name), so the ordered list held
        # that path twice, could never match the folder's count, and the
        # order fix silently fell back to name order for exactly those
        # playlists. One entry per file, in playlist order.
        dl = _make_download(tmp_path)
        (tmp_path / "Zulu.flac").write_bytes(b"a")
        (tmp_path / "Alpha.flac").write_bytes(b"b")

        written = dl.playlist_populate(
            {tmp_path},
            "My List",
            is_album=False,
            sort_alphabetically=True,
            paths_ordered=[tmp_path / "Zulu.flac", tmp_path / "Alpha.flac", tmp_path / "Zulu.flac"],
        )

        assert written[0].read_text(encoding="utf-8").splitlines() == ["Zulu.flac", "Alpha.flac"]

    def test_a_failed_item_never_becomes_a_ghost_entry(self, tmp_path):
        # A failed download can hand back a path with no file behind it; the
        # playlist must list only what landed.
        dl = _make_download(tmp_path)
        (tmp_path / "One.flac").write_bytes(b"a")

        written = dl.playlist_populate(
            {tmp_path},
            "My List",
            is_album=False,
            sort_alphabetically=False,
            paths_ordered=[tmp_path / "One.flac", tmp_path / "Never Landed.flac"],
        )

        assert written[0].read_text(encoding="utf-8").splitlines() == ["One.flac"]

    def test_another_directorys_file_stays_out_of_this_m3u(self, tmp_path):
        # A multi-disc album writes one playlist per disc folder; disc 2's
        # tracks must not leak into disc 1's file.
        dl = _make_download(tmp_path)
        disc1 = tmp_path / "CD1"
        disc2 = tmp_path / "CD2"
        disc1.mkdir()
        disc2.mkdir()
        (disc1 / "One.flac").write_bytes(b"a")
        (disc2 / "Two.flac").write_bytes(b"b")
        ordered = [disc1 / "One.flac", disc2 / "Two.flac"]

        written = dl.playlist_populate(
            {disc1, disc2}, "Box", is_album=True, sort_alphabetically=True, paths_ordered=ordered
        )

        by_dir = {p.parent.name: p.read_text(encoding="utf-8").splitlines() for p in written}
        assert by_dir == {"CD1": ["One.flac"], "CD2": ["Two.flac"]}

    def test_a_playlist_folder_symlink_stays_listed_while_its_share_is_away(self, tmp_path):
        # A playlist-folder entry is a symlink into the track tree; a target on
        # a briefly-offline share must stay a playlist line, not vanish.
        dl = _make_download(tmp_path)
        link = tmp_path / "Linked.flac"
        link.symlink_to(tmp_path / "gone" / "Linked.flac")

        written = dl.playlist_populate(
            {tmp_path}, "My List", is_album=False, sort_alphabetically=False, paths_ordered=[link]
        )

        assert written[0].read_text(encoding="utf-8").splitlines() == [str(pathlib.Path("gone") / "Linked.flac")]

    def test_an_existing_m3u_keeps_receiving_the_playlist(self, tmp_path):
        # Libraries built before the .m3u8 rename hold a .m3u; writing the new
        # extension beside it would leave two files a scanner both ingests, and
        # nothing may ever delete one.
        dl = _make_download(tmp_path)
        (tmp_path / "One.flac").write_bytes(b"a")
        legacy = tmp_path / "_My List.m3u"
        legacy.write_text("old\n", encoding="utf-8")

        written = dl.playlist_populate(
            {tmp_path}, "My List", is_album=False, sort_alphabetically=False, paths_ordered=[tmp_path / "One.flac"]
        )

        assert written == [legacy]
        assert not (tmp_path / "_My List.m3u8").exists(), "no m3u8 sibling appears"


class TestTheM3uNeverShrinks:
    """The order this run knows may REORDER the folder, never shorten it.

    A run reports back only what it actually fetched. A re-download skips the
    tracks you already have, a cancelled run stops partway, an item can fail.
    Writing only those over an existing playlist replaced a complete m3u with a
    one-line one, which for the file is the same as losing the playlist.
    """

    def test_a_redownload_that_skips_what_you_own_keeps_the_whole_list(self, tmp_path):
        # The shape of the bug: 3 tracks on disk, one fetched this run (the
        # other two were skipped as already owned, and a skip reports no path).
        dl = _make_download(tmp_path)
        for name in ("01 One.flac", "02 Two.flac", "03 Three.flac"):
            (tmp_path / name).write_bytes(b"a")
        listed = tmp_path / "_My List.m3u8"
        listed.write_text("01 One.flac\n02 Two.flac\n03 Three.flac\n", encoding="utf-8")

        written = dl.playlist_populate(
            {tmp_path},
            "My List",
            is_album=False,
            sort_alphabetically=True,
            paths_ordered=[tmp_path / "03 Three.flac"],
        )

        assert written[0].read_text(encoding="utf-8").splitlines() == [
            "01 One.flac",
            "02 Two.flac",
            "03 Three.flac",
        ]

    def test_a_cancelled_run_leaves_the_playlist_whole(self, tmp_path):
        dl = _make_download(tmp_path)
        for name in ("A.flac", "B.flac", "C.flac"):
            (tmp_path / name).write_bytes(b"a")

        written = dl.playlist_populate(
            {tmp_path},
            "My List",
            is_album=False,
            sort_alphabetically=True,
            paths_ordered=[tmp_path / "A.flac"],  # cancelled after the first
        )

        assert written[0].read_text(encoding="utf-8").splitlines() == ["A.flac", "B.flac", "C.flac"]

    def test_nothing_landed_writes_the_folder_rather_than_an_empty_file(self, tmp_path):
        dl = _make_download(tmp_path)
        (tmp_path / "One.flac").write_bytes(b"a")

        written = dl.playlist_populate(
            {tmp_path}, "My List", is_album=False, sort_alphabetically=True, paths_ordered=[]
        )

        assert written[0].read_text(encoding="utf-8").splitlines() == ["One.flac"]


class TestTheCollectorPreservesSubmissionOrder:
    def test_results_come_back_in_submission_order_not_completion_order(self, tmp_path):
        dl = _make_download(tmp_path)
        first, second, failed = Future(), Future(), Future()
        # "Completed" in reverse order; as_completed sees them however it
        # likes, the collector must not care.
        second.set_result((True, tmp_path / "Second.flac"))
        first.set_result((True, tmp_path / "First.flac"))
        failed.set_result((False, ""))

        paths = dl._process_download_futures(
            [first, second, failed], progress=MagicMock(), progress_task=0, progress_stdout=True
        )

        assert paths == [tmp_path / "First.flac", tmp_path / "Second.flac"]

    def test_an_abort_collects_the_landed_work_and_skips_the_cancelled(self, tmp_path):
        # CTRL+C mid-list: the loop cancels the not-yet-started tail and
        # breaks. The landed prefix must still come back (the m3u of a partial
        # download lists what landed), and a cancelled future must be skipped,
        # not asked for a result it will never have.
        dl = _make_download(tmp_path)
        dl.event_abort.set()
        landed, never_started = Future(), Future()
        landed.set_result((True, tmp_path / "First.flac"))

        paths = dl._process_download_futures(
            [landed, never_started], progress=MagicMock(), progress_task=0, progress_stdout=True
        )

        assert paths == [tmp_path / "First.flac"]
        assert never_started.cancelled()

    def test_an_abort_does_not_re_raise_an_item_that_crashed(self, tmp_path):
        # The abort breaks out of the reporting loop, so an item that raised
        # may never have been surfaced. Asking it for its result in the
        # collection pass turned the user's Cancel into a job failure, and no
        # m3u was written for the prefix that did land.
        dl = _make_download(tmp_path)
        dl.event_abort.set()
        landed, crashed = Future(), Future()
        landed.set_result((True, tmp_path / "First.flac"))
        crashed.set_exception(OSError("disk full"))

        paths = dl._process_download_futures(
            [landed, crashed], progress=MagicMock(), progress_task=0, progress_stdout=True
        )

        assert paths == [tmp_path / "First.flac"]
