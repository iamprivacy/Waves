"""Issue #35: a 200- or 501-track playlist failed "every time".

Nothing here is about playlists as such. Everything here is about SCALE: a
collection download judges itself all-or-nothing, so a per-track hazard that a
12-track album meets once in a hundred runs meets a 501-track playlist on
essentially every run. Three such hazards turned one entry into a red row over
a folder holding hundreds of songs:

* a track TIDAL no longer carries was counted as a FAILURE at the preparation
  gate, so a stale entry (which a years-old playlist always has) failed the
  whole playlist, permanently, on every retry;
* an item that RAISED unwound the entire list, discarding the honest tally of
  the other 500 and the m3u with it;
* whatever went wrong, the row said one word: "Failed".

The tests below pin each of those, plus the scale itself.
"""

from __future__ import annotations

import pathlib
import threading
import time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import requests
import tidalapi
from requests.adapters import HTTPAdapter
from tidalapi import Track
from tidalapi.exceptions import ObjectNotFound

from waves.config import _API_RETRY_TOTAL, ApiCallStopped, api_waits_wake_for, harden_api_session
from waves.constants import REQUESTS_TIMEOUT_SEC
from waves.download import Download
from waves.helper.exceptions import DownloadIncomplete
from waves.helper.tidal import get_album_artists
from waves.progress import Progress
from waves.waves_ui import backend
from waves.waves_ui.backend import _collection_incomplete_reason, _TrackedDownload

# --- the preparation gate: delisted is a refusal, not a failure --------------
# Issue #25 settled this rule for the stream fetch. The preparation gate runs
# FIRST and re-fetches every track of a collection (`session.track(id,
# with_album=True)`), so a delisted track 404s there and never reaches the
# stream fetch at all. It swallowed the 404 into a bare "no media", which
# item() reports as ok=False, which the job counts as a failure.


def _tracked(session=None) -> tuple[_TrackedDownload, MagicMock]:
    relay = MagicMock()
    dl = _TrackedDownload(
        tidal_obj=MagicMock(),
        skip_existing=False,
        path_base="./tmp",
        fn_logger=MagicMock(),
        progress=MagicMock(),
        track_signals=relay,
    )
    if session is not None:
        dl.session = session
    dl.event_abort = threading.Event()
    dl.event_run = threading.Event()
    dl.event_run.set()
    return dl, relay


def _track(track_id: str = "123") -> MagicMock:
    m = MagicMock(spec=Track)
    m.id = track_id
    m.track_num = 1
    m.volume_num = 1
    m.duration = 100
    return m


@pytest.fixture(autouse=True)
def _stub_name_builders():
    with (
        patch.object(backend, "name_builder_title", return_value="Title"),
        patch("waves.download.name_builder_item", return_value="Artist - Title"),
        patch("waves.download.name_builder_title", return_value="Title"),
    ):
        yield


def test_a_delisted_track_is_refused_not_failed():
    """The reported shape: TIDAL 404s the re-fetch of one playlist entry."""
    session = MagicMock()
    session.track.side_effect = ObjectNotFound("Object not found")
    dl, _relay = _tracked(session)

    assert dl._validate_and_prepare_media(_track(), None, None) is None
    # Marked on THIS thread as a refusal, which is what keeps it out of
    # fail_count when item() settles the outcome.
    assert dl._take_unavailable() is True
    assert dl.list_unavailable is False  # one item, never the whole list


def test_a_delisted_track_does_not_fail_the_playlist_around_it():
    """500 tracks land, one is gone from TIDAL: that is a finished download."""
    assert _collection_incomplete_reason(500, 500, 0, 1) is None


def test_a_transient_error_at_the_gate_is_still_a_failure_and_is_logged():
    """A rate limit or a dropped session is not a refusal, and it may not stay
    invisible: the blanket catch used to log nothing at all."""
    session = MagicMock()
    session.track.side_effect = TimeoutError("connection timed out")
    dl, _relay = _tracked(session)

    assert dl._validate_and_prepare_media(_track(), None, None) is None
    assert dl._take_unavailable() is False  # a failure, so the job counts it
    assert dl.fn_logger.exception.called


