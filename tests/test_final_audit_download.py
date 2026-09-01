"""The final pre-release pass over waves/download.py, one guard per finding.

Three defects, all of them silent in their own way: a run that writes a second
copy of a track it already has, a run that paints a finished playlist red, and a
rate-limit pause that quietly stops holding the run back. Each test states the
user-visible sequence it prevents, and each was checked against the unfixed code
first: reverting its fix turns the test red.

  1  the late skip only recognised a twin tagged on the BASE name, so a twin at
     a numbered variant, and a twin whose file carries no id at all, each got a
     byte-identical second copy that nothing ever removes
  2  the loser of two in-flight twins was refused at the move and reported as a
     FAILED track, over a file sitting correctly on disk
  3  two batch pauses in force at once: the first to wake reopened the gate for
     everybody, so the run went back to full API traffic mid-pause

Three more guards came out of reading the round itself, each one a hole the
round opened: the refused move logged a twin's step-aside in the words of a lost
download, the exhausted-name path still failed an item whose own twin held the
last name, and the counted pause bound every worker except the one that took it.
"""

from __future__ import annotations

import contextlib
import pathlib
import threading
import time
from collections import defaultdict
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from waves.download import Download


def _make_download(tmp_path: pathlib.Path, *, skip_existing: bool = True) -> Download:
    dl = Download(
        tidal_obj=MagicMock(),
        skip_existing=skip_existing,
        path_base=str(tmp_path),
        fn_logger=MagicMock(),
        progress=MagicMock(),
    )
    dl.settings = MagicMock()
    dl.settings.data.filename_illegal_replacement = ""
    dl.settings.data.filename_illegal_map = None
    dl.settings.data.extract_flac = False
    dl.settings.data.downsample_enabled = False
    dl.settings.data.video_convert_mp4 = False
    dl.settings.data.path_binary_ffmpeg = ""
    dl.event_abort = threading.Event()
    dl.event_run = threading.Event()
    dl.event_run.set()

    return dl


def _track(item_id: str):
    return SimpleNamespace(id=item_id, name="Song", artist=SimpleNamespace(name="Artist"), artists=[], duration=200)


def _lines(logged) -> list[str]:
    """What a stand-in logger was actually told, message by message."""
    return [str(call.args[0]) for call in logged.call_args_list if call.args]


_OCCUPIED_BY_A_STRANGER = "occupied by another writer"
_OCCUPIED_BY_ITS_OWN = "already holds this item's own copy"


class _ReachedTheClaim(Exception):
    """Raised by the claim spy: the guard did NOT late-skip this pass."""


def _run_to_the_guard(dl: Download, dst: pathlib.Path, media) -> tuple[bool, pathlib.Path]:
    """Drive _perform_actual_download to the late-skip guard, with the network
    download stubbed to 'succeeded'.

    Returns the (ok, path) of a late skip. Raises _ReachedTheClaim when the
    guard let the pass through, which is the distinction every finding-1 test
    turns on: skipping onto the twin's file, or going on to write a second one.
    """

    def _fake_download(self, *, media, stream_manifest, path_file, event_stop=None, **kw):
        return True, path_file

    def _plan(self, *a, **k):
        return defaultdict(float)

    def _spy_claim(self, *a, **k):
        raise _ReachedTheClaim

    with (
        patch.object(Download, "_download", _fake_download),
        patch.object(Download, "_finalize_plan", _plan),
        patch.object(Download, "_note_stage", lambda *a, **k: None),
        patch.object(Download, "_claim_destination", _spy_claim),
    ):
        return dl._perform_actual_download(
            media=media,
            path_media_dst=dst,
            stream_manifest=None,
            do_flac_extract=False,
            is_parent_album=False,
            media_stream=None,
        )


