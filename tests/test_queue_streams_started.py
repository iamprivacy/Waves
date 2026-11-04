"""The next queued row starts once the current job has started its last stream.

THE BUG
-------
The bridge ran one queue item at a time and only started the next when the
previous Worker ENDED, which is after every one of its tracks was tagged and
copied onto the library volume (minutes, on a slow one) and after the
politeness delay. A single track's finish blocked the whole queue while the
network sat idle.

THE FIX
-------
_TrackedDownload counts its items as they take their stream slot (or settle
without one) and reports once, through _jobStreamsStarted, when the last has;
_pump_queue then starts the next row while up to downloads_finishing_max jobs
finish behind the streaming one. Streams still start in queue order (one job
streams at a time), a row for the same album as a live job waits, and a job
that dies before reporting gives the turn back when it ends.
"""

from __future__ import annotations

from threading import Lock, local
from unittest.mock import MagicMock, patch

from tests.test_queue_delta_protocol import _albumish, _HoldPool, _job_stub, _plain_relay
from waves.download_slots import StreamSlots
from waves.waves_ui import backend

# ----- the count, on the tracked download ---------------------------------- #


def _tracked():
    """A real _TrackedDownload without Download.__init__ (needs a session)."""
    td = backend._TrackedDownload.__new__(backend._TrackedDownload)
    td._track_signals = None
    td._tls = local()
    td._gate_lock = Lock()
    td._gate_left = None
    td._gate_fired = False
    td.fired = 0
    td._on_streams_started = lambda: setattr(td, "fired", td.fired + 1)
    td._job_token = None
    return td


def _settle_as_new_thread(td):
    td._tls.gate_counted = False
    td._gate_settle()


def test_the_report_fires_once_when_the_last_item_is_counted():
    td = _tracked()
    td.expect_items(3)
    _settle_as_new_thread(td)
    _settle_as_new_thread(td)
    assert td.fired == 0
    _settle_as_new_thread(td)
    assert td.fired == 1
    # A re-submit pass of failed items counts past zero and fires nothing.
    _settle_as_new_thread(td)
    _settle_as_new_thread(td)
    assert td.fired == 1


def test_an_item_is_counted_once_however_many_times_it_settles():
    """The stream-acquired hook and item()'s exit both settle on the same
    thread; the second is a no-op."""
    td = _tracked()
    td.expect_items(2)
    td._tls.gate_counted = False
    td._note_stream_acquired(MagicMock())
    td._gate_settle()
    td._gate_settle()
    assert td.fired == 0
    _settle_as_new_thread(td)
    assert td.fired == 1


def test_nothing_to_count_fires_at_once():
    td = _tracked()
    td.expect_items(0)
    assert td.fired == 1


def test_a_job_that_was_never_told_its_count_never_fires():
    td = _tracked()
    _settle_as_new_thread(td)
    _settle_as_new_thread(td)
    assert td.fired == 0


def test_a_collection_learns_its_count_from_the_engines_enumeration():
    """An album or playlist never calls expect_items itself: the engine's
    items() reports the enumerated size through _note_list_size, and that has
    to arm the count, or the collection holds the streaming turn until it
    ends (the queue overlap would only ever work for single tracks)."""
    td = _tracked()
    td._note_list_size(2)
    assert td.list_item_count == 2
    _settle_as_new_thread(td)
    assert td.fired == 0
    _settle_as_new_thread(td)
    assert td.fired == 1


def test_the_engine_reports_the_enumerated_size_before_any_item_runs():
    """items() is the only caller of the hook; pin that it still calls it
    with the list size ahead of the fan-out."""
    import inspect

    from waves.download import Download

    src = inspect.getsource(Download.items)
    assert "self._note_list_size(list_total)" in src
    assert src.index("self._note_list_size(list_total)") < src.index("self._execute_collection_downloads(")


def test_an_owned_skip_and_a_duplicate_track_both_count():
    """The owned-track skip returns before the engine's item() runs, and a
    playlist can list one track twice: both still count toward the report,
    which is why the count lives here and is a counter, not a set of ids."""
    td = _tracked()
    td.expect_items(3)
    media = MagicMock()
    media.id = "77"
    with (
        patch.object(backend._TrackedDownload, "_claim_decision", return_value=("skip", {})),
        patch.object(backend._TrackedDownload, "_emit_skip", return_value=(True, "")),
    ):
        assert td.item("t", media=media) == (True, "")
        assert td.fired == 0
        td.item("t", media=media)
        assert td.fired == 0
        td.item("t", media=media)
    assert td.fired == 1


def test_the_report_opens_the_slot_ledgers_job_gate_too():
    ledger = StreamSlots()
    td = _tracked()
    td._job_token = ledger.job_enter()
    td.expect_items(1)
    _settle_as_new_thread(td)
    assert td._job_token.streams_started is True
    ledger.job_leave(td._job_token)


# ----- the scheduler, on the bridge ---------------------------------------- #


