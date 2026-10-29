"""waves.page_store: the account's page snapshot, one sqlite row per page."""

from __future__ import annotations

import json
import os
import sqlite3
from threading import Lock
from types import SimpleNamespace

import pytest

from waves.page_store import PageStore
from waves.waves_ui import backend
from waves.waves_ui.backend import _PAGE_CACHE_VERSION, WavesBridge

V = _PAGE_CACHE_VERSION


def _rows(*keys):
    return [(k, json.dumps({"id": k})) for k in keys]


def test_round_trip_keeps_each_kind_in_its_order(tmp_path):
    store = PageStore(str(tmp_path / "p.sqlite3"))
    store.save(V, "u", {"artists": _rows("3", "1", "2"), "home": [("", "[1]")]})
    got = store.load(V, "u", ("artists", "home", "searches"))
    assert [k for k, _ in got["artists"]] == ["3", "1", "2"], "insertion order is eviction order"
    assert got["home"] == [("", "[1]")]
    assert got["searches"] == []


def test_another_account_or_layout_reads_nothing_and_a_save_replaces_it(tmp_path):
    store = PageStore(str(tmp_path / "p.sqlite3"))
    store.save(V, "u", {"artists": _rows("1")})
    assert store.load(V, "other", ("artists",)) is None
    assert store.load(V - 1, "u", ("artists",)) is None
    store.save(V, "other", {"home": [("", "[]")]})
    assert store.load(V, "other", ("artists",)) == {"artists": []}, "the old account's pages went"


def test_only_changed_rows_are_written(tmp_path):
    path = str(tmp_path / "p.sqlite3")
    store = PageStore(path)
    assert store.save(V, "u", {"artists": _rows("1", "2", "3")}) == 3
    assert store.save(V, "u", {"artists": _rows("1", "2", "3")}) == 0
    changed = [*_rows("1", "2"), ("3", json.dumps({"id": "3", "new": True}))]
    assert store.save(V, "u", {"artists": changed}) == 1
    # A row that dropped out is deleted; a reopened store knows what is held.
    # Both moved up a position: only their places are rewritten, no body.
    assert PageStore(path).save(V, "u", {"artists": changed[1:]}) == 0
    assert [k for k, _ in PageStore(path).load(V, "u", ("artists",))["artists"]] == ["2", "3"]


def test_a_damaged_file_starts_over_instead_of_failing_every_save(tmp_path):
    path = tmp_path / "p.sqlite3"
    path.write_bytes(b"not a database at all" * 64)
    store = PageStore(str(path))
    assert store.save(V, "u", {"artists": _rows("1")}) == 1
    assert [k for k, _ in store.load(V, "u", ("artists",))["artists"]] == ["1"]


def test_another_account_never_inherits_the_file(tmp_path):
    path = tmp_path / "p.sqlite3"
    store = PageStore(str(path))
    store.save(V, "alice", {"home": [("", json.dumps({"mine": "ALICEPERSONAL" * 50}))]})
    store.save(V, "bob", {"home": [("", "[]")]})
    store.close()
    blob = b"".join(p.read_bytes() for p in tmp_path.iterdir() if p.name.startswith("p.sqlite3"))
    assert b"ALICEPERSONAL" not in blob


def test_a_kind_left_out_of_a_save_is_kept(tmp_path):
    store = PageStore(str(tmp_path / "p.sqlite3"))
    store.save(V, "u", {"artists": _rows("1"), "searches": _rows("s")})
    store.save(V, "u", {"artists": _rows("2")})
    got = store.load(V, "u", ("artists", "searches"))
    assert [k for k, _ in got["artists"]] == ["2"] and [k for k, _ in got["searches"]] == ["s"]


def test_wipe_removes_the_file_and_the_next_save_starts_over(tmp_path):
    path = tmp_path / "p.sqlite3"
    store = PageStore(str(path))
    store.save(V, "u", {"artists": _rows("1")})
    store.wipe()
    assert not any(n.startswith("p.sqlite3") for n in os.listdir(tmp_path))
    assert store.save(V, "u", {"artists": _rows("2")}) == 1
    assert [k for k, _ in store.load(V, "u", ("artists",))["artists"]] == ["2"]


def test_close_is_final(tmp_path):
    path = tmp_path / "p.sqlite3"
    store = PageStore(str(path))
    store.save(V, "u", {"artists": _rows("1")})
    store.close()
    assert store.save(V, "u", {"artists": _rows("2")}) == 0
    assert store.load(V, "u", ("artists",)) is None
    os.remove(path)  # nothing holds it open
    assert not path.exists(), "a closed store never recreates its file"


