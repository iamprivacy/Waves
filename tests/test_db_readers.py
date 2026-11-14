"""Per-thread read connections close with their thread, frozen or not.

The library cache and the ownership store each give every thread that asks a
presence question a read connection of its own. A sqlite3.Connection sits in
a reference cycle with its own statement cache, so dropping the last outside
reference does not close it: only a cyclic collection does. The app calls
gc.freeze() at boot and after every library publish, which moves every live
object into a generation the collector never visits again, so a connection
that was alive at a freeze and whose thread later ended stayed open for good.
A long download queue (a fresh thread per collection) ran the process out of
file descriptors.
"""

from __future__ import annotations

import gc
import os
import sqlite3
import sys
import threading

import pytest

from waves import db

_POSIX_FDS = next((d for d in ("/proc/self/fd", "/dev/fd") if os.path.isdir(d)), None)


def _open_fds() -> int:
    return len(os.listdir(_POSIX_FDS))


def _make_db(tmp_path) -> str:
    path = str(tmp_path / "store.sqlite3")
    conn = db.connect(path, kind=db.CACHE)
    conn.execute("CREATE TABLE t (x INTEGER)")
    conn.execute("INSERT INTO t VALUES (1)")
    conn.commit()
    conn.close()
    return path


def _readers(path: str) -> db.ThreadReaders:
    return db.ThreadReaders(lambda: sqlite3.connect(path, check_same_thread=False))


def _on_threads_with_freeze(n: int, work) -> None:
    """Run ``work`` on ``n`` short-lived threads, freezing the collector while
    each one still holds its connection, the way a library publish lands in
    the middle of a download."""
    for _ in range(n):
        asked = threading.Event()
        go_on = threading.Event()

        def body(asked=asked, go_on=go_on):
            work()
            asked.set()
            go_on.wait(10)

        t = threading.Thread(target=body)
        t.start()
        assert asked.wait(10)
        gc.freeze()
        go_on.set()
        t.join(10)


@pytest.fixture
def unfreeze():
    try:
        yield
    finally:
        gc.unfreeze()
        gc.collect()


# --- the shared helper -------------------------------------------------------


def test_a_thread_s_reader_is_reused_by_that_thread(tmp_path):
    readers = _readers(_make_db(tmp_path))
    try:
        assert readers.conn() is readers.conn()
        assert readers.conn().execute("SELECT x FROM t").fetchall() == [(1,)]
    finally:
        readers.close()


def test_a_reader_alive_at_a_freeze_closes_when_its_thread_ends(tmp_path, unfreeze):
    readers = _readers(_make_db(tmp_path))
    seen: list[sqlite3.Connection] = []

    def work():
        conn = readers.conn()
        conn.execute("SELECT x FROM t").fetchall()
        seen.append(conn)

    _on_threads_with_freeze(3, work)

    assert len(seen) == 3
    for conn in seen:
        with pytest.raises(sqlite3.ProgrammingError):
            conn.execute("SELECT 1")
    assert readers.connections() == []
    readers.close()


def test_close_takes_every_thread_s_reader(tmp_path):
    readers = _readers(_make_db(tmp_path))
    parked = threading.Event()
    release = threading.Event()

    def body():
        readers.conn().execute("SELECT 1")
        parked.set()
        release.wait(10)

    t = threading.Thread(target=body, daemon=True)
    t.start()
    try:
        assert parked.wait(10)
        mine = readers.conn()
        held = readers.connections()
        assert len(held) == 2 and any(c is mine for c in held)
        readers.close()
        for conn in held:
            with pytest.raises(sqlite3.ProgrammingError):
                conn.execute("SELECT 1")
        assert readers.connections() == []
    finally:
        release.set()
        t.join(10)


def test_a_read_after_close_opens_nothing(tmp_path):
    opened = []
    path = _make_db(tmp_path)

    def opener():
        conn = sqlite3.connect(path, check_same_thread=False)
        opened.append(conn)
        return conn

    readers = db.ThreadReaders(opener)
    readers.close()
    with pytest.raises(sqlite3.ProgrammingError):
        readers.conn()
    assert opened == []
    assert readers.closed