def _queue(stub, n, media=None):
    with _plain_relay():
        for i in range(n):
            mid = media or f"m{i}"
            stub._download(_albumish(mid), "track", mid, "{title}", False, mid)


def test_the_next_row_starts_when_the_streaming_job_reports_not_when_it_ends():
    stub = _job_stub(pool=_HoldPool())
    stub.settings.data.downloads_finishing_max = 3
    _queue(stub, 3)
    assert len(stub.dl_pool.started) == 1
    first = stub._streaming_qid
    assert stub._live_qids == {first}
    with _plain_relay():
        stub._on_job_streams_started(first)
    assert len(stub.dl_pool.started) == 2, "the second row started while the first is still live"
    second = stub._streaming_qid
    assert second != first and stub._live_qids == {first, second}
    # The second has not reported: the third waits however many seats are free.
    assert len(stub._job_specs) == 1
    with _plain_relay():
        stub._on_job_finished(first)
    assert stub._live_qids == {second} and len(stub.dl_pool.started) == 2


def test_zero_finishing_seats_is_one_job_at_a_time_as_before():
    stub = _job_stub(pool=_HoldPool())
    stub.settings.data.downloads_finishing_max = 0
    _queue(stub, 2)
    first = stub._streaming_qid
    with _plain_relay():
        stub._on_job_streams_started(first)
    assert len(stub.dl_pool.started) == 1, "no seat: the next row waits for the Worker to end"
    assert stub._streaming_qid is None and stub._live_qids == {first}
    with _plain_relay():
        stub._on_job_finished(first)
    assert len(stub.dl_pool.started) == 2
    assert stub._live_qids == {stub._streaming_qid}


def test_the_live_jobs_are_capped_by_the_finishing_count():
    stub = _job_stub(pool=_HoldPool())
    stub.settings.data.downloads_finishing_max = 1
    _queue(stub, 3)
    with _plain_relay():
        stub._on_job_streams_started(stub._streaming_qid)
        stub._on_job_streams_started(stub._streaming_qid)
    assert len(stub.dl_pool.started) == 2, "one streaming plus one finishing is the cap at 1 seat"
    assert stub._streaming_qid is None and len(stub._live_qids) == 2
    with _plain_relay():
        stub._on_job_finished(min(stub._live_qids))
    assert len(stub.dl_pool.started) == 3


def test_a_row_for_the_same_album_as_a_live_job_waits_for_it():
    """The bridge never queues one album twice while its row is live, so the
    shape is made by hand: the waiting spec is pointed at the live job's
    album (the name reservations are per job, so two jobs writing one album
    at once could number a duplicate)."""
    stub = _job_stub(pool=_HoldPool())
    stub.settings.data.downloads_finishing_max = 3
    _queue(stub, 2)
    first = stub._streaming_qid
    waiting = next(iter(stub._job_specs))
    stub._job_specs[waiting].media_id = stub._queue_item(first)["media_id"]
    with _plain_relay():
        stub._on_job_streams_started(first)
    assert len(stub.dl_pool.started) == 1, "two jobs writing one album at once could number a duplicate"
    assert stub._streaming_qid is None
    with _plain_relay():
        stub._on_job_finished(first)
    assert len(stub.dl_pool.started) == 2


def test_a_dropped_row_still_holds_its_album_while_its_job_finishes():
    """RETRY on a stopped row (and the clears) drop the row while its job may
    still be tagging and moving tracks. The album has to stay held until the
    job ends, or the retry writes the same folder alongside it."""
    stub = _job_stub(pool=_HoldPool())
    stub.settings.data.downloads_finishing_max = 3
    _queue(stub, 1, media="album")
    first = stub._streaming_qid
    with _plain_relay():
        stub._on_job_streams_started(first)
        stub._remove_row(first)
    _queue(stub, 1, media="album")
    assert len(stub.dl_pool.started) == 1, "the retry ran beside the job still finishing the same album"
    with _plain_relay():
        stub._on_job_finished(first)
    assert len(stub.dl_pool.started) == 2
    assert stub._live_media == {stub._streaming_qid: "album"}


def test_a_job_that_dies_before_reporting_gives_the_turn_back():
    stub = _job_stub(pool=_HoldPool())
    stub.settings.data.downloads_finishing_max = 3
    _queue(stub, 2)
    first = stub._streaming_qid
    with _plain_relay():
        stub._on_job_finished(first)
    assert stub._streaming_qid is not None and stub._streaming_qid != first
    assert stub._live_qids == {stub._streaming_qid}
    assert len(stub.dl_pool.started) == 2


def test_a_stale_report_from_an_ended_job_starts_nothing_extra():
    stub = _job_stub(pool=_HoldPool())
    stub.settings.data.downloads_finishing_max = 3
    _queue(stub, 2)
    first = stub._streaming_qid
    with _plain_relay():
        stub._on_job_streams_started(first)
    second = stub._streaming_qid
    with _plain_relay():
        stub._on_job_streams_started(first)  # again, late
    assert stub._streaming_qid == second, "a report from a job that is not the streaming one changes nothing"
    assert len(stub.dl_pool.started) == 2