@contextlib.contextmanager
def _stubbed_stream(payload: bytes = b"fresh bytes"):
    """The network half of the pipeline stubbed out, with the real claim and the
    real move left in place: what the item reports is what the queue row and the
    collection tally are built from.

    Taken ONCE, around every thread of a test, never inside the workers: two
    threads patching one class attribute restore each other's stand-in, and the
    last one out would leave it there for the rest of the suite.
    """

    def _fake_download(self, *, media, stream_manifest, path_file, event_stop=None, **kw):
        path_file.write_bytes(payload)

        return True, path_file

    def _plan(self, *a, **k):
        return defaultdict(float)

    with (
        patch.object(Download, "_download", _fake_download),
        patch.object(Download, "_finalize_plan", _plan),
        patch.object(Download, "_note_stage", lambda *a, **k: None),
    ):
        yield


def _run_to_the_end(dl: Download, dst: pathlib.Path, media) -> tuple[bool, pathlib.Path]:
    """One item's whole pass, inside _stubbed_stream."""
    return dl._perform_actual_download(
        media=media,
        path_media_dst=dst,
        stream_manifest=None,
        do_flac_extract=False,
        is_parent_album=False,
        media_stream=None,
    )


# --------------------------------------------------------------------------- #
# 1. The late skip recognises every twin THIS RUN wrote, not just a tagged one
#    sitting on the base name.
#
# A playlist or a mix may name one song twice. Both occurrences pass every
# earlier check over a folder that is still empty, and the file only appears at
# the move, so the post-stream guard is the only thing left that can tell the
# second occurrence its own file is already there. Reading the base name's tag
# answers for the ordinary case and for nothing else: the first twin may have
# landed at "Song_01" (a stranger held the base name), or landed a raw .ts video
# that carries no tag atoms at all. Both read as a stranger, and the run wrote a
# byte-identical copy beside its own file, listed twice in the playlist file and
# never cleaned up (this app does not delete user files).
#
# The run's own ledger of written names answers all three. It is positive
# evidence of the strongest kind (this run made the file), so the contract that
# cost two earlier rounds is untouched: an UNKNOWN identity on disk is still a
# stranger, never "mine".
# --------------------------------------------------------------------------- #
def test_a_twin_that_landed_at_a_numbered_variant_is_still_a_late_skip(tmp_path):
    """The first occurrence stepped around a colliding stranger onto Song_01.
    The second must land on that file, not write Song_02 beside it."""
    dl = _make_download(tmp_path)
    dst = tmp_path / "Song.flac"
    dst.write_bytes(b"a different track that happens to share the name")
    twin = tmp_path / "Song_01.flac"
    twin.write_bytes(b"landed by the first twin")
    dl._record_name_written(twin, "42")

    with patch("waves.download.read_item_id", lambda p: "999"):
        ok, landed = _run_to_the_guard(dl, dst, _track("42"))

    assert ok is True
    assert landed == twin, "the twin's own file is where this occurrence already lives"
    assert not (tmp_path / "Song_02.flac").exists()


def test_a_twin_this_run_wrote_with_no_readable_id_is_still_a_late_skip(tmp_path):
    """A raw .ts video writes no tag atoms, so its file can never answer for
    itself. The run knows it wrote it, and that is enough."""
    dl = _make_download(tmp_path)
    dst = tmp_path / "Song.ts"
    dst.write_bytes(b"landed by the first twin")
    dl._record_name_written(dst, "42")

    with patch("waves.download.read_item_id", lambda p: ""):
        ok, landed = _run_to_the_guard(dl, dst, _track("42"))

    assert ok is True
    assert landed == dst
    assert not (tmp_path / "Song_01.ts").exists()


