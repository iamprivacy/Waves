"""Tests for the download-ownership store (tidaler/ownership.py).

Pure standard library, no PySide6, so these run in the lint venv like the other
engine-level tests. The store's whole point is that ownership is decided against
the live filesystem, so every test writes and deletes real files under tmp_path.
"""

from __future__ import annotations

import sqlite3
import threading

import pytest

from tidaler.ownership import OwnershipStore, quality_rank


def _store(tmp_path):
    return OwnershipStore(str(tmp_path / "ownership.sqlite3"))


def _track_file(tmp_path, name):
    p = tmp_path / name
    p.write_text("audio")
    return str(p)


@pytest.mark.parametrize(
    "tier,rank",
    [("LOW", 0), ("HIGH", 1), ("LOSSLESS", 2), ("HI_RES_LOSSLESS", 3), ("lossless", 2), (None, -1), ("bogus", -1)],
)
def test_quality_rank(tier, rank):
    assert quality_rank(tier) == rank


def test_record_then_owned(tmp_path):
    store = _store(tmp_path)
    path = _track_file(tmp_path, "song.flac")
    store.record("123", path, "LOSSLESS")
    info = store.ownership_of("123")
    assert info is not None
    assert info["owned"] is True
    assert info["path"] == path
    assert info["quality_tier"] == "LOSSLESS"
    assert info["quality_rank"] == 2


def test_unrecorded_is_not_owned(tmp_path):
    store = _store(tmp_path)
    assert store.ownership_of("nope") is None


def test_deleted_file_self_heals(tmp_path):
    store = _store(tmp_path)
    path = _track_file(tmp_path, "song.flac")
    store.record("123", path, "LOSSLESS")
    assert store.ownership_of("123") is not None
    # The user deletes the file: ownership must fall back to "not owned" without
    # any history clearing, so a re-download is offered.
    (tmp_path / "song.flac").unlink()
    assert store.ownership_of("123") is None
    # Re-create it: the same recorded row makes it owned again, no re-record.
    _track_file(tmp_path, "song.flac")
    assert store.ownership_of("123") is not None


def test_best_surviving_quality_wins(tmp_path):
    store = _store(tmp_path)
    low = _track_file(tmp_path, "song.m4a")
    hi = _track_file(tmp_path, "song.flac")
    store.record("123", low, "HIGH")
    store.record("123", hi, "HI_RES_LOSSLESS")
    info = store.ownership_of("123")
    assert info["path"] == hi
    assert info["quality_tier"] == "HI_RES_LOSSLESS"


def test_falls_back_to_lower_quality_when_better_copy_is_gone(tmp_path):
    store = _store(tmp_path)
    low = _track_file(tmp_path, "song.m4a")
    hi = _track_file(tmp_path, "song.flac")
    store.record("123", low, "HIGH")
    store.record("123", hi, "HI_RES_LOSSLESS")
    (tmp_path / "song.flac").unlink()  # lose the hi-res copy
    info = store.ownership_of("123")
    assert info["path"] == low
    assert info["quality_tier"] == "HIGH"


def test_upsert_same_path_updates_in_place(tmp_path):
    store = _store(tmp_path)
    path = _track_file(tmp_path, "song.flac")
    store.record("123", path, "HIGH")
    store.record("123", path, "HI_RES_LOSSLESS")  # re-download to the same path
    con = sqlite3.connect(str(tmp_path / "ownership.sqlite3"))
    (count,) = con.execute("SELECT COUNT(*) FROM downloads WHERE track_id='123'").fetchone()
    con.close()
    assert count == 1, "same (track_id, path) must update, not append"
    assert store.ownership_of("123")["quality_tier"] == "HI_RES_LOSSLESS"


def test_different_paths_append_rows(tmp_path):
    store = _store(tmp_path)
    a = _track_file(tmp_path, "a.flac")
    b = _track_file(tmp_path, "b.flac")
    store.record("123", a, "LOSSLESS")
    store.record("123", b, "LOSSLESS")
    con = sqlite3.connect(str(tmp_path / "ownership.sqlite3"))
    (count,) = con.execute("SELECT COUNT(*) FROM downloads WHERE track_id='123'").fetchone()
    con.close()
    assert count == 2