def test_drop_closes_only_the_calling_thread_s_reader(tmp_path):
    readers = _readers(_make_db(tmp_path))
    parked = threading.Event()
    release = threading.Event()
    theirs: list[sqlite3.Connection] = []

    def body():
        theirs.append(readers.conn())
        parked.set()
        release.wait(10)

    t = threading.Thread(target=body, daemon=True)
    t.start()
    try:
        assert parked.wait(10)
        mine = readers.conn()
        readers.drop()
        with pytest.raises(sqlite3.ProgrammingError):
            mine.execute("SELECT 1")
        assert theirs[0].execute("SELECT 1").fetchall() == [(1,)]
        assert readers.conn() is not mine
    finally:
        release.set()
        t.join(10)
        readers.close()


# --- through the real stores --------------------------------------------------


@pytest.mark.skipif(_POSIX_FDS is None or sys.platform == "win32", reason="counts open descriptors")
def test_library_cache_readers_do_not_pile_up_across_freezes(tmp_path, unfreeze):
    """The report's case: many short-lived threads each ask a presence
    question while a publish freezes the collector."""
    import waves.matching as matching
    from waves.library_index import LibraryIndex

    idx = LibraryIndex(str(tmp_path / "library.sqlite3"))
    key = matching.presence_key("Pain Remains", "Lorna Shore")
    try:
        idx.presence_facts(key)
        before = _open_fds()
        _on_threads_with_freeze(25, lambda: idx.presence_facts(key))
        grown = _open_fds() - before
    finally:
        idx.close()
    assert grown < 5, f"{grown} descriptors left open by 25 finished reader threads"


@pytest.mark.skipif(_POSIX_FDS is None or sys.platform == "win32", reason="counts open descriptors")
def test_ownership_readers_do_not_pile_up_across_freezes(tmp_path, unfreeze):
    from waves.ownership import OwnershipStore

    store = OwnershipStore(str(tmp_path / "ownership.sqlite3"))
    try:
        store.members_of("123")
        before = _open_fds()
        _on_threads_with_freeze(25, lambda: store.members_of("123"))
        grown = _open_fds() - before
    finally:
        store.close()
    assert grown < 5, f"{grown} descriptors left open by 25 finished reader threads"


def test_ownership_close_takes_every_thread_s_reader(tmp_path):
    from waves.ownership import OwnershipStore

    store = OwnershipStore(str(tmp_path / "ownership.sqlite3"))
    parked = threading.Event()
    release = threading.Event()

    def body():
        store.members_of("123")
        parked.set()
        release.wait(10)

    t = threading.Thread(target=body, daemon=True)
    t.start()
    try:
        assert parked.wait(10)
        held = store._readers.connections()
        assert held, "the parked thread's reader was never registered"
        store.close()
        for conn in held:
            with pytest.raises(sqlite3.ProgrammingError):
                conn.execute("SELECT 1")
    finally:
        release.set()
        t.join(10)


def test_ownership_read_cut_off_by_close_neither_warns_nor_switches(tmp_path, caplog, monkeypatch):
    """A read already past the closed check when close() takes its reader
    fails with sqlite's own error: no "could not use the store file"
    warning in the crash trail, and no switch to shared reads."""
    from waves.ownership import OwnershipStore

    store = OwnershipStore(str(tmp_path / "ownership.sqlite3"))

    class _CutOff:
        def execute(self, *_a):
            store.close()  # quit lands between the check and the read
            raise sqlite3.ProgrammingError("Cannot operate on a closed database.")

    monkeypatch.setattr(store._readers, "conn", lambda: _CutOff())
    caplog.set_level("WARNING", logger="waves")
    with pytest.raises(sqlite3.ProgrammingError):
        store.members_of("123")
    assert not [r for r in caplog.records if "could not use the store file" in r.getMessage()]
    assert store._shared_reads is False
