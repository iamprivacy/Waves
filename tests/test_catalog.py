"""waves.catalog: one read-only view over every store file."""

from __future__ import annotations

import os
import sqlite3
import threading

import pytest

from waves import db
from waves.catalog import Catalog
from waves.follows import FollowStore
from waves.library_index import LibraryIndex
from waves.mb_service import MBService
from waves.ownership import OwnershipStore


@pytest.fixture
def config(tmp_path):
    return tmp_path / "config"


def _stores(config):
    config.mkdir(exist_ok=True)
    lib_path = str(config / "library-0123456789ab.sqlite3")
    lib = LibraryIndex(lib_path)
    rel = FollowStore(str(config / "releases.sqlite3"))
    own = OwnershipStore(str(config / "ownership.sqlite3"))
    mb = MBService(str(config / "musicbrainz.sqlite3"), fetch=lambda url: (404, ""))
    return lib, rel, own, mb


def _close(*stores):
    for s in stores:
        s.close()


def test_every_existing_store_is_attached_and_nothing_is_created(config):
    config.mkdir()
    cat = Catalog(str(config), library_file=lambda: str(config / "library-0123456789ab.sqlite3"))
    with cat.connect() as conn:
        assert conn.schemas == frozenset()
    assert os.listdir(config) == [], "a missing store must never be created"

    lib, rel, own, mb = _stores(config)
    mb.link("tidal", "42", "Someone", "mbid-1", "titles")
    try:
        with cat.connect() as conn:
            assert conn.schemas == {"lib", "rel", "own", "mb"}
            assert conn.execute("SELECT COUNT(*) FROM own.downloads").fetchone() == (0,)
    finally:
        _close(lib, rel, own, mb)
    assert "flac-check.sqlite3" not in os.listdir(config)


def test_the_catalog_never_writes(config):
    lib, rel, own, mb = _stores(config)
    cat = Catalog(str(config), library_file=lambda: lib._path)
    try:
        with cat.connect() as conn, pytest.raises(sqlite3.OperationalError):
            conn.execute("DELETE FROM rel.followed")
    finally:
        _close(lib, rel, own, mb)


def test_followed_artists_joins_the_releases_check_proof(config):
    lib, rel, own, mb = _stores(config)
    try:
        rel.follow("42", "Someone", "")
        rel.follow("43", "Nobody", "")
        mb.link("tidal", "42", "Someone", "mbid-1", "titles")
        cat = Catalog(str(config), library_file=lambda: lib._path)
        rows = cat.query("SELECT artist_id, name, mbid, how FROM followed_artists ORDER BY artist_id")
        assert rows == [("42", "Someone", "mbid-1", "titles"), ("43", "Nobody", "", None)]
    finally:
        _close(lib, rel, own, mb)


def test_library_artists_joins_the_library_lookup(config):
    lib, rel, own, mb = _stores(config)
    try:
        with lib._lock:
            for folder, artist, key, tracks in (
                ("/m/a1", "Björk", "bjork", 10),
                ("/m/a2", "Bjork", "bjork", 12),
                ("/m/a3", "Björk", "bjork", 9),
                ("/m/b1", "Other", "other", 4),
            ):
                lib._conn.execute(
                    "INSERT INTO albums (folder_path, artist, track_count, pkey_artist) VALUES (?, ?, ?, ?)",
                    (folder, artist, tracks, key),
                )
            lib._conn.commit()
        mb.link("library", "bjork", "Björk", "mbid-b", "file_tag")
        cat = Catalog(str(config), library_file=lambda: lib._path)
        rows = cat.query("SELECT key, artist, albums, tracks, mbid FROM library_artists ORDER BY key")
        assert rows == [("bjork", "Björk", 3, 31, "mbid-b"), ("other", "Other", 1, 4, "")]
        assert cat.query("SELECT waves_name_key(?)", ("Guns N’ Roses",)) == cat.query(
            "SELECT waves_name_key(?)", ("guns n' roses",)
        )
    finally:
        _close(lib, rel, own, mb)


def test_a_view_waits_for_both_its_stores(config):
    config.mkdir()
    rel = FollowStore(str(config / "releases.sqlite3"))
    try:
        with Catalog(str(config)).connect() as conn:
            assert conn.schemas == {"rel"}
            with pytest.raises(sqlite3.OperationalError):
                conn.execute("SELECT * FROM followed_artists")
    finally:
        rel.close()