def test_an_untagged_file_this_run_did_not_write_is_still_a_stranger(tmp_path):
    """The contract two earlier rounds were spent on: only the run's own record
    speaks for an unreadable file. An untagged occupant the run never wrote is
    the collision the claim exists to step around, and reading it as this item
    made a distinct track skip instead of uniquifying, sidecars and all."""
    dl = _make_download(tmp_path)
    dst = tmp_path / "Song.flac"
    dst.write_bytes(b"a different track, written by something that tags nothing")

    with patch("waves.download.read_item_id", lambda p: ""):
        try:
            _run_to_the_guard(dl, dst, _track("42"))
        except _ReachedTheClaim:
            pass
        else:
            raise AssertionError("an untagged stranger was treated as this item's own copy")


def test_a_name_this_run_wrote_for_another_item_is_not_this_item(tmp_path):
    """The ledger is read for THIS item's id, never as 'somebody wrote here'."""
    dl = _make_download(tmp_path)
    dst = tmp_path / "Song.flac"
    sibling = tmp_path / "Song_01.flac"
    sibling.write_bytes(b"a colliding track of the same name")
    dl._record_name_written(sibling, "999")

    with patch("waves.download.read_item_id", lambda p: ""):
        try:
            _run_to_the_guard(dl, dst, _track("42"))
        except _ReachedTheClaim:
            pass
        else:
            raise AssertionError("another item's file was treated as this item's own copy")


def test_a_recorded_name_whose_file_is_gone_is_not_a_landing(tmp_path):
    """Skipping onto a path that holds nothing would report a track delivered
    and leave the library without it."""
    dl = _make_download(tmp_path)
    dst = tmp_path / "Song.flac"
    dl._record_name_written(tmp_path / "Song_01.flac", "42")  # never created

    with patch("waves.download.read_item_id", lambda p: ""):
        try:
            _run_to_the_guard(dl, dst, _track("42"))
        except _ReachedTheClaim:
            pass
        else:
            raise AssertionError("a ledger entry with no file behind it was read as a landed copy")


def test_a_name_this_run_wrote_in_another_folder_is_no_answer(tmp_path):
    """The same track twice under a template that spells the two occurrences
    differently ({list_pos}, the default playlist template) is two files by the
    user's own naming, and has to stay two."""
    dl = _make_download(tmp_path)
    elsewhere = tmp_path / "Other"
    elsewhere.mkdir()
    landed = elsewhere / "Song.flac"
    landed.write_bytes(b"the first occurrence, under its own name")
    dl._record_name_written(landed, "42")

    with patch("waves.download.read_item_id", lambda p: ""):
        try:
            _run_to_the_guard(dl, tmp_path / "Song.flac", _track("42"))
        except _ReachedTheClaim:
            pass
        else:
            raise AssertionError("a name written in another folder answered for this destination")


def test_a_forced_redownload_still_reaches_the_move(tmp_path):
    """REDOWNLOAD turns skipping off for that thread, and the ledger must not
    smuggle a skip back in: replacing the copy in place is the whole point."""
    dl = _make_download(tmp_path, skip_existing=False)
    dst = tmp_path / "Song.flac"
    dst.write_bytes(b"the copy being replaced")
    dl._record_name_written(dst, "42")

    with patch("waves.download.read_item_id", lambda p: "42"):
        try:
            _run_to_the_guard(dl, dst, _track("42"))
        except _ReachedTheClaim:
            pass
        else:
            raise AssertionError("a forced redownload late-skipped its own file")


