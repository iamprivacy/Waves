"""waves.db: every store opens its file the same way, and the store list
the factory reset and the catalog read covers every file Waves keeps."""

from __future__ import annotations

import pytest

from waves import db
from waves.follows import FollowStore
from waves.library_index import LibraryIndex
from waves.mb_service import MBService
from waves.ownership import OwnershipStore

# sqlite's synchronous levels: 1 NORMAL, 2 FULL.
_NORMAL, _FULL = 1, 2


def _pragmas(conn) -> tuple[str, int, int]:
    return (
        conn.execute("PRAGMA journal_mode").fetchone()[0],
        conn.execute("PRAGMA synchronous").fetchone()[0],
        conn.execute("PRAGMA journal_size_limit").fetchone()[0],
    )


def test_connect_cache_and_state(tmp_path):
    cache = db.connect(str(tmp_path / "c.sqlite3"), kind=db.CACHE)
    state = db.connect(str(tmp_path / "s.sqlite3"), kind=db.STATE)
    try:
        assert _pragmas(cache) == ("wal", _NORMAL, db.JOURNAL_SIZE_LIMIT)
        assert _pragmas(state) == ("wal", _FULL, db.JOURNAL_SIZE_LIMIT)
    finally:
        cache.close()
        state.close()


def test_connect_refuses_an_unknown_kind(tmp_path):
    with pytest.raises(ValueError):
        db.connect(str(tmp_path / "x.sqlite3"), kind="other")


def test_connect_takes_memory():
    conn = db.connect(":memory:", kind=db.CACHE)
    try:
        conn.execute("CREATE TABLE t (x)")
    finally:
        conn.close()


def test_every_store_opens_through_connect(tmp_path):
    lib = LibraryIndex(str(tmp_path / "library-0123456789ab.sqlite3"))
    own = OwnershipStore(str(tmp_path / "ownership.sqlite3"))
    rel = FollowStore(str(tmp_path / "releases.sqlite3"))
    mb = MBService(str(tmp_path / "musicbrainz.sqlite3"), fetch=lambda url: (404, ""))
    try:
        assert _pragmas(lib._conn) == ("wal", _NORMAL, db.JOURNAL_SIZE_LIMIT)
        assert _pragmas(own._conn) == ("wal", _FULL, db.JOURNAL_SIZE_LIMIT)
        assert _pragmas(rel._conn) == ("wal", _FULL, db.JOURNAL_SIZE_LIMIT)
        with mb._db_lock:
            assert _pragmas(mb._db()) == ("wal", _NORMAL, db.JOURNAL_SIZE_LIMIT)
    finally:
        lib.close()
        own.close()
        rel.close()
        mb.close()


def test_store_names_match_the_files_the_features_open():
    from waves import flac_verify

    assert db.store("rel").file == "releases.sqlite3"
    assert db.store("own").file == "ownership.sqlite3"
    assert db.store("mb").file == "musicbrainz.sqlite3"
    assert db.store("flac").file == flac_verify.CACHE_NAME
    assert db.store("lib").pattern.match("library-0123456789ab.sqlite3")
    with pytest.raises(KeyError):
        db.store("nope")


def test_wipe_names_cover_each_store_and_its_sidecars():
    names = set(db.wipe_names())
    for s in db.STORES:
        assert s.file in names
        for suffix in s.sidecars:
            assert s.file + suffix in names
    # The sqlite stores' WAL sidecars are always there to take.
    for s in db.STORES:
        if s.sqlite:
            assert {s.file + "-wal", s.file + "-shm"} <= names


def test_library_pattern_is_anchored():
    (pat,) = db.wipe_patterns()
    assert pat.match("library-0123456789ab.sqlite3-wal")
    assert not pat.match("library-0123456789ab.sqlite3.bak")
    assert not pat.match("my-library-0123456789ab.sqlite3")


def test_only_the_account_snapshots_go_on_logout():
    assert {s.file for s in db.STORES if s.on_logout} == {"page_cache.sqlite3", "page_cache.json", "search_cache.json"}


def test_the_factory_wipe_takes_exactly_what_it_took_before_the_list_moved():
    """The reset reads its store names from waves.db now; the set it removes
    must be the one it removed when the names were written out by hand."""
    from waves.waves_ui.backend import _FACTORY_WIPE_FILES, _FACTORY_WIPE_LOG_PATTERNS

    by_hand = {"settings.json", "settings.json.bak", "settings.json.tmp"}
    by_hand |= {"token.json", "token.json.bak", "token.json.tmp", "waves.json", "waves.json.tmp"}
    for name in ("page_cache.json", "search_cache.json", "browse_tile_art.json"):
        by_hand |= {name, name + ".tmp"}
    for name in ("ownership", "library", "releases", "musicbrainz", "mbarbiter"):
        by_hand |= {f"{name}.sqlite3", f"{name}.sqlite3-wal", f"{name}.sqlite3-shm"}
    by_hand |= {"flac-check.sqlite3", "flac-check.sqlite3-journal", "flac-check.sqlite3-wal", "flac-check.sqlite3-shm"}
    by_hand |= {"app.log", "crash.log", "waves_dev.log"}
    # Added since: the page store that replaced the two JSON snapshots.
    by_hand |= {"page_cache.sqlite3", "page_cache.sqlite3-wal", "page_cache.sqlite3-shm"}
    assert set(_FACTORY_WIPE_FILES) == by_hand

    def wiped(name: str) -> bool:
        return any(p.match(name) for p in _FACTORY_WIPE_LOG_PATTERNS)

    for name in ("library-0123456789ab.sqlite3", "library-0123456789ab.sqlite3-shm", "page_cache.json.x1Y_-.tmp"):
        assert wiped(name), name
    for name in ("token.json.Ab1.tmp", "browse_tile_art.json.q.tmp", "waves.json.z.tmp"):
        assert wiped(name), name
    for name in ("page_cacheXjson.a.tmp", "notes.json.a.tmp", "library-0123456789ab.sqlite3.bak"):
        assert not wiped(name), name
