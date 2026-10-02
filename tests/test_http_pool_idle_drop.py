"""Pooled HTTP connections never outlive a long idle gap or a sleep (issue #47).

A Mac that slept comes back with every pooled keep-alive dead. Reusing one
means polling the dead socket and closing it, in every worker at once, and a
packaged build crashed right there (a segmentation fault inside the compiled
copy of urllib3's socket poll). ``waves.http_pool.IdleDropAdapter`` sidesteps
the path: before a request, connections left waiting longer than
``idle_reset_sec`` (wall clock), or across a sleep (the wall clock ran ahead
of the monotonic one), are closed and their slots left empty, so the request
opens a fresh connection instead of testing a stale one.

Two gaps the first version (download session only, pools emptied whole) left
open are pinned here: the catalog session shares the rule, since the artist
page asks it from five threads at once; and a request in flight across the
sleep no longer blocks the drop for that whole wake, nor does its own finish
count as fresh use.
"""

from __future__ import annotations

import queue

import pytest
import requests
import tidalapi

from waves.config import harden_api_session
from waves.http_pool import IdleDropAdapter


class _Clock:
    def __init__(self, monkeypatch, wall: float = 1000.0, mono: float = 10.0) -> None:
        self.wall = wall
        self.mono = mono
        monkeypatch.setattr("waves.http_pool.time.time", lambda: self.wall)
        monkeypatch.setattr("waves.http_pool.time.monotonic", lambda: self.mono)

    def tick(self, seconds: float) -> None:
        """Time passes with the machine awake."""
        self.wall += seconds
        self.mono += seconds

    def sleep(self, seconds: float) -> None:
        """The machine sleeps: the monotonic clock stops."""
        self.wall += seconds


class _Conn:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


def _adapter(monkeypatch, **kwargs):
    clock = _Clock(monkeypatch)
    adapter = IdleDropAdapter(pool_connections=2, pool_maxsize=2, **kwargs)
    adapter._last_wall, adapter._last_mono = clock.wall, clock.mono
    return adapter, clock


def _pool_with(adapter, *conns):
    pool = adapter.poolmanager.connection_from_host("example.com", 443, scheme="https")
    for _ in range(pool.pool.maxsize):
        pool.pool.get(block=False)
    for c in conns:
        pool.pool.put(c, block=False)
    while pool.pool.qsize() < pool.pool.maxsize:
        pool.pool.put(None, block=False)
    return pool


def _no_network(monkeypatch):
    def send(_adapter, request, *args, **kwargs):
        send.calls += 1
        return "sent"

    send.calls = 0
    monkeypatch.setattr("waves.http_pool.HTTPAdapter.send", send)
    return send


def test_both_sessions_carry_the_rule():
    from waves.download import Download

    assert isinstance(Download._shared_http().get_adapter("https://example.com"), IdleDropAdapter)
    session = tidalapi.Session.__new__(tidalapi.Session)
    session.request_session = requests.Session()
    harden_api_session(session)
    api = session.request_session.get_adapter("https://api.tidal.com/v1/")
    assert isinstance(api, IdleDropAdapter)


def test_the_catalog_adapter_keeps_its_default_timeout(monkeypatch):
    seen = {}

    def send(_adapter, request, *args, **kwargs):
        seen.update(kwargs)
        return "sent"

    monkeypatch.setattr("waves.http_pool.HTTPAdapter.send", send)
    session = tidalapi.Session.__new__(tidalapi.Session)
    session.request_session = requests.Session()
    harden_api_session(session)
    session.request_session.get_adapter("https://api.tidal.com/v1/").send("request")
    assert seen.get("timeout")


def test_connections_idle_past_the_threshold_are_closed_before_the_request(monkeypatch):
    adapter, clock = _adapter(monkeypatch)
    sent = _no_network(monkeypatch)
    conn = _Conn()
    pool = _pool_with(adapter, conn)

    clock.tick(adapter.idle_reset_sec + 1)
    assert adapter.send("request") == "sent"

    assert conn.closed
    assert all(c is None for c in list(pool.pool.queue))
    assert sent.calls == 1
    # The request itself counts as use: nothing is dropped a moment later.
    other = _Conn()
    pool.pool.get(block=False)
    pool.pool.put(other, block=False)
    clock.tick(1)
    adapter.send("request")
    assert not other.closed