# --------------------------------------------------------------------------- #
# 2. The loser of two in-flight twins reports a SKIP, not a failure.
#
# Both occurrences of a twice-listed track hold ONE claim on one name (an item
# never has to make way for itself), so when both are in flight the slower one
# arrives at a name its own twin has just filled. The move refuses an occupied
# destination it may not overwrite, which is right, but the verdict was not: the
# run tallied the track as FAILED and finished the playlist red, with every
# file, sidecar and m3u entry correct on disk.
# --------------------------------------------------------------------------- #
def test_the_losing_twin_reports_a_skip_rather_than_failing_the_track(tmp_path):
    dl = _make_download(tmp_path)
    dst = tmp_path / "Song.flac"
    first_in_extras = threading.Event()
    second_in_extras = threading.Event()
    first_finished = threading.Event()
    entered: list[int] = []
    entered_lock = threading.Lock()

    def _extras(*args, **kwargs):
        # Both occurrences are past the guard and holding the same claim by the
        # time either of them gets here, which is the window the run really has.
        with entered_lock:
            is_first = not entered
            entered.append(1)

        if is_first:
            first_in_extras.set()
            assert second_in_extras.wait(10), "the twin never reached its own claim"
        else:
            second_in_extras.set()
            assert first_finished.wait(10), "the twin never landed its file"

        return None

    dl._handle_metadata_and_extras = _extras
    results: dict[str, tuple] = {}

    def _run(tag: str) -> None:
        results[tag] = _run_to_the_end(dl, dst, _track("42"))

    with _stubbed_stream(b"the one copy of this track"):
        winner = threading.Thread(target=_run, args=("winner",))
        winner.start()
        try:
            assert first_in_extras.wait(10), "the first occurrence never reached its claim"
            loser = threading.Thread(target=_run, args=("loser",))
            loser.start()
            winner.join(20)
            first_finished.set()
            loser.join(20)
        finally:
            first_finished.set()
            second_in_extras.set()

    assert results["winner"] == (True, dst)
    assert results["loser"] == (True, dst), "a track that is on disk must never be reported as failed"
    assert dst.read_bytes() == b"the one copy of this track"
    assert not (tmp_path / "Song_01.flac").exists(), "the twin wrote a second copy of the same track"
    assert dl._names_reserved == {}, "claims are released either way"
    # And the trail says what happened. An expected step-aside logged as a
    # download left out of the library is a support bundle reporting a loss that
    # never happened, in the same words a real one uses.
    assert not [
        line for line in _lines(dl.fn_logger.error) if _OCCUPIED_BY_A_STRANGER in line
    ], "the twin's own copy was reported as another writer's file"
    assert any(_OCCUPIED_BY_ITS_OWN in line for line in _lines(dl.fn_logger.debug))


def test_a_foreign_occupant_at_the_move_still_fails_the_item(tmp_path):
    """The other half: nothing may be reported delivered because SOMEBODY's
    file is at the name. Another writer landing there between the claim and the
    move is exactly what the refusal is for, and it still fails the item."""
    dl = _make_download(tmp_path)
    dst = tmp_path / "Song.flac"

    def _extras(*args, **kwargs):
        dst.write_bytes(b"another writer got here first")

        return None

    dl._handle_metadata_and_extras = _extras

    with _stubbed_stream(), patch("waves.download.read_item_id", lambda p: "999"):
        ok, path = _run_to_the_end(dl, dst, _track("42"))

    assert ok is False
    assert path == dst
    assert dst.read_bytes() == b"another writer got here first", "a stranger's file must be left exactly as it was"
    assert any(
        _OCCUPIED_BY_A_STRANGER in line for line in _lines(dl.fn_logger.error)
    ), "a download really left out of the library has to say so, in its own words"
    assert not [line for line in _lines(dl.fn_logger.debug) if _OCCUPIED_BY_ITS_OWN in line]


def test_the_move_names_the_kind_of_occupant_that_refused_it(tmp_path):
    """The two outcomes read identically in a support bundle otherwise: the same
    ERROR was logged whether a download was genuinely left out of the library or
    an expected twin simply had nothing to write. The move never replaces the
    file either way, which is what the refusal is."""
    dl = _make_download(tmp_path)
    source = tmp_path / "source.flac"
    source.write_bytes(b"the fresh copy")
    dst = tmp_path / "Song.flac"
    dst.write_bytes(b"whoever is already there")

    assert dl._move_file(source, dst, overwrite=False, occupant_is_own=lambda p: False) is False
    assert any(_OCCUPIED_BY_A_STRANGER in line for line in _lines(dl.fn_logger.error))

    dl.fn_logger.reset_mock()

    assert dl._move_file(source, dst, overwrite=False, occupant_is_own=lambda p: True) is False
    assert not dl.fn_logger.error.called, "an expected twin collision must not read as a lost download"
    assert any(_OCCUPIED_BY_ITS_OWN in line for line in _lines(dl.fn_logger.debug))

    dl.fn_logger.reset_mock()

    # Every other caller passes no question at all, and for them an occupant is
    # a stranger exactly as it always was.
    assert dl._move_file(source, dst, overwrite=False) is False
    assert any(_OCCUPIED_BY_A_STRANGER in line for line in _lines(dl.fn_logger.error))
    assert dst.read_bytes() == b"whoever is already there", "a refused move must never replace the file"