def test_only_the_track_being_gone_counts_as_gone_not_its_album():
    """session.track(id, with_album=True) is TWO requests. A pulled or
    region-locked ALBUM says nothing about a song that still streams, and
    letting its 404 stand would quietly skip a song this app could have saved
    and call the collection complete without it."""
    bare = _track()
    bare.album = None
    session = MagicMock()
    session.track.side_effect = lambda tid, with_album=False: _raise_if(with_album, bare)
    dl, _relay = _tracked(session)

    caller_album = object()
    asked = _track()
    asked.album = caller_album

    got = dl._validate_and_prepare_media(asked, None, None)
    assert got is bare, "the song is still downloadable, so it must still be prepared"
    assert got.album is caller_album, "and it keeps the album details the caller already had"
    assert dl._take_unavailable() is False, "nothing here says TIDAL stopped carrying the song"


def _raise_if(with_album, bare):
    if with_album:
        raise ObjectNotFound("Object not found")
    return bare


def test_a_missing_object_never_condemns_the_whole_list():
    """The gate can be holding nothing at all (a download asked for by id).
    Marking the LIST refused on that evidence would settle every sibling track
    as refused too."""
    dl, _relay = _tracked()
    dl._note_unavailable_item(None)
    assert dl.list_unavailable is False


# --- one crashed item may not unwind the list -------------------------------


def _collection(n_items: int, concurrency: int = 3) -> tuple[Download, Progress, int]:
    b = Download.__new__(Download)  # bypass __init__; set only what the method touches
    b.settings = MagicMock()
    b.settings.data.downloads_concurrent_max = concurrency
    b.event_abort = threading.Event()
    b.fn_logger = MagicMock()
    b.progress_gui = None
    progress = Progress()
    return b, progress, progress.add_task("list", total=n_items)


def _run(b: Download, items: list, progress: Progress, task: int) -> list[pathlib.Path]:
    return Download._execute_collection_downloads(
        b, items, "{track_title}", None, None, False, True, len(items), progress, task, True
    )


def test_a_crashed_item_leaves_the_rest_of_the_list_alone():
    items = [f"t{i}" for i in range(20)]
    b, progress, task = _collection(len(items))
    attempted: list[str] = []
    crashes: list[int] = []
    b._note_item_crashed = lambda: crashes.append(1)

    def fake_item(media, **kwargs):
        attempted.append(media)
        if media == "t7":
            raise OSError("the destination could not be created")
        return True, pathlib.Path(f"/base/{media}.flac")

    b.item = fake_item

    landed = _run(b, items, progress, task)

    assert sorted(attempted) == sorted(items), "every item still got its turn"
    assert len(landed) == len(items) - 1, "only the crashed item produced no file"
    assert progress.tasks[task].finished, "the list settled instead of unwinding"
    assert crashes == [1], "the crash was counted once, for the job to judge"


def test_a_crashed_item_is_tallied_as_a_failure():
    """The tally is what keeps the verdict honest: without it a list that lost
    a track to a crash would report a clean done."""
    dl, _relay = _tracked()
    dl._note_item_crashed()
    assert dl.fail_count == 1
    assert dl.ok_count == 0
    assert _collection_incomplete_reason(dl.write_count, dl.ok_count, dl.fail_count) == "1 of 1 tracks failed"


def test_a_five_hundred_track_list_attempts_every_track_once():
    """The reported scale, at the reported concurrency. Nothing in the loop may
    re-submit, drop or double up an item once the list is this long."""
    items = [f"t{i}" for i in range(501)]
    b, progress, task = _collection(len(items))
    attempted: list[str] = []
    lock = threading.Lock()

    def fake_item(media, **kwargs):
        with lock:
            attempted.append(media)
        return True, pathlib.Path(f"/base/{media}.flac")

    b.item = fake_item

    landed = _run(b, items, progress, task)

    assert len(attempted) == 501
    assert sorted(set(attempted)) == sorted(items)
    assert len(landed) == 501
    assert progress.tasks[task].finished


# --- the row has to say what went wrong -------------------------------------


def test_the_incomplete_verdict_is_the_one_exception_safe_to_repeat():
    """Only DownloadIncomplete carries a message written for the user; every
    other exception can spell out a path or a host, and the queue is on
    screen."""
    with pytest.raises(DownloadIncomplete, match="6 of 501 tracks failed"):
        backend._raise_download_incomplete("6 of 501 tracks failed")
    # Still a RuntimeError to everyone who only needs "the download failed".
    assert issubclass(DownloadIncomplete, RuntimeError)


