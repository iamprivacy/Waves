"""The download slots: what a track holds while it STREAMS, and nothing else.

A track used to hold its place in the download fan-out from its first byte to
its last step: stream, convert, extract, tag, copy onto the library volume,
politeness delay. With three workers and a library on a slow volume that read
as the whole queue freezing for minutes at a time (the header chart flat at
0 Mb/s, three items "finishing") while nothing at all was on the network.

This module is the process-wide ledger that separates the two halves:

* **Stream slots**, ``downloads_concurrent_max`` of them. A worker takes one
  before it asks TIDAL for a stream and holds it while the bytes come in.
  Waiters are served first come, first served, so an album's tracks still
  start in order and a re-submitted failure queues behind the tracks already
  waiting. A released slot cools down for the politeness delay before it can
  be taken again, which is what keeps the delay spacing downloads on the
  network now that the worker itself no longer sleeps.
* **Finishing seats**, ``downloads_finishing_max`` of them. When a stream
  ends, the worker asks for a seat: granted, it gives its stream slot back at
  once and finishes (convert, tag, copy) in the background while the next
  track streams in its place; refused (every seat taken, or the temp disk
  near full), it keeps the stream slot through the finish, which is exactly
  the old behavior and the safe fallback. Zero seats turns the feature off.
* **The job gate**, for the GUI's queue. Queue items may now overlap (the
  next one streams while the previous one finishes) but their streams must
  still start in queue order, so a later job's workers wait for a slot until
  every earlier job has reported that all of its streams have started, or
  has left.

Plain ``threading`` and ``shutil`` so it runs the same on macOS, Windows and
Linux, and Qt-free so the engine stays importable without PySide6. Nothing
about the user's library, tracks or paths passes through here: the only log
lines are the cooldown length and a refusal over low disk space.
"""

from __future__ import annotations

import logging
import shutil
import tempfile
import time
from collections import deque
from collections.abc import Callable, Iterable
from threading import Condition, Event, Lock

from waves.poolgauge import PoolGauge

logger = logging.getLogger("waves.slots")

# Free space the temp volume must keep for a finishing seat to be granted. A
# track finishing in the background holds its temp copies while the next one
# streams into its own, so the worst case is every seat plus every slot full
# of hi-res FLACs at once: eight seats and three streams of a 100 MB track
# with up to three temp copies each is about 3.3 GB. Below this the track
# keeps its stream slot and finishes first, the way it always did.
TEMP_FREE_MARGIN_BYTES: int = 3 * 1024**3

# Longest single wait inside the ledger before a waiter re-checks the stop
# events: the same cadence the engine's pace gate polls at, so a STOP lands
# on a worker waiting for a slot as promptly as on one waiting out a pause.
_POLL_S: float = 0.2

# Observational gauges for the verbose perf sampler, one per hold kind:
# ``dlstream`` is slots in use (and so, with the segment gauge, whether the
# network is being driven), ``dlfin`` is tracks finishing in the background.
# Registered in backend.py beside the engine's own gauges.
STREAM_GAUGE = PoolGauge(3)
FINISH_GAUGE = PoolGauge(3)


def _temp_free_bytes() -> int:
    """Free bytes on the volume the engine's temp files land on. Raises
    OSError like ``shutil.disk_usage`` does; the caller treats that as no
    room."""
    return shutil.disk_usage(tempfile.gettempdir()).free


class JobToken:
    """One queued item's place in the job gate (see StreamSlots.job_enter)."""

    __slots__ = ("streams_started",)

    def __init__(self) -> None:
        #: Set once the job has reported that every stream it will ever start
        #: has started (or was never going to), so later jobs may take slots.
        self.streams_started = False


