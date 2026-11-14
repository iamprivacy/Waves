"""How Waves opens its sqlite stores, and the one list of every file it keeps.

Waves keeps its data in several files under the config folder, one per
feature, on purpose: each has its own writer (the library scan runs in a child
process, downloads and the Releases sweep on worker threads, MusicBrainz
answers on whichever worker asked), and sqlite lets one writer at a time touch
a file. Separate files mean a long scan never makes a download wait for the
lock. Reading across them is the catalog's job (see ``waves.catalog``).

Two things live here:

* ``connect``: the one way a store opens its writing connection, so every
  file gets the same settings. WAL, so readers never wait on the writer; a
  cap on how large the ``-wal`` file stays after a checkpoint, so a big scan
  does not leave tens of MB behind; and, for a CACHE (rebuildable) store,
  ``synchronous=NORMAL``: under WAL that never corrupts a file, it only lets
  a power cut take the last few commits, which a rescan or a re-ask restores.
  A STATE store (the user's own record: follows, what was downloaded) keeps
  sqlite's default full sync.
* ``STORES``: every file Waves keeps, what kind it is, and which wipes take it.
  The factory reset and the catalog both read this list, so a new store is
  added in one place and cannot be forgotten by either.

A third, ``ThreadReaders``, is the per-thread read connections the library
cache and the ownership store hand their presence lookups to.

Pure standard library, no Qt.
"""

from __future__ import annotations

import contextlib
import re
import sqlite3
import weakref
from collections.abc import Callable
from dataclasses import dataclass
from threading import Lock, local

# What a store holds, for the sync level and for the catalog's notes.
STATE = "state"  # the user's own record; cannot be rebuilt
CACHE = "cache"  # rebuilt by a rescan, a re-ask or a refetch

# How large a store's -wal file may stay once a checkpoint has emptied it.
JOURNAL_SIZE_LIMIT = 16 * 1024 * 1024

_WAL_SIDECARS = ("-wal", "-shm")


@dataclass(frozen=True)
class Store:
    """One file Waves keeps under the config folder.

    ``schema`` is the name the catalog attaches a sqlite store under ('' for
    a file the catalog does not read). ``pattern`` matches the per-root
    library files whose names carry a digest. ``on_logout`` says signing out
    removes the file (only the account's own page snapshots do)."""

    file: str
    kind: str
    schema: str = ""
    sqlite: bool = True
    sidecars: tuple[str, ...] = _WAL_SIDECARS
    pattern: re.Pattern | None = None
    on_logout: bool = False


STORES: tuple[Store, ...] = (
    # The library scan cache, one file per library folder (library_index.
    # cache_file_for_root); the plain name is the pre-per-root file and the
    # name used while no library is set.
    Store(
        "library.sqlite3",
        CACHE,
        schema="lib",
        pattern=re.compile(r"library-[0-9a-f]{12}\.sqlite3(-wal|-shm)?\Z"),
    ),
    Store("releases.sqlite3", STATE, schema="rel"),
    Store("ownership.sqlite3", STATE, schema="own"),
    Store("musicbrainz.sqlite3", CACHE, schema="mb"),
    # Opened only for the length of a FLAC check, in rollback-journal mode.
    Store("flac-check.sqlite3", CACHE, schema="flac", sidecars=("-journal", "-wal", "-shm")),
    # The arbiter's own cache from before the shared MusicBrainz file; no
    # longer read, only wiped.
    Store("mbarbiter.sqlite3", CACHE),
    # The account's page snapshot and last searches (backend._save_page_cache,
    # waves.page_store), removed on sign-out as well. Not in the catalog: a
    # sign-out deletes it while the app runs.
    Store("page_cache.sqlite3", CACHE, on_logout=True),
    # Where earlier builds kept the snapshot and searches; imported and
    # removed by the first load, listed so a leftover still falls to the wipes.
    Store("page_cache.json", CACHE, sqlite=False, sidecars=(".tmp",), on_logout=True),
    Store("search_cache.json", CACHE, sqlite=False, sidecars=(".tmp",), on_logout=True),
    Store("browse_tile_art.json", CACHE, sqlite=False, sidecars=(".tmp",)),
)


def store(schema: str) -> Store:
    """The registered store attached under ``schema``."""
    for s in STORES:
        if s.schema == schema:
            return s
    raise KeyError(schema)


def wipe_names() -> tuple[str, ...]:
    """Every exact file name the factory reset removes for the stores:
    each file and each of its sidecars."""
    return tuple(name for s in STORES for name in (s.file, *(s.file + suffix for suffix in s.sidecars)))