class _QueueCarcass:
    """Just what _enqueue and _set_queue_status touch, with the real methods
    bound on: the row dict they build is the thing under test."""

    _enqueue = backend.WavesBridge._enqueue
    _queue_item = backend.WavesBridge._queue_item
    _set_queue_status = backend.WavesBridge._set_queue_status

    def __init__(self) -> None:
        self._queue_seq = 0
        self._queue: list = []
        self._queue_index: dict = {}
        self._queue_lock = threading.Lock()
        self._qdirty_added: list = []
        self.changed: list = []

    def _emit_queue(self) -> None:
        pass

    def _queue_mark_changed(self, qid: int) -> None:
        self.changed.append(qid)

    def _target_tier(self) -> str:
        return ""

    def _queued_quality_value(self) -> str:
        return ""

    def _library_bulk_skip_on(self) -> bool:
        return False


def test_a_queue_row_is_born_with_a_reason_field():
    """The drawer's model fixes its roles from the first row it is handed, so
    a field that only appears once something fails exists on no row at all."""
    carcass = _QueueCarcass()
    qid = carcass._enqueue("Playlist", "playlist", "pl-1", collection=True, tracks=501)
    assert carcass._queue_index[qid]["reason"] == ""


def test_a_failed_row_keeps_the_reason_and_a_settled_row_drops_it():
    carcass = _QueueCarcass()
    qid = carcass._enqueue("Playlist", "playlist", "pl-1", collection=True, tracks=501)
    row = carcass._queue_index[qid]

    carcass._set_queue_status(qid, "failed", "6 of 501 tracks failed")
    assert row["status"] == "failed"
    assert row["reason"] == "6 of 501 tracks failed"

    # A row that is retried in place and then finishes may not keep explaining
    # a failure that no longer stands.
    carcass._set_queue_status(qid, "done")
    assert row["reason"] == ""


def test_a_reason_arriving_on_an_already_failed_row_still_reaches_the_drawer():
    """The status word does not change when a held retry fails again, so the
    reason alone has to be enough to mark the row dirty."""
    carcass = _QueueCarcass()
    qid = carcass._enqueue("Playlist", "playlist", "pl-1", collection=True, tracks=501)
    carcass._set_queue_status(qid, "failed", "6 of 501 tracks failed")
    carcass.changed.clear()

    carcass._set_queue_status(qid, "failed", "2 of 501 tracks failed")
    assert carcass._queue_index[qid]["reason"] == "2 of 501 tracks failed"
    assert carcass.changed == [qid]


# --- tagging a playlist track that has no album -----------------------------
# An album download always has an album by construction. A playlist is the one
# surface that can carry a track whose album block never arrived.


def test_album_artists_of_an_album_less_track_is_empty_not_a_crash():
    track = MagicMock(spec=Track)
    track.album = None
    assert get_album_artists(track) == []


# --- the rate-limit pause is a real setting again ---------------------------
# Both fields shipped in the Advanced card and were read by nothing, so a user
# meeting rate limits on a long playlist could turn them and change nothing.


def _paced(every: int, seconds: float) -> tuple[Download, list[float]]:
    dl = Download.__new__(Download)
    dl.settings = SimpleNamespace(
        data=SimpleNamespace(api_rate_limit_batch_size=every, api_rate_limit_delay_sec=seconds)
    )
    dl.fn_logger = MagicMock()
    dl.event_abort = None
    dl._paced_items = 0
    dl._pace_lock = threading.Lock()
    # The gate every OTHER worker waits on while one takes the pause.
    dl._pace_gate = threading.Event()
    dl._pace_gate.set()
    slept: list[float] = []
    dl._sleep_politely = lambda secs, event_stop=None: slept.append(secs)
    return dl, slept


def test_the_pause_lands_on_every_batch_boundary_and_nowhere_else():
    dl, slept = _paced(20, 3.0)
    for _ in range(41):
        dl._rate_limit_pause()
    # Songs 21 and 41 open a new batch; the first twenty go straight through.
    assert slept == [3.0, 3.0]