def test_a_library_folder_change_is_followed_on_the_next_open(config):
    config.mkdir()
    first = LibraryIndex(str(config / "library-aaaaaaaaaaaa.sqlite3"))
    second = LibraryIndex(str(config / "library-bbbbbbbbbbbb.sqlite3"))
    current = [first]
    try:
        with first._lock:
            first._conn.execute("INSERT INTO albums (folder_path, artist) VALUES ('/a', 'A')")
            first._conn.commit()
        cat = Catalog(str(config), library_file=lambda: current[0]._path)
        assert cat.query("SELECT COUNT(*) FROM lib.albums") == [(1,)]
        current[0] = second
        assert cat.query("SELECT COUNT(*) FROM lib.albums") == [(0,)]
    finally:
        _close(first, second)


def test_an_in_memory_library_is_left_out(config):
    config.mkdir()
    cat = Catalog(str(config), library_file=lambda: ":memory:")
    assert "lib" not in cat.paths()


def test_a_damaged_store_costs_only_its_schema(config):
    config.mkdir()
    rel = FollowStore(str(config / "releases.sqlite3"))
    (config / "ownership.sqlite3").write_bytes(b"not a database at all" * 100)
    try:
        with Catalog(str(config)).connect() as conn:
            assert "rel" in conn.schemas
            # sqlite reads the header lazily: the damaged file either fails to
            # attach or fails its first read, never the others.
            if "own" in conn.schemas:
                with pytest.raises(sqlite3.DatabaseError):
                    conn.execute("SELECT * FROM own.sqlite_master").fetchall()
            assert conn.execute("SELECT COUNT(*) FROM rel.followed").fetchone() == (0,)
    finally:
        rel.close()


def test_close_refuses_new_connections_and_waits_for_open_ones(config):
    config.mkdir()
    rel = FollowStore(str(config / "releases.sqlite3"))
    cat = Catalog(str(config))
    entered, release = threading.Event(), threading.Event()
    finished = []

    def reader():
        with cat.connect() as conn:
            entered.set()
            release.wait(5)
            finished.append(conn.execute("SELECT COUNT(*) FROM rel.followed").fetchone())

    t = threading.Thread(target=reader)
    t.start()
    try:
        assert entered.wait(5)
        closer = threading.Thread(target=cat.close)
        closer.start()
        closer.join(0.2)
        assert closer.is_alive(), "close waits for the reader in use"
        release.set()
        closer.join(5)
        assert not closer.is_alive()
        assert finished == [(0,)]
        assert cat.closed
        assert cat.query("SELECT 1") == []
        with cat.connect() as conn:
            assert conn is None
    finally:
        release.set()
        t.join(5)
        rel.close()
    # Nothing holds the file once close returns: it deletes (Windows refuses
    # an open file).
    os.remove(config / "releases.sqlite3")


def test_every_catalog_schema_is_a_registered_store():
    schemas = {s.schema for s in db.STORES if s.schema}
    assert schemas == {"lib", "rel", "own", "mb", "flac"}
    cat = Catalog("/nowhere", library_file=lambda: "/nowhere/library-0123456789ab.sqlite3")
    assert set(cat.paths()) == schemas


def test_the_reset_closes_the_catalog_before_any_store_or_file():
    import inspect

    from waves.waves_ui.backend import WavesBridge

    body = inspect.getsource(WavesBridge.factoryReset)
    closed = body.index("cat.close()")
    assert closed < body.index("self._ownership.close()")
    assert closed < body.index("for name in _FACTORY_WIPE_FILES")


def test_a_folder_name_with_uri_characters_still_attaches(tmp_path):
    from waves.catalog import _ro_uri

    config = tmp_path / "odd ?name #1 %20"
    config.mkdir()
    rel = FollowStore(str(config / "releases.sqlite3"))
    try:
        rel.follow("42", "Someone", "")
        with Catalog(str(config)).connect() as conn:
            assert conn.schemas == {"rel"}
            assert conn.execute("SELECT name FROM rel.followed").fetchall() == [("Someone",)]
    finally:
        rel.close()
    assert _ro_uri(str(config / "x.sqlite3")).startswith("file:///") and "?mode=ro" in _ro_uri("x")
