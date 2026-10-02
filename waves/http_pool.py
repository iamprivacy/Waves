"""One rule for every pooled HTTP adapter the app mounts: a connection left
waiting in a pool across a long gap, or across a sleep, is closed before it
can be reused.

CDNs and TIDAL's API close idle keep-alives in about a minute, and a Mac
that slept has lost every one of them. Finding that out the normal way means
polling the dead socket and closing it in each worker at once, and that is
exactly where a packaged build crashed on wake (issue #47: a segmentation
fault inside the compiled copy of urllib3's socket poll while three workers
cleared their dead connections together). Replacing the waiting connections
with empty slots skips that whole path: the pool simply opens fresh
connections, which it would have had to do anyway.

Both the segment-download session (``waves.download``) and the catalog
session (``waves.config``) mount an adapter built on this one: the catalog
fans out too (the artist page asks for its five sections from five threads,
search enrichment from more), so it needed the same protection.
"""

from __future__ import annotations

import contextlib
import logging
import queue
import threading
import time

from requests.adapters import HTTPAdapter

logger = logging.getLogger("waves.http_pool")


class IdleDropAdapter(HTTPAdapter):
    """HTTPAdapter whose pooled connections never outlive a long idle gap or
    a sleep."""

    # A pooled connection left alone this long (wall clock, so a sleep counts
    # in full) is dropped before it is reused.
    idle_reset_sec: float = 60.0
    # The wall clock running this far ahead of the monotonic clock between two
    # readings means the machine slept in between (the monotonic clock stops
    # during sleep on macOS): every keep-alive is dead then, however short the
    # gap looks, and a request that was in flight when the lid closed proves
    # nothing about the ones waiting in the pool.
    sleep_gap_sec: float = 5.0

    def __init__(self, *args, **kwargs) -> None:
        self._drop_lock = threading.Lock()
        self._last_wall: float = time.time()
        self._last_mono: float = time.monotonic()
        super().__init__(*args, **kwargs)

    def _stale_since(self, wall0: float, mono0: float) -> str:
        """Why the connections pooled since the reading (wall0, mono0) are not
        worth reusing: "sleep", "idle", or "" when they are fine."""
        wall = time.time() - wall0
        mono = time.monotonic() - mono0
        if wall - mono > self.sleep_gap_sec:
            return "sleep"
        if wall > self.idle_reset_sec:
            return "idle"
        return ""

    def _drop_pooled_connections(self) -> None:
        """Close every connection waiting in a pool and leave an empty slot in
        its place.

        Only the waiting ones: a connection checked out for a request in
        flight is past the socket poll already, and its worker closes it on
        return (the request fails or finishes on its own terms). A worker
        blocked on a full blocking pool's queue is handed one of the empty
        slots and opens a fresh connection, so the drop runs with requests in
        flight. The first version emptied the pools whole (``close`` plus
        ``clear``) and so had to wait for them to go quiet, and a download
        running across the sleep never let that happen for that wake."""
        for manager in (self.poolmanager, *self.proxy_manager.values()):
            for key in list(manager.pools.keys()):
                pool = None
                with contextlib.suppress(KeyError):
                    pool = manager.pools[key]
                slots = getattr(pool, "pool", None)
                if slots is None:
                    continue
                n = 0
                while True:
                    try:
                        conn = slots.get(block=False)
                    except queue.Empty:
                        break
                    n += 1
                    if conn is not None:
                        with contextlib.suppress(Exception):
                            conn.close()
                for _ in range(n):
                    with contextlib.suppress(queue.Full):
                        slots.put(None, block=False)

    def send(self, request, *args, **kwargs):
        wall0, mono0 = time.time(), time.monotonic()
        with self._drop_lock:
            why = self._stale_since(self._last_wall, self._last_mono)
            if why:
                logger.info(
                    "HTTP pool %s for %.0f s, dropping its connections before reuse", why, wall0 - self._last_wall
                )
                self._drop_pooled_connections()
            self._last_wall, self._last_mono = wall0, mono0
        try:
            return super().send(request, *args, **kwargs)
        finally:
            with self._drop_lock:
                # A request that ran across the sleep: the pool is dead
                # behind it, and its own finish must not pass as fresh use.
                if self._stale_since(wall0, mono0) == "sleep":
                    logger.info("HTTP request ran across a sleep, dropping the pool's connections")
                    self._drop_pooled_connections()
                self._last_wall, self._last_mono = time.time(), time.monotonic()