def test_a_short_list_and_a_single_song_never_pause():
    dl, slept = _paced(20, 3.0)
    for _ in range(20):
        dl._rate_limit_pause()
    assert slept == []


def test_the_pause_holds_every_worker_not_just_the_one_taking_it():
    """A pause is a promise to TIDAL, not a rest for one worker. The collection
    fan-out runs several at once, and while one slept the others kept asking:
    with the default three workers the request rate dropped by about a third
    where the setting says the app stands back."""
    dl, _slept = _paced(1, 3.0)
    taking = threading.Event()
    release = threading.Event()

    def _hold(secs, event_stop=None):
        taking.set()
        release.wait(5)

    dl._sleep_politely = _hold
    dl.event_abort = threading.Event()

    dl._rate_limit_pause()  # item 1: no boundary yet
    due = threading.Thread(target=dl._rate_limit_pause)  # item 2 opens a new batch
    due.start()
    assert taking.wait(5), "the boundary item never took its pause"

    sibling_through = threading.Event()
    sibling = threading.Thread(target=lambda: (dl._rate_limit_pause(), sibling_through.set()))
    sibling.start()
    try:
        assert not sibling_through.wait(0.5), "a sibling walked straight through the pause"
    finally:
        release.set()
        due.join(5)
        sibling.join(5)
    assert sibling_through.is_set(), "and it went on as soon as the pause was over"


def test_a_worker_waiting_out_a_siblings_pause_still_wakes_for_a_stop():
    dl, _slept = _paced(1, 3.0)
    dl.event_abort = threading.Event()
    dl._pace_gate.clear()  # as if a sibling were taking the pause right now
    walked = threading.Event()
    waiter = threading.Thread(target=lambda: (dl._rate_limit_pause(), walked.set()))
    waiter.start()
    try:
        assert not walked.wait(0.3)
        dl.event_abort.set()
        assert walked.wait(5), "STOP left a worker waiting on a pause it will never see the end of"
    finally:
        dl._pace_gate.set()
        waiter.join(5)


@pytest.mark.parametrize(("every", "seconds"), [(0, 3.0), (20, 0.0), (0, 0.0)])
def test_either_value_at_zero_turns_the_pause_off(every, seconds):
    dl, slept = _paced(every, seconds)
    for _ in range(60):
        dl._rate_limit_pause()
    assert slept == []


@pytest.mark.parametrize(
    "data",
    [
        SimpleNamespace(api_rate_limit_batch_size="many", api_rate_limit_delay_sec=3.0),  # not a number
        SimpleNamespace(api_rate_limit_batch_size=20, api_rate_limit_delay_sec=None),  # never written
        SimpleNamespace(),  # a settings file from a build that had no such field
    ],
)
def test_an_unreadable_setting_means_no_pause_never_a_stalled_download(data):
    dl, slept = _paced(20, 3.0)
    dl.settings = SimpleNamespace(data=data)
    for _ in range(60):
        dl._rate_limit_pause()
    assert slept == []


def test_the_pause_wakes_for_a_stop():
    """Every deliberate wait in a download is one the user may want to cut
    short, so it waits on the stop event, not on the clock."""
    dl = Download.__new__(Download)
    dl.event_abort = threading.Event()
    stop = threading.Event()
    stop.set()
    started = time.monotonic()
    dl._sleep_politely(30.0, stop)
    assert time.monotonic() - started < 1.0


# --- the catalog calls get to try more than once ----------------------------
# A 501-track playlist makes some 1500 calls to api.tidal.com in a row, and
# every one of them went out exactly once: tidalapi mounts no adapter, so the
# first 429 or 5xx failed that track, and a failed track failed the playlist.


_API_URL = "https://api.tidal.com/v1/"


def _hardened_session():
    """A tidalapi session carcass carrying nothing but the requests session the
    policy is mounted on."""
    session = tidalapi.Session.__new__(tidalapi.Session)
    session.request_session = requests.Session()
    harden_api_session(session)
    return session


def _api_retry(session):
    return session.request_session.get_adapter(_API_URL).max_retries


def test_the_catalog_session_retries_reads_and_honours_retry_after():
    retry = _api_retry(_hardened_session())
    assert retry.total >= 1
    assert 429 in retry.status_forcelist
    assert {500, 502, 503, 504}.issubset(set(retry.status_forcelist))
    assert retry.respect_retry_after_header is True
    # The last answer comes back as a response, so tidalapi's own
    # raise_for_status still produces the very same TooManyRequests / HTTPError
    # every caller already handles.
    assert retry.raise_on_status is False