def test_connections_used_within_the_threshold_are_kept(monkeypatch):
    adapter, clock = _adapter(monkeypatch)
    _no_network(monkeypatch)
    conn = _Conn()
    _pool_with(adapter, conn)

    clock.tick(adapter.idle_reset_sec - 1)
    adapter.send("request")

    assert not conn.closed


def test_a_short_gap_that_spans_a_sleep_still_drops(monkeypatch):
    """A lid closed for ten seconds kills every keep-alive just the same, and
    ten seconds is well inside the idle threshold: the sleep shows as the
    wall clock running ahead of the monotonic one."""
    adapter, clock = _adapter(monkeypatch)
    _no_network(monkeypatch)
    conn = _Conn()
    _pool_with(adapter, conn)

    clock.tick(2)
    clock.sleep(adapter.sleep_gap_sec + 1)
    clock.tick(1)
    adapter.send("request")

    assert conn.closed


def test_a_request_in_flight_across_the_sleep_does_not_block_the_drop(monkeypatch):
    """The first version emptied the pools whole, so it had to wait for every
    request to finish, and a download running while the Mac slept kept the
    dead pool alive for the whole wake. Now the drop touches only the
    connections WAITING in the pool, so it runs beside a request in flight,
    and the in-flight request's own finish is read as the sleep it was, not
    as fresh use."""
    adapter, clock = _adapter(monkeypatch)
    waiting = _Conn()
    pool = _pool_with(adapter, waiting)
    seen = []

    def nested(_adapter, request, *args, **kwargs):
        if request == "first":
            # The Mac sleeps with "first" in flight; "second" is the first
            # request after the wake.
            clock.sleep(30)
            clock.tick(1)
            adapter.send("second")
        seen.append(request)
        return "sent"

    monkeypatch.setattr("waves.http_pool.HTTPAdapter.send", nested)
    adapter.send("first")

    assert seen == ["second", "first"]
    assert waiting.closed
    assert all(c is None for c in list(pool.pool.queue))


def test_a_request_that_ran_across_the_sleep_drops_at_its_own_finish(monkeypatch):
    """No other request arrived during the sleep: the one that was in flight
    finishes after the wake, and the next request a moment later would read a
    fresh stamp. The finish itself drops the dead connections."""
    adapter, clock = _adapter(monkeypatch)
    waiting = _Conn()
    _pool_with(adapter, waiting)

    def slept_through(_adapter, request, *args, **kwargs):
        clock.sleep(30)
        clock.tick(1)
        return "sent"

    monkeypatch.setattr("waves.http_pool.HTTPAdapter.send", slept_through)
    adapter.send("request")

    assert waiting.closed


def test_a_failed_request_still_stamps_its_use(monkeypatch):
    adapter, clock = _adapter(monkeypatch)

    def boom(_adapter, request, *args, **kwargs):
        raise OSError("connection reset")

    monkeypatch.setattr("waves.http_pool.HTTPAdapter.send", boom)
    clock.tick(5)
    with pytest.raises(OSError):
        adapter.send("request")
    assert adapter._last_wall == clock.wall


def test_a_worker_waiting_on_the_blocking_pool_is_served_an_empty_slot(monkeypatch):
    """pool_block=True parks a worker on the pool's queue when every slot is
    checked out. A drop that emptied the queue would leave it waiting on a
    queue nobody fills again; the drop puts an empty slot back for each
    connection it closed, so the waiter gets one."""
    adapter, _clock = _adapter(monkeypatch, pool_block=True)
    pool = adapter.poolmanager.connection_from_host("example.com", 443, scheme="https")
    dead = _Conn()
    _pool_with(adapter, dead)
    n = pool.pool.maxsize

    adapter._drop_pooled_connections()

    assert dead.closed
    got = [pool.pool.get(block=False) for _ in range(n)]
    assert got == [None] * n
    with pytest.raises(queue.Empty):
        pool.pool.get(block=False)


def test_the_idle_gap_reads_the_wall_clock():
    """time.monotonic() does not advance while a Mac sleeps, so the gap that
    matters most would read as zero on it; the idle gap is wall time, and the
    monotonic clock serves only to recognise the sleep."""
    import inspect

    src = inspect.getsource(IdleDropAdapter._stale_since)
    assert "time.time()" in src
    assert "wall > self.idle_reset_sec" in src
