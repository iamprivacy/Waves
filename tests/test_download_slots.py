"""The download slot ledger (waves/download_slots.py), on its own.

A track used to hold its place in the download fan-out from first byte to
last step, so three tracks copying onto a slow library volume kept the
network idle for minutes. The ledger separates the stream slot (held while
the bytes come in) from the finishing seat (held while the file is
converted, tagged and copied), and carries the politeness delay as the
slot's cooldown. These tests pin that contract with no engine and no Qt.
"""

from __future__ import annotations

import threading
import time
from threading import Event

import pytest

from waves import download_slots
from waves.download_slots import StreamSlots


class _Clock:
    """A clock the test advances by hand, so cooldown math is exact."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _ledger(clock=None):
    return StreamSlots(clock=clock or time.monotonic)


def _acquire(ledger, job=None, streams=3, seats=3, cooldown=0.0, stops=()):
    return ledger.acquire_stream(
        job,
        stream_limit=lambda: streams,
        finish_limit=lambda: seats,
        cooldown_s=cooldown,
        stop_events=stops,
    )


@pytest.fixture(autouse=True)
def _plenty_of_disk(monkeypatch):
    monkeypatch.setattr(download_slots, "_temp_free_bytes", lambda: 10 * 1024**3)


def _in_thread(fn):
    t = threading.Thread(target=fn, daemon=True)
    t.start()
    return t


# ----- stream slots ---------------------------------------------------------


def test_slots_are_capped_by_the_live_limit():
    ledger = _ledger()
    held = [_acquire(ledger, streams=2) for _ in range(2)]
    assert all(held) and ledger.held() == 2

    got = []
    t = _in_thread(lambda: got.append(_acquire(ledger, streams=2)))
    t.join(0.3)
    assert t.is_alive(), "a third acquire must wait while two are held"

    held[0].release()
    t.join(2)
    assert not t.is_alive() and got[0] is not None
    assert ledger.held() == 2


def test_waiters_are_served_first_come_first_served():
    ledger = _ledger()
    first = _acquire(ledger, streams=1)
    order: list[int] = []
    started = []

    def waiter(i):
        started.append(i)
        t = _acquire(ledger, streams=1)
        order.append(i)
        t.release()

    threads = []
    for i in range(5):
        threads.append(_in_thread(lambda i=i: waiter(i)))
        # Each waiter must be queued before the next arrives for the order
        # to mean anything.
        for _ in range(100):
            if ledger.waiting() >= i + 1:
                break
            time.sleep(0.005)
    first.release()
    for t in threads:
        t.join(3)
    assert order == [0, 1, 2, 3, 4]


def test_a_stop_wakes_a_waiter_with_none():
    ledger = _ledger()
    held = _acquire(ledger, streams=1)
    stop = Event()
    got = []
    t = _in_thread(lambda: got.append(_acquire(ledger, streams=1, stops=(None, stop))))
    t.join(0.3)
    assert t.is_alive()
    stop.set()
    t.join(2)
    assert not t.is_alive() and got == [None]
    assert ledger.waiting() == 0
    held.release()


def test_a_lowered_limit_grants_nothing_until_held_drops_under_it():
    ledger = _ledger()
    limit = {"n": 3}
    held = [_acquire(ledger, streams=3) for _ in range(3)]
    limit["n"] = 1
    got = []
    t = _in_thread(
        lambda: got.append(
            ledger.acquire_stream(None, stream_limit=lambda: limit["n"], finish_limit=lambda: 0, stop_events=())
        )
    )
    held[0].release()
    held[1].release()
    t.join(0.3)
    assert t.is_alive(), "two released of three held is still 1 held against a limit of 1"
    held[2].release()
    t.join(2)
    assert not t.is_alive() and got[0] is not None


def test_a_raised_limit_applies_to_the_next_acquire():
    ledger = _ledger()
    limit = {"n": 1}
    first = ledger.acquire_stream(None, stream_limit=lambda: limit["n"], finish_limit=lambda: 0, stop_events=())
    got = []
    t = _in_thread(
        lambda: got.append(
            ledger.acquire_stream(None, stream_limit=lambda: limit["n"], finish_limit=lambda: 0, stop_events=())
        )
    )
    t.join(0.3)
    assert t.is_alive()
    limit["n"] = 2
    t.join(2)
    assert not t.is_alive() and got[0] is not None
    first.release()


# ----- cooldown -------------------------------------------------------------


def test_a_released_slot_cools_down_before_it_can_be_taken_again():
    clock = _Clock()
    ledger = _ledger(clock)
    ticket = _acquire(ledger, streams=1, cooldown=4.0)
    ticket.release()
    assert ledger.held() == 0 and ledger.cooling() == 1

    got = []
    t = _in_thread(lambda: got.append(_acquire(ledger, streams=1)))
    t.join(0.5)
    assert t.is_alive(), "the slot is still cooling"
    clock.now += 4.0
    t.join(2)
    assert not t.is_alive() and got[0] is not None
    assert ledger.cooling() == 0


def test_a_zero_cooldown_frees_the_slot_at_once():
    ledger = _ledger()
    _acquire(ledger, streams=1, cooldown=0.0).release()
    assert ledger.cooling() == 0
    assert _acquire(ledger, streams=1) is not None


def test_the_cooldown_is_logged_like_the_old_delay(caplog):
    ledger = _ledger(_Clock())
    with caplog.at_level("DEBUG", logger="waves.slots"):
        _acquire(ledger, streams=1, cooldown=3.5).release()
    assert "Next download will start in 3.5 seconds." in caplog.text


# ----- finishing seats ------------------------------------------------------


def test_a_seat_hands_the_slot_back_and_is_freed_on_release():
    ledger = _ledger()
    ticket = _acquire(ledger, streams=1, seats=1)
    assert ticket.try_finishing_seat() is True
    assert ticket.holds_finishing_seat and not ticket.holds_stream_slot
    assert ledger.held() == 0 and ledger.seats() == 1
    # The slot is free for the next stream while this one finishes.
    nxt = _acquire(ledger, streams=1, seats=1)
    assert nxt is not None
    ticket.release()
    assert ledger.seats() == 0
    nxt.release()


def test_a_seat_is_refused_at_the_cap_and_the_slot_stays_held():
    ledger = _ledger()
    a = _acquire(ledger, streams=2, seats=1)
    b = _acquire(ledger, streams=2, seats=1)
    assert a.try_finishing_seat() is True
    assert b.try_finishing_seat() is False
    assert b.holds_stream_slot and not b.holds_finishing_seat
    assert ledger.held() == 1 and ledger.seats() == 1
    b.release()
    assert ledger.held() == 0
    a.release()


def test_zero_seats_turns_the_feature_off():
    ledger = _ledger()
    ticket = _acquire(ledger, streams=1, seats=0)
    assert ticket.try_finishing_seat() is False
    assert ledger.held() == 1
    ticket.release()


def test_a_seat_is_refused_on_low_disk(monkeypatch, caplog):
    ledger = _ledger()
    monkeypatch.setattr(download_slots, "_temp_free_bytes", lambda: download_slots.TEMP_FREE_MARGIN_BYTES - 1)
    ticket = _acquire(ledger, streams=1, seats=3)
    with caplog.at_level("INFO", logger="waves.slots"):
        assert ticket.try_finishing_seat() is False
    assert "low disk" in caplog.text
    assert ledger.held() == 1 and ledger.seats() == 0
    ticket.release()


def test_a_disk_check_that_fails_counts_as_no_room(monkeypatch):
    ledger = _ledger()

    def boom():
        raise OSError("no such volume")

    monkeypatch.setattr(download_slots, "_temp_free_bytes", boom)
    ticket = _acquire(ledger, streams=1, seats=3)
    assert ticket.try_finishing_seat() is False
    ticket.release()
    assert ledger.held() == 0


def test_release_is_idempotent_and_a_second_seat_ask_is_a_no_op():
    ledger = _ledger()
    ticket = _acquire(ledger, streams=1, seats=1)
    assert ticket.try_finishing_seat() is True
    assert ticket.try_finishing_seat() is True
    ticket.release()
    ticket.release()
    assert ledger.held() == 0 and ledger.seats() == 0
    assert ticket.try_finishing_seat() is False


def test_seat_release_with_a_cooldown_spaces_the_next_stream_from_stream_end():
    """The cooldown starts when the slot is handed back at stream end, not
    when the track is done: that is the point of the move."""
    clock = _Clock()
    ledger = _ledger(clock)
    ticket = _acquire(ledger, streams=1, seats=1, cooldown=3.0)
    ticket.try_finishing_seat()
    assert ledger.cooling() == 1
    clock.now += 3.0
    assert ledger.cooling() == 0
    nxt = _acquire(ledger, streams=1, seats=1)
    assert nxt is not None
    ticket.release()  # the finish ends later; no second cooldown
    assert ledger.cooling() == 0
    nxt.release()


# ----- the job gate ---------------------------------------------------------


def test_a_later_job_waits_until_the_earlier_one_reports_streams_started():
    ledger = _ledger()
    j1 = ledger.job_enter()
    j2 = ledger.job_enter()
    got = []
    t = _in_thread(lambda: got.append(_acquire(ledger, job=j2, streams=3)))
    t.join(0.3)
    assert t.is_alive(), "job 2 must not take a slot before job 1 has started its streams"
    first = _acquire(ledger, job=j1, streams=3)
    assert first is not None, "job 1 itself is never held by the gate"
    ledger.job_streams_started(j1)
    t.join(2)
    assert not t.is_alive() and got[0] is not None
    first.release()
    got[0].release()
    ledger.job_leave(j1)
    ledger.job_leave(j2)


def test_a_job_that_leaves_without_reporting_unblocks_the_next():
    ledger = _ledger()
    j1 = ledger.job_enter()
    j2 = ledger.job_enter()
    got = []
    t = _in_thread(lambda: got.append(_acquire(ledger, job=j2, streams=3)))
    t.join(0.3)
    assert t.is_alive()
    ledger.job_leave(j1)
    t.join(2)
    assert not t.is_alive() and got[0] is not None
    got[0].release()
    ledger.job_leave(j2)


def test_a_gated_waiter_does_not_hold_up_an_earlier_jobs_waiter_behind_it():
    """Job 1's re-submitted failure arrives after job 2's gated track: it
    must still get the slot, or the gate would deadlock the queue (job 2
    waits for job 1 to start all its streams, job 1 waits behind job 2)."""
    ledger = _ledger()
    j1 = ledger.job_enter()
    j2 = ledger.job_enter()
    blocker = _acquire(ledger, job=j1, streams=1)
    got2, got1 = [], []
    t2 = _in_thread(lambda: got2.append(_acquire(ledger, job=j2, streams=1)))
    for _ in range(100):
        if ledger.waiting() == 1:
            break
        time.sleep(0.005)
    t1 = _in_thread(lambda: got1.append(_acquire(ledger, job=j1, streams=1)))
    blocker.release()
    t1.join(2)
    assert not t1.is_alive() and got1[0] is not None, "job 1's later waiter got through the gated one"
    assert t2.is_alive()
    ledger.job_streams_started(j1)
    got1[0].release()
    t2.join(2)
    assert not t2.is_alive() and got2[0] is not None
    got2[0].release()
    ledger.job_leave(j1)
    ledger.job_leave(j2)


def test_a_caller_without_a_job_is_not_gated():
    ledger = _ledger()
    j1 = ledger.job_enter()
    ticket = _acquire(ledger, job=None, streams=1)
    assert ticket is not None
    ticket.release()
    ledger.job_leave(j1)


def test_the_gauges_follow_the_holds():
    ledger = _ledger()
    before = (download_slots.STREAM_GAUGE.activeThreadCount(), download_slots.FINISH_GAUGE.activeThreadCount())
    ticket = _acquire(ledger, streams=2, seats=2)
    assert download_slots.STREAM_GAUGE.activeThreadCount() == before[0] + 1
    assert download_slots.STREAM_GAUGE.maxThreadCount() == 2
    ticket.try_finishing_seat()
    assert download_slots.STREAM_GAUGE.activeThreadCount() == before[0]
    assert download_slots.FINISH_GAUGE.activeThreadCount() == before[1] + 1
    ticket.release()
    assert download_slots.FINISH_GAUGE.activeThreadCount() == before[1]