def test_a_retry_after_header_can_hold_a_call_but_not_park_it():
    """urllib3 honours Retry-After verbatim and caps nothing, so one answer
    could park a download worker, and the queue behind it, for as long as it
    says."""
    retry = _api_retry(_hardened_session())
    short = SimpleNamespace(headers={"Retry-After": "5"})
    forever = SimpleNamespace(headers={"Retry-After": "3600"})
    assert retry.get_retry_after(short) == 5
    # urllib3 waits on the clock, not on an event, so this ceiling is also the
    # bound on how late a STOP taken during a rate limit can land.
    assert retry.get_retry_after(forever) * _API_RETRY_TOTAL <= 30
    assert retry.get_retry_after(SimpleNamespace(headers={})) is None
    # The ceiling has to survive every attempt of a retried call, and urllib3
    # rebuilds the policy between attempts.
    assert type(retry.new()) is type(retry)


@pytest.mark.parametrize("header", ["60s", "soon", "1.5", "", "please stand by"])
def test_an_unparseable_retry_after_is_no_header_not_an_exception(header):
    """urllib3 raises InvalidHeader for anything that is not a plain count of
    seconds or an HTTP date, and mounting a retry policy is what newly exposed
    the app to that: a proxy or a captive portal could leave a catalog call as
    an exception no caller expects, in place of the 429 they handle."""
    retry = _api_retry(_hardened_session())
    assert retry.get_retry_after(SimpleNamespace(headers={"Retry-After": header})) is None


def test_a_retry_wait_is_taken_on_the_stop_event_when_there_is_one():
    """urllib3 sleeps between retries on the wall clock, and with the queue
    running one job at a time that sleep sits in front of the whole queue:
    after STOP each worker still finished its ladder (up to 30 seconds per
    request, a minute for the two calls a track costs), so the next thing the
    user queued sat at Queued with nothing running."""
    retry = _api_retry(_hardened_session())
    stop = threading.Event()
    stop.set()
    api_waits_wake_for(stop)
    try:
        t0 = time.monotonic()
        with pytest.raises(ApiCallStopped):
            retry.sleep(SimpleNamespace(headers={"Retry-After": "10"}))
        assert time.monotonic() - t0 < 1.0, "a stopped job was held by a retry wait"
    finally:
        api_waits_wake_for(None)


def test_a_stopped_job_does_not_empty_its_retry_ladder_into_tidal():
    """The other half of cutting the wait short, and the one it created.

    urllib3 reads a returned sleep as "the wait is over" and fires the next
    attempt at once. So waking every worker on the STOP freed the queue (which
    is what it was for) and turned the ladder into a burst: twenty further
    requests inside a hundredth of a second, at the one moment TIDAL is
    already throttling us. The wait is the last point that can still refuse
    the call, so it refuses instead of returning.
    """
    retry = _api_retry(_hardened_session())
    stop = threading.Event()
    stop.set()
    api_waits_wake_for(stop)
    try:
        for header in ({"Retry-After": "10"}, {}):
            with pytest.raises(ApiCallStopped):
                retry.sleep(SimpleNamespace(headers=header))
    finally:
        api_waits_wake_for(None)


def test_a_wait_that_ends_on_the_clock_still_retries():
    """The regression guard: only a STOP refuses. A wait that simply timed out
    is an ordinary backoff and the ladder carries on."""
    retry = _api_retry(_hardened_session())
    waited: list[float] = []
    running = SimpleNamespace(wait=waited.append, is_set=lambda: False)
    api_waits_wake_for(running)
    try:
        retry.sleep(SimpleNamespace(headers={"Retry-After": "3"}))
    finally:
        api_waits_wake_for(None)

    assert waited == [3]


def test_a_retry_wait_off_a_download_thread_is_the_plain_wait_it_always_was():
    """A search or a browse page shares this session and has no job to be
    stopped, so those waits are unchanged."""
    retry = _api_retry(_hardened_session())
    api_waits_wake_for(None)
    slept: list[float] = []
    with patch("waves.config.time.sleep", slept.append):
        retry.sleep(SimpleNamespace(headers={"Retry-After": "7"}))
    assert slept == [7]


