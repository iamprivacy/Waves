"""The account's page snapshot and last searches, one row per page.

The app keeps the pages it painted (Browse, artist pages, the library's first
pages, Home) and the newest searches so the next launch paints at once and
revalidates in the background (see the bridge's ``_save_page_cache``). This
file holds them in sqlite, one row per page, in the order the app's caches
evict them: a save writes only the pages whose content changed instead of
rewriting the whole snapshot each time, and a load reads only the kinds it
asks for (the landing at launch, the searches after the reveal).

The snapshot belongs to one account: ``meta`` carries the snapshot layout
version and the account it was written for, and a load for any other
answers nothing. Signing out removes the file outright (``wipe``), the same
promise the JSON files made; a deleted row could otherwise linger in the
file's free pages.

Thread-safe: one connection, opened on first use, under a lock. ``close``
is final until ``wipe`` or the next save reopens it (a factory reset closes
it so the wipe can take the file). Pure standard library plus waves.db.
"""

from __future__ import annotations

import contextlib
import logging
import os
import sqlite3
from threading import Lock

from waves import db

logger = logging.getLogger("waves.pagestore")

_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)",
    """CREATE TABLE IF NOT EXISTS pages (
           kind TEXT NOT NULL, key TEXT NOT NULL, pos INTEGER NOT NULL, body TEXT NOT NULL,
           PRIMARY KEY (kind, key))""",
)


class PageStore:
    """See the module docstring."""

    def __init__(self, path: str) -> None:
        self._path = str(path)
        self._lock = Lock()
        self._conn: sqlite3.Connection | None = None
        self._closed = False
        # What the file holds, per (kind, key): (pos, hash of body), so a
        # save skips the rows that did not change. Filled on open.
        self._held: dict[tuple[str, str], tuple[int, int]] = {}

    @property
    def path(self) -> str:
        return self._path

    def _db(self) -> sqlite3.Connection | None:
        """The connection (caller holds the lock), opened on first use."""
        if self._closed:
            return None
        if self._conn is None:
            parent = os.path.dirname(self._path)
            if parent:
                os.makedirs(parent, exist_ok=True)
            try:
                self._conn = self._open()
            except sqlite3.DatabaseError as exc:
                # Only a file that is not (or no longer) a database: a locked,
                # unreadable or full disk (an OperationalError) leaves it be.
                if type(exc) is not sqlite3.DatabaseError:
                    raise
                # A damaged file (a disk fault, a half copy): this is a cache
                # the next save refills, so it starts over rather than fail
                # every save and load until a sign-out.
                logger.info("page snapshot unreadable, starting a new one")
                self._remove_files()
                self._conn = self._open()
        return self._conn

    def _open(self) -> sqlite3.Connection:
        conn = None
        try:
            conn = db.connect(self._path, kind=db.CACHE)
            for stmt in _SCHEMA:
                conn.execute(stmt)
            conn.commit()
            self._held = {
                (kind, key): (pos, hash(body))
                for kind, key, pos, body in conn.execute("SELECT kind, key, pos, body FROM pages")
            }
        except sqlite3.Error:
            if conn is not None:
                conn.close()
            raise
        return conn

    def _remove_files(self) -> None:
        for suffix in ("", "-wal", "-shm", "-journal"):
            with contextlib.suppress(OSError):
                os.remove(self._path + suffix)

    def _owner(self, conn: sqlite3.Connection) -> tuple[str | None, str | None]:
        got = dict(conn.execute("SELECT key, value FROM meta WHERE key IN ('version', 'user')").fetchall())
        return got.get("version"), got.get("user")

    def save(self, version: int, user: str, sections: dict[str, list[tuple[str, str]]]) -> int:
        """Store each named kind whole, as ordered (key, serialized body)
        pairs: rows of that kind not listed go, rows whose position and body
        are unchanged are left alone, and one that only moved has just its
        position rewritten. A snapshot for another account or layout
        replaces the file. Returns how many rows' bodies were written."""
        written = 0
        with self._lock:
            conn = self._db()
            if conn is None:
                return 0
            try:
                if self._owner(conn) != (str(version), str(user)):
                    if self._held:
                        # Another account's (or layout's) pages: the file goes
                        # whole, so none of them lingers in its free pages.
                        self._reset_conn()
                        self._remove_files()
                        conn = self._db()
                        if conn is None:
                            return 0
                    conn.execute(
                        "INSERT OR REPLACE INTO meta (key, value) VALUES ('version', ?), ('user', ?)",
                        (str(version), str(user)),
                    )
                for kind, rows in sections.items():
                    written += self._save_kind(conn, kind, rows)
                conn.commit()
            except sqlite3.Error:
                # The connection may already be closed (the file was being
                # replaced): the error that got here is the one to raise.
                with contextlib.suppress(sqlite3.Error):
                    conn.rollback()
                # The held map may no longer match the file: re-read it next open.
                self._reset_conn()
                raise
        return written

    def _save_kind(self, conn: sqlite3.Connection, kind: str, rows: list[tuple[str, str]]) -> int:
        """(caller holds the lock, in the save's transaction) One kind
        brought to ``rows``; returns how many bodies were written."""
        written = 0
        keep = set()
        for pos, (key, body) in enumerate(rows):
            key = str(key)
            keep.add(key)
            mark = (pos, hash(body))
            held = self._held.get((kind, key))
            if held == mark:
                continue
            if held is not None and held[1] == mark[1]:
                # Only its place moved (an eviction renumbers the rest): the
                # body stays as written.
                conn.execute("UPDATE pages SET pos = ? WHERE kind = ? AND key = ?", (pos, kind, key))
                self._held[(kind, key)] = mark
                continue
            conn.execute(
                "INSERT OR REPLACE INTO pages (kind, key, pos, body) VALUES (?, ?, ?, ?)",
                (kind, key, pos, body),
            )
            self._held[(kind, key)] = mark
            written += 1
        gone = [k for (kd, k) in self._held if kd == kind and k not in keep]
        for key in gone:
            conn.execute("DELETE FROM pages WHERE kind = ? AND key = ?", (kind, key))
            del self._held[(kind, key)]
        return written

    def load(self, version: int, user: str, kinds: tuple[str, ...]) -> dict[str, list[tuple[str, str]]] | None:
        """The snapshot's rows for ``kinds``, each kind in its saved order,
        or None when there is no snapshot for this account and layout."""
        with self._lock:
            conn = self._db()
            if conn is None:
                return None
            if self._owner(conn) != (str(version), str(user)):
                return None
            out: dict[str, list[tuple[str, str]]] = {kind: [] for kind in kinds}
            marks = ",".join("?" for _ in kinds)
            sql = f"SELECT kind, key, body FROM pages WHERE kind IN ({marks}) ORDER BY kind, pos"  # noqa: S608
            for kind, key, body in conn.execute(sql, tuple(kinds)):
                out[kind].append((key, body))
            return out

    def wipe(self) -> None:
        """Remove the file and its sidecars (sign-out). The next save starts
        a new one."""
        with self._lock:
            self._reset_conn()
            self._remove_files()

    def close(self) -> None:
        """Close for good (factory reset): saves and loads answer nothing."""
        with self._lock:
            self._closed = True
            self._reset_conn()

    def _reset_conn(self) -> None:
        if self._conn is not None:
            with contextlib.suppress(sqlite3.Error):
                self._conn.close()
            self._conn = None
        self._held = {}