def wipe_patterns() -> tuple[re.Pattern, ...]:
    """The anchored patterns for store files whose names carry a digest."""
    return tuple(s.pattern for s in STORES if s.pattern is not None)


def json_names() -> tuple[str, ...]:
    """The JSON stores, for the reset's staging-leftover pattern."""
    return tuple(s.file for s in STORES if not s.sqlite)


def connect(path: str, *, kind: str) -> sqlite3.Connection:
    """A store's writing connection, set up the same way for every file.

    Shareable across threads (``check_same_thread=False``): every store
    guards its connection with its own lock. ``:memory:`` takes the same
    pragmas harmlessly (an in-memory file has no WAL to keep)."""
    if kind not in (STATE, CACHE):
        raise ValueError(kind)
    conn = sqlite3.connect(path, check_same_thread=False)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(f"PRAGMA journal_size_limit={JOURNAL_SIZE_LIMIT}")
        if kind == CACHE:
            conn.execute("PRAGMA synchronous=NORMAL")
    except sqlite3.Error:
        # A file that is not a database: no handle may stay open on it (a
        # caller that removes it would be refused on Windows).
        conn.close()
        raise
    return conn


# sqlite's own wording for a read on a closed connection.
_CLOSED = "Cannot operate on a closed database."


class _Reader:
    """One thread's read connection, held by that thread's local storage.

    A sqlite3.Connection sits in a reference cycle with its own statement
    cache, so losing its last outside reference does not close it: only a
    cyclic collection does, and the app freezes the collector (gc.freeze at
    boot and after every library publish), after which a connection that was
    alive at the freeze is never collected at all. This wrapper is not in a
    cycle, so plain reference counting frees it the moment its thread's
    local storage goes, frozen or not, and it closes the connection then.

    It can also be weakly referenced (a Connection cannot), which is how
    ``ThreadReaders`` finds every live reader without keeping one alive."""

    __slots__ = ("__weakref__", "conn")

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def close(self) -> None:
        # A second close is a no-op in sqlite3. Suppressed because a
        # straggler read can be inside the connection as another thread
        # closes it, and at interpreter exit the module may be half gone.
        with contextlib.suppress(Exception):
            self.conn.close()

    def __del__(self) -> None:
        self.close()


class ThreadReaders:
    """A read connection per thread for one store file.

    ``opener`` makes a new connection; it must pass ``check_same_thread=
    False`` so ``close`` can shut another thread's connection (each is still
    only USED by the thread it was made on). A thread's connection closes
    when the thread ends, when it calls ``drop``, or when anyone calls
    ``close``, which also refuses any later ``conn`` with sqlite's own
    closed error rather than opening a fresh connection."""

    def __init__(self, opener: Callable[[], sqlite3.Connection]) -> None:
        self._opener = opener
        self._local = local()
        self._lock = Lock()
        # Weak references to every reader handed out, so close() reaches
        # every thread's connection, not only the caller's (an open handle
        # refuses a delete on Windows, and the factory reset closes a store
        # and then removes its file). discard as the callback: the entry goes
        # the moment the owning thread does, with no lock to wait for.
        self._refs: set = set()
        self._closed = False

    @property
    def closed(self) -> bool:
        return self._closed

    def conn(self) -> sqlite3.Connection:
        """This thread's connection, opened on its first use."""
        reader = getattr(self._local, "reader", None)
        if reader is not None:
            return reader.conn
        if self._closed:
            raise sqlite3.ProgrammingError(_CLOSED)
        reader = _Reader(self._opener())
        with self._lock:
            if self._closed:
                reader.close()
                raise sqlite3.ProgrammingError(_CLOSED)
            self._refs.add(weakref.ref(reader, self._refs.discard))
        self._local.reader = reader
        return reader.conn

    def drop(self) -> None:
        """Close and forget the calling thread's connection, if it has one."""
        reader = getattr(self._local, "reader", None)
        self._local.reader = None
        if reader is not None:
            reader.close()

    def close(self) -> None:
        """Close every thread's connection and refuse new ones."""
        with self._lock:
            self._closed = True
            refs, self._refs = self._snapshot(), set()
        # Closed explicitly, BEFORE this thread's own reference is dropped.
        for ref in refs:
            reader = ref()
            if reader is not None:
                reader.close()
        self._local.reader = None

    def connections(self) -> list[sqlite3.Connection]:
        """The connections still open (for tests)."""
        out = []
        for ref in self._snapshot():
            reader = ref()
            if reader is not None:
                out.append(reader.conn)
        return out

    def _snapshot(self) -> list:
        # A finished thread's callback can discard an entry while this
        # copies the set; copy again rather than fail.
        while True:
            try:
                return list(self._refs)
            except RuntimeError:
                continue