def test_a_retry_wait_honours_retry_after_over_the_backoff():
    """Same durations and the same order as urllib3's own sleep; only what is
    waited ON changed."""
    retry = _api_retry(_hardened_session())
    waited: list[float] = []
    # is_set False: this wait ends on the clock, not on a STOP.
    stop = SimpleNamespace(wait=waited.append, is_set=lambda: False)
    api_waits_wake_for(stop)
    try:
        retry.sleep(SimpleNamespace(headers={"Retry-After": "7"}))
        assert waited == [7]
        waited.clear()
        retry.sleep(SimpleNamespace(headers={}))  # no header: the backoff decides
        assert waited == [] or waited[0] >= 0
    finally:
        api_waits_wake_for(None)


def test_nothing_that_reached_tidal_is_ever_sent_twice():
    """A status or read retry is GET/HEAD only, so a sign-in or a playlist edit
    is never resubmitted. (A connect retry can repeat any method, but only when
    the connection never opened, so TIDAL never saw it.)"""
    assert set(_api_retry(_hardened_session()).allowed_methods) == {"GET", "HEAD"}


def test_a_guess_made_while_the_setting_was_inert_does_not_suddenly_take_effect():
    """Both fields were editable in Advanced while nothing read them, and they
    asked a different question then ("albums to process"), so a value on disk
    is a guess about something else. An old 60 would quietly add half an hour
    to a long playlist."""
    from waves.config import _migrate_settings
    from waves.model.cfg import Settings as ModelSettings

    stale = ModelSettings()
    stale.api_rate_limit_batch_size = 3
    stale.api_rate_limit_delay_sec = 60.0
    stale.api_rate_limit_wired_migrated = False

    assert _migrate_settings(stale) is True
    assert stale.api_rate_limit_delay_sec == ModelSettings().api_rate_limit_delay_sec
    assert stale.api_rate_limit_wired_migrated is True
    # How OFTEN a short pause happens is a number that means the same thing it
    # always did, so it is the user's and is left alone.
    assert stale.api_rate_limit_batch_size == 3

    # And a choice made from here on is the user's, and stands.
    stale.api_rate_limit_delay_sec = 12.0
    _migrate_settings(stale)
    assert stale.api_rate_limit_delay_sec == 12.0


def test_a_tuned_pace_survives_the_migration_running_a_second_time():
    """The marker is a field the previous release does not have, and that
    release rewrites settings.json from its own model on every launch: a
    downgrade and back strips the marker and runs this again. It used to reset
    both fields outright, so the pace the user had tuned in between was gone
    with no notice at all."""
    from waves.config import _migrate_settings
    from waves.model.cfg import Settings as ModelSettings

    tuned = ModelSettings()
    tuned.api_rate_limit_batch_size = 50
    tuned.api_rate_limit_delay_sec = 10.0
    tuned.api_rate_limit_wired_migrated = False  # as a round trip leaves it

    _migrate_settings(tuned)

    assert (tuned.api_rate_limit_batch_size, tuned.api_rate_limit_delay_sec) == (50, 10.0)


def test_workers_that_meet_the_same_rate_limit_do_not_retry_in_lockstep():
    """Three download workers hit the limit in the same instant; without jitter
    they would repeat together, at the moment TIDAL asked for less."""
    assert _api_retry(_hardened_session()).backoff_jitter > 0


def test_every_catalog_call_carries_a_timeout():
    """tidalapi passes none, so a black-holed connection parked a download
    worker forever and, with the queue running one job at a time, the queue
    with it."""
    adapter = _hardened_session().request_session.get_adapter(_API_URL)
    prepared = requests.Request("GET", _API_URL + "x").prepare()

    seen: dict = {}
    with patch.object(HTTPAdapter, "send", lambda self, request, **kw: seen.update(kw)):
        adapter.send(prepared)  # the caller gave no timeout, as tidalapi never does
    assert seen["timeout"] == REQUESTS_TIMEOUT_SEC

    seen.clear()
    with patch.object(HTTPAdapter, "send", lambda self, request, **kw: seen.update(kw)):
        adapter.send(prepared, timeout=5)  # a caller that asks for one keeps it
    assert seen["timeout"] == 5