def test_the_file_is_a_cache_tuned_store(tmp_path):
    store = PageStore(str(tmp_path / "p.sqlite3"))
    store.save(V, "u", {})
    with store._lock:
        conn = store._conn
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert conn.execute("PRAGMA synchronous").fetchone()[0] == 1


# ---- the bridge -------------------------------------------------------------


def _bridge(tmp_path, user="42"):
    b = WavesBridge.__new__(WavesBridge)
    b._logged_in = True
    b._factory_reset = False
    b._lib_cache = {}
    b._lib_sort = {}
    b._browse_root_cache = None
    b._browse_pages = {}
    b._artist_cache = {}
    b._search_cache = {}
    b._home_cache = None
    b._page_cache_path = str(tmp_path / "page_cache.json")
    b._search_cache_path = str(tmp_path / "search_cache.json")
    b._page_store = PageStore(str(tmp_path / "page_cache.sqlite3"))
    b._page_cache_lock = Lock()
    b.tidal = SimpleNamespace(session=SimpleNamespace(user=SimpleNamespace(id=user)))
    return b


def test_an_earlier_builds_json_snapshot_is_imported_once_and_removed(tmp_path):
    page = {"id": "7", "albums": []}
    (tmp_path / "page_cache.json").write_text(json.dumps({"version": V, "user": "42", "artists": {"7": page}}))
    search = {"artists": [], "albums": []}
    (tmp_path / "search_cache.json").write_text(json.dumps({"version": V, "user": "42", "searches": {"q": search}}))
    b = _bridge(tmp_path)
    b._load_page_cache()
    assert b._artist_cache == {"7": page}
    assert not (tmp_path / "page_cache.json").exists()
    b._load_search_cache()
    assert b._search_cache["q"] == (backend._STALE_STAMP, search)
    assert not (tmp_path / "search_cache.json").exists()
    # Next launch: straight from the store.
    again = _bridge(tmp_path)
    again._load_page_cache()
    assert again._artist_cache == {"7": page}


def test_another_accounts_json_snapshot_is_dropped_not_imported(tmp_path):
    (tmp_path / "page_cache.json").write_text(json.dumps({"version": V, "user": "9", "artists": {"7": {}}}))
    b = _bridge(tmp_path)
    b._load_page_cache()
    assert b._artist_cache == {}
    assert not (tmp_path / "page_cache.json").exists()


def test_a_damaged_row_costs_the_restore_never_the_launch(tmp_path):
    b = _bridge(tmp_path)
    b._artist_cache = {"7": {"id": "7"}}
    b._save_page_cache()
    conn = sqlite3.connect(tmp_path / "page_cache.sqlite3")
    conn.execute("UPDATE pages SET body = '{not json' WHERE kind = 'artists'")
    conn.commit()
    conn.close()
    fresh = _bridge(tmp_path)
    fresh._load_page_cache()  # must not raise
    assert fresh._artist_cache == {}


def test_a_wipe_takes_a_saved_snapshot_with_its_sidecars(tmp_path):
    b = _bridge(tmp_path)
    b._artist_cache = {"7": {"id": "7"}}
    b._save_page_cache()
    assert (tmp_path / "page_cache.sqlite3").exists()
    with b._page_cache_lock:
        b._page_store.wipe()
    assert not any(n.startswith("page_cache") for n in os.listdir(tmp_path))


def test_the_reset_closes_the_store_before_the_wipe():
    import inspect

    body = inspect.getsource(WavesBridge.factoryReset)
    assert body.index("store.close()") < body.index("for name in _FACTORY_WIPE_FILES")


def test_a_disk_error_on_open_never_removes_the_file(tmp_path, monkeypatch):
    path = tmp_path / "p.sqlite3"
    PageStore(str(path)).save(V, "u", {"artists": _rows("1")})
    store = PageStore(str(path))

    def refused(self):
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(PageStore, "_open", refused)
    with pytest.raises(sqlite3.OperationalError):
        store.save(V, "u", {"artists": _rows("2")})
    monkeypatch.undo()
    assert [k for k, _ in PageStore(str(path)).load(V, "u", ("artists",))["artists"]] == ["1"]


def test_a_failed_reopen_for_another_account_raises_its_own_error(tmp_path, monkeypatch):
    path = tmp_path / "p.sqlite3"
    store = PageStore(str(path))
    store.save(V, "alice", {"artists": _rows("1")})

    def refused(self):
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(PageStore, "_open", refused)
    with pytest.raises(sqlite3.OperationalError, match="disk I/O"):
        store.save(V, "bob", {"artists": _rows("2")})
