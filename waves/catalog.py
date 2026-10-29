"""One read-only view of everything Waves keeps.

Each feature keeps its own sqlite file (see ``waves.db``): the library scan
cache, the follows and releases, the downloads record, the MusicBrainz file
and the FLAC check's verdicts. The catalog opens them all at once, each
ATTACHed read-only under a short schema name, so any feature can ask a
question that spans them in one SQL statement:

    lib   the library scan cache of the current library folder
    rel   follows and the releases the sweep saw
    own   what Waves downloaded
    mb    what MusicBrainz answered
    flac  the FLAC check's verdicts

    with catalog.connect() as conn:
        conn.execute("SELECT name, mbid FROM followed_artists").fetchall()

Rules this is built on:

* Read-only, by sqlite itself (``mode=ro``): the files keep exactly one
  writer each, so a long library scan never makes a download wait on a lock.
* A missing file is left out, never created: ATTACH on a path that does not
  exist would make an empty file, and that would bring back a file a factory
  reset just removed. ``schemas`` on the connection says which ones are there.
* A connection lives for one ``with`` block. Opening one and attaching every
  file costs well under a millisecond, and nothing holds a file open between
  uses, so the reset can delete every file (Windows refuses to delete an open
  one), a change of library folder is picked up on the next open, and no long
  read keeps a writer's WAL from being checkpointed. Run the reads and leave
  the block; never keep the connection.
* Each file is consistent on its own; a statement that reads two files reads
  each as of its own last commit, which is all any display or match needs.

Waves' own normalizer is there as an SQL function, ``waves_name_key(text)``
(``artist_import.name_key``), so artist names from different stores meet
the way the app compares them. The views every connection carries:

    followed_artists  each followed artist with the MusicBrainz artist the
                      Releases check proved for it (rel + mb)
    library_artists   each artist in the library (by the album rows' artist
                      key, the key the library lookup files them under) with
                      its most used spelling, album and track counts and the
                      MusicBrainz artist the library lookup proved (lib + mb)

Pure standard library, no Qt. Logs counts and exception types only, never a
path (an attach error's message carries the full file path).
"""

from __future__ import annotations

import contextlib
import logging
import os
import pathlib
import sqlite3
from collections.abc import Callable, Iterator
from threading import Condition

from waves import db
from waves.artist_import import name_key

logger = logging.getLogger("waves.catalog")

# How long close() waits for connections still in use, so the files are free
# to delete when it returns.
CLOSE_WAIT_S = 2.0

# (name, schemas it needs, body)
_VIEWS = (
    (
        "followed_artists",
        ("rel", "mb"),
        """SELECT f.artist_id AS artist_id, f.name AS name, f.origin AS origin,
                  COALESCE(NULLIF(l.mbid, ''), f.mbid, '') AS mbid, l.how AS how
             FROM rel.followed f
             LEFT JOIN mb.artist_links l ON l.scope = 'tidal' AND l.key = f.artist_id""",
    ),
    (
        "library_artists",
        ("lib", "mb"),
        """SELECT a.key AS key,
                  (SELECT b.artist FROM lib.albums b WHERE b.pkey_artist = a.key
                    GROUP BY b.artist ORDER BY COUNT(*) DESC, b.artist LIMIT 1) AS artist,
                  a.albums AS albums, a.tracks AS tracks,
                  COALESCE(l.mbid, '') AS mbid, l.how AS how
             FROM (SELECT pkey_artist AS key, COUNT(*) AS albums, SUM(track_count) AS tracks
                     FROM lib.albums WHERE COALESCE(pkey_artist, '') != '' GROUP BY pkey_artist) a
             LEFT JOIN mb.artist_links l ON l.scope = 'library' AND l.key = a.key""",
    ),
)


class CatalogConnection:
    """A read-only connection with every store that exists attached."""

    def __init__(self, conn: sqlite3.Connection, schemas: frozenset[str]) -> None:
        self._conn = conn
        self.schemas = schemas

    def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        return self._conn.execute(sql, params)


def _ro_uri(path: str) -> str:
    """A read-only sqlite URI for ``path``. pathlib spells it for the
    platform (a Windows drive letter and backslashes included) and
    percent-encodes what a URI cannot carry, so a "?" or "#" in a folder
    name can never end the path early."""
    return pathlib.Path(os.path.abspath(path)).as_uri() + "?mode=ro"


def _name_key(value) -> str:
    return name_key(str(value or ""))


class Catalog:
    """Opens catalog connections over the files under ``config_dir``.

    ``library_file`` answers the path of the library cache currently open for
    the current library folder (one file per folder), read on every open so a
    folder change is followed without telling the catalog."""

    def __init__(self, config_dir: str, *, library_file: Callable[[], str | None] = lambda: None) -> None:
        self._dir = str(config_dir)
        self._library_file = library_file
        self._cond = Condition()
        self._in_use = 0
        self._closed = False

    def paths(self) -> dict[str, str]:
        """Each schema the catalog would attach, with its file."""
        out = {}
        for s in db.STORES:
            if not (s.sqlite and s.schema):
                continue
            if s.schema == "lib":
                try:
                    path = self._library_file()
                except Exception:
                    path = None
                if not path or path == ":memory:":
                    continue
            else:
                path = os.path.join(self._dir, s.file)
            out[s.schema] = str(path)
        return out

    @contextlib.contextmanager
    def connect(self) -> Iterator[CatalogConnection | None]:
        """A catalog connection for the length of the block, or None once
        the catalog is closed (a factory reset is under way)."""
        with self._cond:
            refused = self._closed
            if not refused:
                self._in_use += 1
        if refused:
            yield None
            return
        raw = None
        try:
            raw = sqlite3.connect(":memory:", uri=True, check_same_thread=False)
            schemas = self._attach(raw)
            raw.create_function("waves_name_key", 1, _name_key, deterministic=True)
            for name, needs, body in _VIEWS:
                if set(needs) <= schemas:
                    raw.execute(f"CREATE TEMP VIEW {name} AS {body}")
            conn = CatalogConnection(raw, frozenset(schemas))
            yield conn
        finally:
            if raw is not None:
                raw.close()
            with self._cond:
                self._in_use -= 1
                self._cond.notify_all()

    def query(self, sql: str, params: tuple = ()) -> list:
        """One statement's rows, on a connection of its own; [] once closed."""
        with self.connect() as conn:
            if conn is None:
                return []
            return conn.execute(sql, params).fetchall()

    def close(self) -> None:
        """Refuse new connections and wait (up to CLOSE_WAIT_S) for the ones
        in use, so the files can be deleted when this returns. Final."""
        with self._cond:
            self._closed = True
            if not self._cond.wait_for(lambda: self._in_use == 0, timeout=CLOSE_WAIT_S):
                logger.info("catalog closed with %d connection(s) still reading", self._in_use)

    @property
    def closed(self) -> bool:
        with self._cond:
            return self._closed

    def _attach(self, raw: sqlite3.Connection) -> set[str]:
        attached = set()
        for schema, path in self.paths().items():
            if not os.path.isfile(path):
                continue
            try:
                raw.execute(f"ATTACH DATABASE ? AS {schema}", (_ro_uri(path),))
            except sqlite3.Error as exc:
                # A locked or damaged file costs its schema, never the catalog.
                logger.info("catalog: %s left out (%s)", schema, type(exc).__name__)
                continue
            attached.add(schema)
        return attached