class Ticket:
    """What ``acquire_stream`` hands back: the stream slot, and later maybe
    a finishing seat. Release it exactly once, in a ``finally``; releasing
    twice is harmless."""

    __slots__ = ("_cooldown_s", "_finish_limit", "_seat", "_slot", "_slots")

    def __init__(self, slots: StreamSlots, finish_limit: Callable[[], int], cooldown_s: float) -> None:
        self._slots = slots
        self._finish_limit = finish_limit
        self._cooldown_s = max(0.0, float(cooldown_s))
        self._slot = True
        self._seat = False

    @property
    def holds_stream_slot(self) -> bool:
        return self._slot

    @property
    def holds_finishing_seat(self) -> bool:
        return self._seat

    def try_finishing_seat(self) -> bool:
        """At stream end: move from a stream slot to a finishing seat if one
        is free and the temp volume has room. True when the slot was handed
        back (the next track may start streaming now); False keeps the slot
        through the finish, today's behavior."""
        if not self._slot or self._seat:
            return self._seat
        if self._slots._take_seat(self._finish_limit):
            self._seat = True
            self._slots._release_slot(self._cooldown_s)
            self._slot = False
            return True
        return False

    def release(self) -> None:
        """Free whatever is still held. Idempotent."""
        if self._slot:
            self._slot = False
            self._slots._release_slot(self._cooldown_s)
        if self._seat:
            self._seat = False
            self._slots._release_seat()