def test_user_id_scoping_is_optional(tmp_path):
    store = _store(tmp_path)
    path = _track_file(tmp_path, "song.flac")
    store.record("123", path, "LOSSLESS", user_id="userA")
    # Default (no filter) sees it; a matching user sees it; a different user does not.
    assert store.ownership_of("123") is not None
    assert store.ownership_of("123", user_id="userA") is not None
    assert store.ownership_of("123", user_id="userB") is None


def test_reopening_existing_db_is_safe(tmp_path):
    path = _track_file(tmp_path, "song.flac")
    store = _store(tmp_path)
    store.record("123", path, "LOSSLESS")
    store.close()
    # Re-running CREATE TABLE / ensure-columns against the populated DB must be a
    # no-op, and the data must survive.
    store2 = _store(tmp_path)
    assert store2.ownership_of("123") is not None
    store2.close()


def test_optional_metadata_columns_round_trip(tmp_path):
    store = _store(tmp_path)
    path = _track_file(tmp_path, "song.flac")
    store.record("123", path, "HI_RES_LOSSLESS", audio_mode="STEREO", bit_depth=24, sample_rate=96000, codecs="FLAC")
    info = store.ownership_of("123")
    assert info["audio_mode"] == "STEREO"
    assert info["bit_depth"] == 24
    assert info["sample_rate"] == 96000
    assert info["codecs"] == "FLAC"


def test_concurrent_records_all_land(tmp_path):
    store = _store(tmp_path)
    files = [_track_file(tmp_path, f"song{i}.flac") for i in range(40)]

    def worker(i):
        store.record(str(i), files[i], "LOSSLESS")

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(40)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    for i in range(40):
        assert store.ownership_of(str(i)) is not None


# --------------------------------------------------------------------------- #
# Collection membership: which track ids make up an album/playlist/mix,
# learned locally so a later "is this fully owned" question elsewhere in the
# app never needs to re-fetch from TIDAL.
# --------------------------------------------------------------------------- #
def test_unknown_collection_is_none_not_empty(tmp_path):
    store = _store(tmp_path)
    assert store.members_of("album1") is None


def test_replace_then_members_of(tmp_path):
    store = _store(tmp_path)
    store.record_members_replace("album1", ["10", "11", "12"])
    assert sorted(store.members_of("album1")) == ["10", "11", "12"]


def test_replace_overwrites_previous_membership(tmp_path):
    store = _store(tmp_path)
    store.record_members_replace("pl1", ["1", "2", "3"])
    store.record_members_replace("pl1", ["1", "2"])  # a track was removed from the playlist
    assert sorted(store.members_of("pl1")) == ["1", "2"]


def test_add_is_additive_not_a_replace(tmp_path):
    store = _store(tmp_path)
    store.record_members_add("album1", ["1"])
    store.record_members_add("album1", ["2"])
    assert sorted(store.members_of("album1")) == ["1", "2"]


def test_add_ignores_duplicates(tmp_path):
    store = _store(tmp_path)
    store.record_members_add("album1", ["1", "1", "2"])
    store.record_members_add("album1", ["2"])
    assert sorted(store.members_of("album1")) == ["1", "2"]


def test_empty_track_list_replace_reads_back_as_unknown(tmp_path):
    # An edge case (a genuinely empty collection) collapses to "unknown" rather
    # than a real distinct state; documented as an accepted, low-consequence
    # simplification (an empty collection has nothing to badge anyway).
    store = _store(tmp_path)
    store.record_members_replace("empty1", [])
    assert store.members_of("empty1") is None


def test_membership_is_scoped_per_collection(tmp_path):
    store = _store(tmp_path)
    store.record_members_replace("album1", ["1", "2"])
    store.record_members_replace("album2", ["3", "4"])
    assert sorted(store.members_of("album1")) == ["1", "2"]
    assert sorted(store.members_of("album2")) == ["3", "4"]


def test_add_falsy_ids_are_skipped(tmp_path):
    store = _store(tmp_path)
    store.record_members_add("album1", ["1", "", None])
    assert store.members_of("album1") == ["1"]