def test_a_twin_landing_between_the_guard_and_the_claim_writes_no_second_copy(tmp_path):
    """The narrow window of the same race: the twin lands after this occurrence
    has passed the guard but before it claims, so the claim (which is not
    owner-aware, deliberately) steps it aside onto a numbered name and it writes
    a byte-identical copy. Asked again on the name it actually wanted, it has
    its answer."""
    dl = _make_download(tmp_path)
    dl._handle_metadata_and_extras = lambda *a, **k: None
    dst = tmp_path / "Song.flac"
    claim_real = Download._claim_destination

    def _claim_after_the_twin_lands(self, path_media_dst, media_id, owned_ids=None, fetch_is_atmos=None):
        if not dst.exists():
            dst.write_bytes(b"landed by the first twin")
            self._record_name_written(dst, "42")

        return claim_real(self, path_media_dst, media_id, owned_ids, fetch_is_atmos)

    with (
        _stubbed_stream(),
        patch.object(Download, "_claim_destination", _claim_after_the_twin_lands),
        patch("waves.download.read_item_id", lambda p: ""),
    ):
        ok, landed = _run_to_the_end(dl, dst, _track("42"))

    assert ok is True
    assert landed == dst
    assert not (tmp_path / "Song_01.flac").exists(), "the second occurrence wrote a copy of its own twin"
    assert dst.read_bytes() == b"landed by the first twin"


def test_an_exhausted_name_family_still_finds_the_twin_that_took_the_last_name(tmp_path):
    """The third door out of the claim: the name and all 99 of its numbered
    copies are taken, so there is nowhere left to write. One of those names can
    be this item's own twin, landed in the same window, and the item is already
    on disk rather than lost. Reported as a failure it painted the collection
    red and told the log a download had nowhere to go."""
    dl = _make_download(tmp_path)
    dl._handle_metadata_and_extras = lambda *a, **k: None
    dst = tmp_path / "Song.flac"

    for count in range(1, 100):
        (tmp_path / f"Song_{count:02d}.flac").write_bytes(b"a colliding stranger")

    claim_real = Download._claim_destination

    def _claim_after_the_twin_lands(self, path_media_dst, media_id, owned_ids=None, fetch_is_atmos=None):
        # The twin fills the one name the family had left, which is what leaves
        # the real claim below with no candidate at all.
        if not dst.exists():
            dst.write_bytes(b"landed by the first twin")
            self._record_name_written(dst, "42")

        return claim_real(self, path_media_dst, media_id, owned_ids, fetch_is_atmos)

    with (
        _stubbed_stream(),
        patch.object(Download, "_claim_destination", _claim_after_the_twin_lands),
        patch("waves.download.read_item_id", lambda p: ""),
    ):
        ok, landed = _run_to_the_end(dl, dst, _track("42"))

    assert ok is True, "the item's own file was on disk and the run called it a failure"
    assert landed == dst
    assert dst.read_bytes() == b"landed by the first twin"
    assert not [line for line in _lines(dl.fn_logger.error) if "No free name left" in line]
    assert dl._names_reserved == {}, "nothing was claimed, so nothing is left held"