class StreamSlots:
    """The ledger itself. One instance per process (``STREAM_SLOTS``); tests
    build their own with a fake clock."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._cond = Condition(Lock())
        # Slots in use by a streaming worker. May exceed the limit for a while
        # after the user lowers the setting: nothing is granted until it drops
        # back under, and nothing running is interrupted.
        self._held = 0
        # Released slots still in their cooldown, as the clock reading each
        # becomes free again. A cooling slot counts as taken until then.
        self._cooling: list[float] = []
        # Waiters in arrival order: (ticket number, job token).
        self._waiting: deque[tuple[int, JobToken | None]] = deque()
        self._next_number = 0
        # Finishing seats in use.
        self._seats = 0
        # Live jobs in queue order (see job_enter).
        self._jobs: list[JobToken] = []
        self._seat_refusals_logged = 0

    # ----- stream slots -------------------------------------------------

    def acquire_stream(
        self,
        job: JobToken | None,
        *,
        stream_limit: Callable[[], int],
        finish_limit: Callable[[], int],
        cooldown_s: float = 0.0,
        stop_events: Iterable[Event | None] = (),
    ) -> Ticket | None:
        """Wait for a stream slot. Returns None when a stop event is set
        first, like the engine's other waits.

        ``stream_limit`` and ``finish_limit`` are read live on every check so
        a saved setting applies to the very next grant. ``cooldown_s`` is how
        long THIS slot cools after it is given back. ``job`` is the caller's
        place in the job gate, or None for a caller outside the queue (the
        CLI), which the gate does not hold.
        """
        stops = [e for e in stop_events if e is not None]
        with self._cond:
            number = self._next_number
            self._next_number += 1
            self._waiting.append((number, job))
            try:
                while True:
                    if any(e.is_set() for e in stops):
                        return None
                    now = self._clock()
                    self._purge_cooling(now)
                    if self._my_turn(number, job) and self._slot_free(stream_limit):
                        self._held += 1
                        STREAM_GAUGE.limit(max(1, self._limit_of(stream_limit)))
                        STREAM_GAUGE.start()
                        return Ticket(self, finish_limit, cooldown_s)
                    self._cond.wait(self._wait_span(now))
            finally:
                self._waiting.remove((number, job))
                self._cond.notify_all()

    def _limit_of(self, limit: Callable[[], int]) -> int:
        try:
            return int(limit())
        except (TypeError, ValueError, AttributeError):
            return 0

    def _slot_free(self, stream_limit: Callable[[], int]) -> bool:
        # A cooling slot is a taken slot until its clock reading passes, so
        # the delay spaces downloads on the network, not merely one worker.
        return self._held + len(self._cooling) < max(1, self._limit_of(stream_limit))

    def _purge_cooling(self, now: float) -> None:
        if self._cooling:
            self._cooling = [t for t in self._cooling if t > now]

    def _wait_span(self, now: float) -> float:
        """How long to sleep before the next check: until the nearest
        cooldown ends, capped so stops are seen promptly."""
        if self._cooling:
            return max(0.0, min(_POLL_S, min(self._cooling) - now))
        return _POLL_S

    def _my_turn(self, number: int, job: JobToken | None) -> bool:
        """First come, first served, among the waiters the job gate lets
        through. A waiter the gate holds does not hold up those behind it
        (an earlier job's re-submitted failures must not sit behind a later
        job's gated tracks), so the test is "no eligible waiter ahead of me"
        rather than "I am at the head"."""
        if not self._gate_open(job):
            return False
        for other_number, other_job in self._waiting:
            if other_number == number:
                return True
            if self._gate_open(other_job):
                return False
        return False

    def _release_slot(self, cooldown_s: float) -> None:
        with self._cond:
            self._held = max(0, self._held - 1)
            STREAM_GAUGE.stop()
            if cooldown_s > 0:
                self._cooling.append(self._clock() + cooldown_s)
                logger.debug(f"Next download will start in {cooldown_s} seconds.")
            self._cond.notify_all()

    # ----- finishing seats ----------------------------------------------

    def _take_seat(self, finish_limit: Callable[[], int]) -> bool:
        limit = max(0, self._limit_of(finish_limit))
        if limit <= 0:
            return False
        with self._cond:
            if self._seats >= limit:
                return False
            try:
                room = _temp_free_bytes() > TEMP_FREE_MARGIN_BYTES
            except OSError:
                room = False
            if not room:
                # INFO the first time, so the breadcrumb trail says why the
                # queue kept its old pace; DEBUG after that so a long run on
                # a full disk does not drown the ring in the same line.
                level = logging.INFO if self._seat_refusals_logged == 0 else logging.DEBUG
                self._seat_refusals_logged += 1
                logger.log(level, "Finishing seat refused: low disk space on the temp volume.")
                return False
            self._seats += 1
            FINISH_GAUGE.limit(limit)
            FINISH_GAUGE.start()
            return True

    def _release_seat(self) -> None:
        with self._cond:
            self._seats = max(0, self._seats - 1)
            FINISH_GAUGE.stop()
            self._cond.notify_all()

    # ----- the job gate -------------------------------------------------

    def job_enter(self) -> JobToken:
        """A queued item begins: take a place in queue order."""
        token = JobToken()
        with self._cond:
            self._jobs.append(token)
        return token

    def job_streams_started(self, token: JobToken) -> None:
        """Every stream this job will start has started: later jobs may take
        slots now, in order behind this job's own waiters."""
        with self._cond:
            token.streams_started = True
            self._cond.notify_all()

    def job_leave(self, token: JobToken) -> None:
        """The job is over, reported or not (a job that died early must not
        hold the queue)."""
        with self._cond:
            if token in self._jobs:
                self._jobs.remove(token)
            self._cond.notify_all()

    def _gate_open(self, job: JobToken | None) -> bool:
        if job is None or job not in self._jobs:
            return True
        for earlier in self._jobs:
            if earlier is job:
                return True
            if not earlier.streams_started:
                return False
        return True

    # ----- readings (tests and the perf sampler) ------------------------

    def held(self) -> int:
        with self._cond:
            return self._held

    def cooling(self) -> int:
        with self._cond:
            self._purge_cooling(self._clock())
            return len(self._cooling)

    def seats(self) -> int:
        with self._cond:
            return self._seats

    def waiting(self) -> int:
        with self._cond:
            return len(self._waiting)


#: The process-wide ledger every Download instance shares, so the limits
#: hold across queue items and not merely within one.
STREAM_SLOTS = StreamSlots()