# --------------------------------------------------------------------------- #
# 3. Two batch pauses in force at once compose.
#
# The pause is a promise to TIDAL that the whole run stands back, held by a gate
# every other worker waits on. Every due worker cleared that gate and set it
# again on its way out, so with a batch size below the worker count (batch 1 or
# 2 against the default three, which is exactly what a user being 429'd sets) a
# second worker reached a boundary while the first was still standing back, and
# the first one's wake-up threw the gate open in the middle of the second one's
# pause. The run went straight back to full API traffic during a pause it was
# still taking.
# --------------------------------------------------------------------------- #
def _paced(every: int, seconds: float) -> Download:
    """A pace carcass, deliberately built the way the older ones are: nothing
    but the counter, the lock and the gate. The rest of the pause's bookkeeping
    has to default on the class, or every stand-in in the suite raises."""
    dl = Download.__new__(Download)
    dl.settings = SimpleNamespace(
        data=SimpleNamespace(api_rate_limit_batch_size=every, api_rate_limit_delay_sec=seconds)
    )
    dl.fn_logger = MagicMock()
    dl.event_abort = threading.Event()
    dl._paced_items = 0
    dl._pace_lock = threading.Lock()
    dl._pace_gate = threading.Event()
    dl._pace_gate.set()

    return dl


def _two_pauses_in_force(dl: Download) -> tuple[list[threading.Event], list[threading.Event], list[threading.Thread]]:
    """Put two workers on two batch boundaries, both standing back at once.

    Hands back the switch that ends each pause, the event each pauser sets once
    _rate_limit_pause finally lets it go, and the threads, all in the order the
    two pauses were taken. Every caller must release both switches.
    """
    sleeping = threading.Semaphore(0)
    releases: list[threading.Event] = []
    releases_lock = threading.Lock()

    def _hold(seconds, event_stop=None):
        release = threading.Event()

        with releases_lock:
            releases.append(release)

        sleeping.release()
        release.wait(20)

    dl._sleep_politely = _hold
    returned: list[threading.Event] = [threading.Event(), threading.Event()]
    pausers: list[threading.Thread] = []

    # Item 3 opens a batch (batch size 2), and so does item 5. The counter is
    # set between the two, so which worker takes which boundary is not a race.
    for index, paced_before in enumerate((2, 4)):
        dl._paced_items = paced_before
        pauser = threading.Thread(target=lambda i=index: (dl._rate_limit_pause(), returned[i].set()))
        pauser.start()
        assert sleeping.acquire(timeout=10), "a boundary item never took its pause"
        pausers.append(pauser)

    return releases, returned, pausers


def test_the_gate_stays_shut_until_the_last_pause_ends():
    """The finding: worker A is standing back, worker C reaches the next
    boundary and stands back too, A wakes first. Its wake-up must not put the
    rest of the run back on the API while C is still pausing."""
    dl = _paced(2, 30.0)
    releases, _returned, pausers = _two_pauses_in_force(dl)

    # And an ordinary item, which only waits the pause out.
    dl._paced_items = 5
    walked = threading.Event()
    waiter = threading.Thread(target=lambda: (dl._rate_limit_pause(), walked.set()))
    waiter.start()

    try:
        assert not walked.wait(0.4), "a worker walked straight through two pauses"

        releases[0].set()

        assert not walked.wait(0.5), "the first pause to wake reopened the gate mid-pause of the second"

        releases[1].set()

        assert walked.wait(10), "the run never resumed once the last pause was over"
    finally:
        for release in releases:
            release.set()

        dl._pace_gate.set()

        for thread in (*pausers, waiter):
            thread.join(10)


def test_a_pauser_that_wakes_first_stands_back_with_its_sibling():
    """The gate held every worker EXCEPT the one that made the promise: a pauser
    whose own sleep ended went straight back to the API while its sibling was
    still standing back, which is the partial pause one level in."""
    dl = _paced(2, 30.0)
    releases, returned, pausers = _two_pauses_in_force(dl)

    try:
        releases[0].set()

        assert not returned[0].wait(0.5), "the first pauser resumed while its sibling was still pausing"

        releases[1].set()

        assert returned[0].wait(10), "the first pauser never resumed once the last pause was over"
        assert returned[1].wait(10)
    finally:
        for release in releases:
            release.set()

        dl._pace_gate.set()

        for thread in pausers:
            thread.join(10)


def test_a_stop_lets_a_waiting_pauser_go_without_waiting_for_its_sibling():
    """The pauser's own wait is a wait like any other: it wakes for a STOP, so
    stopping a job never has to sit out a sibling's remaining pause."""
    dl = _paced(2, 30.0)
    releases, returned, pausers = _two_pauses_in_force(dl)

    try:
        releases[0].set()

        assert not returned[0].wait(0.5), "the first pauser resumed while its sibling was still pausing"

        dl.event_abort.set()

        assert returned[0].wait(10), "STOP left a pauser waiting out a sibling's pause"
    finally:
        for release in releases:
            release.set()

        dl._pace_gate.set()

        for thread in pausers:
            thread.join(10)


def test_a_pause_cut_short_by_a_stop_hands_its_hold_straight_back():
    """A STOP ends a pause like any other ending, so the gate opens at once
    rather than leaving the other workers to wait the clock out."""
    dl = _paced(1, 30.0)
    dl.event_abort.set()  # the real sleep returns the moment this is set
    dl._paced_items = 1
    started = time.monotonic()

    dl._rate_limit_pause()

    assert time.monotonic() - started < 5.0, "the pause sat out its 30 seconds through a STOP"

    assert dl._pace_gate.is_set()
    assert dl._pace_holds == 0
    assert dl._pace_until == 0.0


def test_a_pause_that_raises_hands_its_hold_back_too():
    """The hold is given back in a finally: a pauser that dies holding it would
    otherwise shut the run's API traffic down for good."""
    dl = _paced(1, 30.0)

    def _boom(seconds, event_stop=None):
        raise RuntimeError("the worker died mid-pause")

    dl._sleep_politely = _boom
    dl._paced_items = 1

    with contextlib.suppress(RuntimeError):
        dl._rate_limit_pause()

    assert dl._pace_gate.is_set()
    assert dl._pace_holds == 0


def test_a_waiter_walks_once_the_pause_is_over_by_the_clock():
    """The backstop, and the reason the count cannot deadlock a run: a hold that
    never comes back leaves the gate shut, so a waiter reads the deadline the
    pause itself set and goes on once it has passed."""
    dl = _paced(2, 30.0)
    dl._pace_holds = 1
    dl._pace_until = time.monotonic() - 0.1
    dl._pace_gate.clear()
    dl._paced_items = 5
    walked = threading.Event()
    waiter = threading.Thread(target=lambda: (dl._rate_limit_pause(), walked.set()))
    waiter.start()

    try:
        assert walked.wait(10), "a worker waited out a pause that was already over"
    finally:
        dl._pace_gate.set()
        waiter.join(10)


def test_a_gate_shut_with_no_pause_behind_it_is_still_waited_on():
    """The backstop reads a deadline, never guesses one: nothing dated means
    there is no pause it can speak for, and the worker waits as it always did
    (the stop is what wakes it)."""
    dl = _paced(2, 30.0)
    dl._pace_gate.clear()
    dl._paced_items = 5
    walked = threading.Event()
    waiter = threading.Thread(target=lambda: (dl._rate_limit_pause(), walked.set()))
    waiter.start()

    try:
        assert not walked.wait(0.5), "a worker walked through a gate that was still shut"
        dl.event_abort.set()
        assert walked.wait(10), "STOP left a worker waiting on a pause it will never see the end of"
    finally:
        dl._pace_gate.set()
        waiter.join(10)
