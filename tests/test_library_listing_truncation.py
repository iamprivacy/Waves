"""A directory listing the scanner cannot trust, and the probe by name that
covers what such a listing leaves out (waves/library_index.py).

THE BUG THIS FENCES OFF
-----------------------
A network share whose directory paging is broken hands the OS the same first
page over and over: one library root listed as 10000 entries of only 1000
distinct names. Every artist past that page was invisible to the walk (never
indexed, every badge "not in library", duplicates downloaded) while a direct
``os.stat(root/Artist)`` found it at once. Worse, a fresh listing used to be
taken at its word: every stored child it did not name was condemned and
pruned that very scan, so anything found by other means would have been
deleted again on the next walk.

Pinned here: repeats are dropped (one walk per folder, one track row per
file); a listing that repeats names, or leaves out a child a stat then finds,
is flagged untrusted and never condemns; a genuine deletion is still pruned
in one scan; probe_folders indexes a hidden artist by name, the result
survives later scans (incremental and forced alike), and the probe refuses
to write into a cache that was never scanned for that root.
"""

from __future__ import annotations

import os
import shutil
import time

import waves.library_index as li
from waves.library_index import SCAN_OK, LibraryIndex


def _mk(base, rel, files):
    d = os.path.join(base, *rel.split("/"))
    os.makedirs(d, exist_ok=True)
    for name in files:
        open(os.path.join(d, name), "w").close()
    return d


def _reader(tagmap, counter=None):
    def read(path):
        if counter is not None:
            counter.append(path)
        return tagmap.get(os.path.dirname(path))

    return read


def _index(tmp_path, tagmap, counter=None):
    return LibraryIndex(str(tmp_path / "library.sqlite3"), read_tags=_reader(tagmap, counter))


class _CM:
    """A scandir stand-in: the entries are handed over as-is."""

    def __init__(self, entries):
        self._entries = entries

    def __enter__(self):
        return iter(self._entries)

    def __exit__(self, *a):
        return False


_TICK = [0]


def _bump(path):
    """Move a folder's mtime forward so the next walk re-lists it."""
    _TICK[0] += 1000
    future = time.time() + _TICK[0]
    os.utime(path, (future, future))


def _fake_listing(monkeypatch, shape):
    """Replace os.scandir for the folders in ``shape`` ({dir: transform}) with
    the transform applied to the real entries; every other folder lists for
    real. A transform receives the real DirEntry list and returns the list the
    OS will be believed to have returned."""
    real = os.scandir
    targets = {os.path.abspath(d): fn for d, fn in shape.items()}

    def fake(path=".", *a, **k):
        fn = targets.get(os.path.abspath(path))
        if fn is None:
            return real(path, *a, **k)
        with real(path, *a, **k) as it:
            entries = list(it)
        return _CM(fn(entries))

    monkeypatch.setattr(li.os, "scandir", fake)
    return real


def _two_artists(tmp_path):
    lib = _mk(tmp_path, "lib", [])
    a = _mk(tmp_path, "lib/A/[2020] Alpha", ["01.flac", "02.flac"])
    b = _mk(tmp_path, "lib/B/[2021] Beta", ["01.flac"])
    tags = {
        a: {"album": "Alpha", "artist": "A", "date": "2020"},
        b: {"album": "Beta", "artist": "B", "date": "2021"},
    }
    return lib, a, b, tags


def _rows(idx, sql, *args):
    with idx._lock:
        return idx._conn.execute(sql, args).fetchall()


def _gen(idx):
    return int(_rows(idx, "SELECT value FROM meta WHERE key = 'scan_gen'")[0][0])


# ---- repeats -------------------------------------------------------------------


def test_duplicated_listing_is_deduped(tmp_path, monkeypatch):
    lib, a, b, tags = _two_artists(tmp_path)
    reads: list[str] = []
    idx = _index(tmp_path, tags, reads)
    # The root AND an album folder both page wrongly: names three times over.
    _fake_listing(monkeypatch, {lib: lambda e: e * 3, a: lambda e: e * 3})
    assert idx.refresh(lib) == 2
    # One row per album, one track row per FILE, the raw count as on disk.
    assert _rows(idx, "SELECT raw_count, track_count FROM albums WHERE folder_path = ?", a) == [(2, 2)]
    assert _rows(idx, "SELECT COUNT(*) FROM tracks WHERE folder_path = ?", a) == [(2,)]
    assert _rows(idx, "SELECT COUNT(*) FROM tracks WHERE folder_path = ?", b) == [(1,)]
    # ...and each file read once, not three times.
    assert sorted(os.path.basename(p) for p in reads if p.startswith(a)) == ["01.flac", "02.flac"]
    # The same album is walked once: one dirs row per real folder.
    assert _rows(idx, "SELECT COUNT(*) FROM dirs")[0][0] == 5  # root, A, Alpha, B, Beta


def test_duplicated_listing_marks_the_dir_unreliable(tmp_path, monkeypatch):
    lib, a, b, tags = _two_artists(tmp_path)
    idx = _index(tmp_path, tags)
    real = _fake_listing(monkeypatch, {lib: lambda e: e * 2})
    assert idx.refresh(lib) == 2
    assert idx.unreliable_dirs() == [lib]
    assert idx.last_scan_partial is True
    assert idx.last_scan_status == SCAN_OK  # the root WAS listed; not a SCAN_* failure
    # A later clean listing of the same folder clears the flag.
    monkeypatch.setattr(li.os, "scandir", real)
    _bump(lib)
    assert idx.refresh(lib) == 2
    assert idx.unreliable_dirs() == []
    assert idx.last_scan_partial is False


def test_a_cache_from_before_listings_were_judged_relists_its_root_once(tmp_path, monkeypatch):
    # An upgrade must not leave a broken root unjudged forever: a warm scan
    # reuses every unchanged listing, so a root whose mtime never moves would
    # never be listed again. The cache's first open without the marker makes
    # the next scan re-list the root once. Simulated by scanning with a CLEAN
    # root listing under the old code's conditions (no marker, no verdict),
    # then switching the share to a broken one without touching the root.
    lib, a, b, tags = _two_artists(tmp_path)
    idx = _index(tmp_path, tags)
    idx.refresh(lib)
    with idx._lock:
        idx._conn.execute("DELETE FROM meta WHERE key = ?", (li._ROOT_JUDGED_KEY,))
        idx._conn.commit()
    idx.close()
    _fake_listing(monkeypatch, {lib: lambda e: e * 2})
    idx = _index(tmp_path, tags)
    assert idx.last_scan_partial is False  # the old cache carried no verdict
    assert idx.refresh(lib) == 2  # root mtime unchanged, yet it is re-listed...
    assert idx.unreliable_dirs() == [lib]  # ...and judged
    assert idx.last_scan_partial is True
    # Once only: a clean reopen does not re-list a root the marker covers.
    idx.close()
    idx = _index(tmp_path, tags)
    with idx._lock:
        assert idx._conn.execute("SELECT mtime FROM dirs WHERE path = ?", (lib,)).fetchone()[0] != 0


def test_a_fresh_cache_is_unaffected_by_the_marker(tmp_path):
    lib, a, b, tags = _two_artists(tmp_path)
    idx = _index(tmp_path, tags)
    assert idx.refresh(lib) == 2
    assert idx.unreliable_dirs() == []
    assert idx.last_scan_partial is False


def test_an_unchanged_folder_keeps_its_untrusted_flag(tmp_path, monkeypatch):
    # A warm scan reuses the stored listing, and with it the stored verdict:
    # the badge fallback must keep probing until a CLEAN listing says otherwise.
    lib, a, b, tags = _two_artists(tmp_path)
    idx = _index(tmp_path, tags)
    real = _fake_listing(monkeypatch, {lib: lambda e: e * 2})
    idx.refresh(lib)
    monkeypatch.setattr(li.os, "scandir", real)
    idx.refresh(lib)  # root mtime unchanged: listing reused, flag carried
    assert idx.unreliable_dirs() == [lib]
    assert idx.last_scan_partial is True


def test_a_warm_scan_keeps_the_measured_shape(tmp_path, monkeypatch):
    # The counts only exist while a listing is being MADE, and a warm scan
    # reuses the stored one, so forgetting them at the start of every walk left
    # Settings explaining a truncation it could no longer measure: "came back
    # incomplete" with no numbers, one scan after the numbers were had.
    lib, a, b, tags = _two_artists(tmp_path)
    idx = _index(tmp_path, tags)
    real = _fake_listing(monkeypatch, {lib: lambda e: e * 2})
    idx.refresh(lib)
    measured = idx.untrusted_listing_shape()
    assert measured[0] > measured[1] > 0
    monkeypatch.setattr(li.os, "scandir", real)
    idx.refresh(lib)  # root mtime unchanged: listing reused, nothing measured
    assert idx.untrusted_listing_shape() == measured


def test_a_healed_share_forgets_the_shape_and_the_recovery(tmp_path, monkeypatch):
    # The other side of keeping it: once every listing is trusted there is
    # nothing to explain and nothing left to reconcile, so neither fact may
    # survive into the note.
    lib, a, b, tags = _two_artists(tmp_path)
    idx = _index(tmp_path, tags)
    _fake_listing(monkeypatch, {lib: lambda e: e * 2})
    idx.refresh(lib)
    idx.note_listing_reconciled(True)
    monkeypatch.undo()
    _bump(lib)  # a real listing this time, so the flag is re-decided
    idx.refresh(lib)
    assert idx.last_scan_partial is False
    assert idx.untrusted_listing_shape() == (0, 0)
    assert idx.last_listing_reconciled is False


def test_the_recovery_verdict_survives_a_relaunch(tmp_path, monkeypatch):
    # It is a fact about the CACHE, not about the run that measured it: the
    # folders a recovery wrote are still there after a restart, so the note
    # must not fall back to warning about missing badges until a scan says so.
    lib, a, b, tags = _two_artists(tmp_path)
    path = str(tmp_path / "library.sqlite3")
    idx = LibraryIndex(path, read_tags=_reader(tags))
    _fake_listing(monkeypatch, {lib: lambda e: e * 2})
    idx.refresh(lib)
    idx.note_listing_reconciled(True)
    idx.close()
    again = LibraryIndex(path, read_tags=_reader(tags))
    assert again.last_scan_partial is True
    assert again.last_listing_reconciled is True


def test_a_recovery_is_complete_only_when_every_name_is_indexed(tmp_path, monkeypatch):
    # What the bridge asks after a fresh mount hands it the real names: a name
    # the cache holds counts however it is spelled, one the walk would never
    # descend into is not missing, and a real absence is.
    lib, a, b, tags = _two_artists(tmp_path)
    idx = _index(tmp_path, tags)
    idx.refresh(lib)
    assert idx.listing_holds_all(lib, ["A", "B"])
    assert idx.listing_holds_all(lib, ["a", "b"])  # folded, as a filesystem compares
    assert idx.listing_holds_all(lib, ["A", "B", "@eaDir", ".hidden"])  # never walked
    assert not idx.listing_holds_all(lib, ["A", "B", "C"])


# ---- never condemn on a listing that cannot be trusted ----------------------------


def test_unreliable_listing_never_condemns_a_missing_child(tmp_path, monkeypatch):
    lib, a, b, tags = _two_artists(tmp_path)
    idx = _index(tmp_path, tags)
    assert idx.refresh(lib) == 2
    # From now on the root repeats A and never names B (the first page, twice).
    _fake_listing(monkeypatch, {lib: lambda e: [x for x in e if x.name == "A"] * 2})
    for _ in range(3):
        _bump(lib)  # forces a fresh listing each time; B is still not in it
        assert idx.refresh(lib) == 2
        assert sorted(x["title"] for x in idx.iter_albums()) == ["Alpha", "Beta"]
        # B is re-stamped every scan, so the generation prune never reaches it.
        assert _rows(idx, "SELECT seen_gen FROM dirs WHERE path = ?", os.path.join(lib, "B")) == [(_gen(idx),)]


def test_truncated_listing_without_repeats_is_caught_by_the_stat_verify(tmp_path, monkeypatch):
    # A plain first-page cutoff: no repeated names, so the dedupe sees nothing.
    # The listing looks perfectly healthy and simply omits B.
    lib, a, b, tags = _two_artists(tmp_path)
    idx = _index(tmp_path, tags)
    assert idx.refresh(lib) == 2
    _fake_listing(monkeypatch, {lib: lambda e: [x for x in e if x.name == "A"]})
    _bump(lib)
    assert idx.refresh(lib) == 2
    assert sorted(x["title"] for x in idx.iter_albums()) == ["Alpha", "Beta"]
    # The stat found B where the listing said nothing: the root is now untrusted.
    assert idx.unreliable_dirs() == [lib]
    assert idx.last_scan_partial is True


def test_healthy_listing_still_condemns_a_vanished_child(tmp_path):
    lib, a, b, tags = _two_artists(tmp_path)
    idx = _index(tmp_path, tags)
    assert idx.refresh(lib) == 2
    shutil.rmtree(os.path.join(lib, "B"))
    _bump(lib)
    # Pruned in ONE scan (the verify stat answers ENOENT), no grace generation.
    assert idx.refresh(lib) == 1
    assert [x["title"] for x in idx.iter_albums()] == ["Alpha"]
    assert _rows(idx, "SELECT COUNT(*) FROM dirs WHERE path LIKE ?", os.path.join(lib, "B") + "%") == [(0,)]
    assert idx.unreliable_dirs() == []
    assert idx.last_scan_partial is False


# ---- the probe by name -------------------------------------------------------------


def _hidden_c(tmp_path, tags):
    c = _mk(tmp_path, "lib/C/[2022] Gamma", ["01.flac", "02.flac", "03.flac"])
    tags[c] = {"album": "Gamma", "artist": "C", "date": "2022"}
    return c


def _root_hides_c(monkeypatch, lib):
    # The root's listing: A and B twice over, never C (a page that repeats).
    _fake_listing(monkeypatch, {lib: lambda e: [x for x in e if x.name != "C"] * 2})


def test_probe_indexes_a_hidden_artist(tmp_path, monkeypatch):
    lib, a, b, tags = _two_artists(tmp_path)
    c = _hidden_c(tmp_path, tags)
    idx = _index(tmp_path, tags)
    _root_hides_c(monkeypatch, lib)
    assert idx.refresh(lib) == 2  # C is invisible to the walk
    assert idx.probe_folders(lib, ["C"], candidates=lambda n: [n]) == 1
    assert sorted(x["title"] for x in idx.iter_albums()) == ["Alpha", "Beta", "Gamma"]
    assert _rows(idx, "SELECT COUNT(*) FROM tracks WHERE folder_path = ?", c) == [(3,)]
    # Written under the root as parent, stamped with the current generation.
    assert _rows(idx, "SELECT parent, seen_gen FROM dirs WHERE path = ?", os.path.join(lib, "C")) == [(lib, _gen(idx))]
    # Survives a warm scan (listing reused)...
    assert idx.refresh(lib) == 3
    # ...a scan that re-lists the untrusted root (still without C)...
    _bump(lib)
    assert idx.refresh(lib) == 3
    # ...and the manual full re-list.
    assert idx.refresh(lib, force_full=True) == 3
    assert sorted(x["title"] for x in idx.iter_albums()) == ["Alpha", "Beta", "Gamma"]


def test_a_probed_folder_is_polled_for_changes(tmp_path, monkeypatch):
    lib, a, b, tags = _two_artists(tmp_path)
    _hidden_c(tmp_path, tags)
    idx = _index(tmp_path, tags)
    _root_hides_c(monkeypatch, lib)
    idx.refresh(lib)
    idx.probe_folders(lib, ["C"], candidates=lambda n: [n])
    assert os.path.join(lib, "C") in idx.container_paths()


def test_probe_tries_the_spellings_in_order_and_stops_at_the_first_hit(tmp_path, monkeypatch):
    lib, a, b, tags = _two_artists(tmp_path)
    _hidden_c(tmp_path, tags)
    idx = _index(tmp_path, tags)
    _root_hides_c(monkeypatch, lib)
    idx.refresh(lib)
    asked: list[str] = []

    def spellings(name):
        asked.append(name)
        return ["C (Explicit)", "C", "C_"]  # the middle one exists

    assert idx.probe_folders(lib, ["C"], candidates=spellings) == 1
    assert asked == ["C"]
    assert _rows(idx, "SELECT COUNT(*) FROM dirs WHERE parent = ?", lib) == [(3,)]


def test_probe_skips_a_spelling_twin_of_a_stored_child(tmp_path, monkeypatch):
    lib, a, b, tags = _two_artists(tmp_path)
    idx = _index(tmp_path, tags)
    _fake_listing(monkeypatch, {lib: lambda e: e * 2})
    idx.refresh(lib)
    before = _rows(idx, "SELECT COUNT(*) FROM dirs")[0][0]
    # "a" is the stored "A" under a folding filesystem: never a second row.
    assert idx.probe_folders(lib, ["a"], candidates=lambda n: [n]) == 0
    assert _rows(idx, "SELECT COUNT(*) FROM dirs")[0][0] == before


def test_probe_returns_none_while_a_scan_holds_the_lock(tmp_path, monkeypatch):
    lib, a, b, tags = _two_artists(tmp_path)
    _hidden_c(tmp_path, tags)
    idx = _index(tmp_path, tags)
    _root_hides_c(monkeypatch, lib)
    idx.refresh(lib)
    idx._scan_busy.acquire()
    try:
        assert idx.probe_folders(lib, ["C"], candidates=lambda n: [n], timeout=0.0) is None
    finally:
        idx._scan_busy.release()
    assert sorted(x["title"] for x in idx.iter_albums()) == ["Alpha", "Beta"]  # nothing written
    # Released: the same ask lands.
    assert idx.probe_folders(lib, ["C"], candidates=lambda n: [n]) == 1


def test_probe_refuses_a_foreign_or_unscanned_root(tmp_path, monkeypatch):
    lib, a, b, tags = _two_artists(tmp_path)
    _hidden_c(tmp_path, tags)
    idx = _index(tmp_path, tags)
    # Never scanned: no generation to stamp, nothing written.
    assert idx.probe_folders(lib, ["C"], candidates=lambda n: [n]) == 0
    assert _rows(idx, "SELECT COUNT(*) FROM dirs") == [(0,)]
    _root_hides_c(monkeypatch, lib)
    idx.refresh(lib)
    # Scanned for lib, asked about another folder: refused.
    other = _mk(tmp_path, "elsewhere", [])
    assert idx.probe_folders(other, ["C"], candidates=lambda n: [n]) == 0
    assert sorted(x["title"] for x in idx.iter_albums()) == ["Alpha", "Beta"]


def test_probe_is_a_no_op_on_a_trusted_listing(tmp_path):
    # A healthy library pays nothing: no untrusted folder, no stat, no write.
    lib, a, b, tags = _two_artists(tmp_path)
    idx = _index(tmp_path, tags)
    idx.refresh(lib)
    asked: list[str] = []
    assert idx.probe_folders(lib, ["Nobody"], candidates=lambda n: asked.append(n) or [n]) == 0
    assert asked == []


def test_probe_writes_no_prune(tmp_path, monkeypatch):
    lib, a, b, tags = _two_artists(tmp_path)
    _hidden_c(tmp_path, tags)
    idx = _index(tmp_path, tags)
    _root_hides_c(monkeypatch, lib)
    idx.refresh(lib)
    # Something the prune WOULD take if it ran: a stale row two generations old.
    with idx._lock:
        idx._conn.execute("UPDATE dirs SET seen_gen = 0 WHERE path = ?", (os.path.join(lib, "B"),))
        idx._conn.commit()
    assert idx.probe_folders(lib, ["C"], candidates=lambda n: [n]) == 1
    assert sorted(x["title"] for x in idx.iter_albums()) == ["Alpha", "Beta", "Gamma"]


def test_probe_verdicts_run_no_per_album_queries(tmp_path, monkeypatch):
    lib, a, b, tags = _two_artists(tmp_path)
    _hidden_c(tmp_path, tags)
    idx = _index(tmp_path, tags)
    _root_hides_c(monkeypatch, lib)
    idx.refresh(lib)
    statements: list[str] = []
    idx._conn.set_trace_callback(statements.append)
    try:
        assert idx.probe_folders(lib, ["C"], candidates=lambda n: [n]) == 1
    finally:
        idx._conn.set_trace_callback(None)
    per_album = [s for s in statements if s.lstrip().startswith("SELECT") and "FROM albums WHERE folder_path" in s]
    assert per_album == []


def test_subtree_walk_never_touches_last_scan_status(tmp_path):
    lib, a, b, tags = _two_artists(tmp_path)
    idx = _index(tmp_path, tags)
    idx.refresh(lib)
    assert idx.last_scan_status == SCAN_OK
    gone = os.path.join(lib, "Nobody")
    assert idx._walk_album_dirs(gone, lambda: True, lambda *_a, **_k: None, _gen(idx), subtree=True) is None
    assert idx.last_scan_status == SCAN_OK


def test_the_listing_shape_is_measured_and_survives_a_relaunch(tmp_path, monkeypatch):
    """The counts behind the verdict are kept, so the app can tell the user how
    much of the folder it was shown instead of only the word incomplete."""
    lib, _a, _b, tags = _two_artists(tmp_path)
    _hidden_c(tmp_path, tags)
    idx = _index(tmp_path, tags)
    # The root hands over its two visible artists five times over.
    _fake_listing(monkeypatch, {lib: lambda e: [x for x in e if x.name != "C"] * 5})
    idx.refresh(lib)
    handed, distinct = idx.untrusted_listing_shape()
    assert (handed, distinct) == (10, 2)
    assert idx.last_scan_partial is True
    idx.close()
    # A relaunch can explain the state before it has scanned anything.
    again = _index(tmp_path, tags)
    assert again.untrusted_listing_shape() == (10, 2)
    again.close()


def test_a_healthy_scan_reports_no_shape_at_all(tmp_path):
    lib, _a, _b, tags = _two_artists(tmp_path)
    idx = _index(tmp_path, tags)
    idx.refresh(lib)
    assert idx.untrusted_listing_shape() == (0, 0)
    assert idx.last_scan_partial is False
    idx.close()


def test_a_share_that_heals_stops_explaining_the_truncation(tmp_path, monkeypatch):
    lib, _a, _b, tags = _two_artists(tmp_path)
    _hidden_c(tmp_path, tags)
    idx = _index(tmp_path, tags)
    _root_hides_c(monkeypatch, lib)
    idx.refresh(lib)
    assert idx.untrusted_listing_shape()[0] > 0
    # The share starts listing properly: re-list the root and the note retires.
    monkeypatch.undo()
    _bump(lib)
    idx.refresh(lib, force_full=True)
    assert idx.untrusted_listing_shape() == (0, 0)
    assert idx.last_scan_partial is False
    idx.close()


def test_a_batch_finds_every_hidden_artist_in_one_call(tmp_path, monkeypatch):
    """probe_folders takes the cache lock, reads the tree and writes ONCE per
    call, which is why a whole page of names is asked together."""
    lib, _a, _b, tags = _two_artists(tmp_path)
    hidden = []
    for artist in ("C", "D", "E"):
        folder = _mk(lib, f"{artist}/[2022] Rec{artist}", ["1.flac"])
        tags[folder] = {"album": f"Rec{artist}", "artist": artist, "date": "2022", "title": "Song"}
        hidden.append(folder)
    hide = {"C", "D", "E"}
    _fake_listing(monkeypatch, {lib: lambda e: [x for x in e if x.name not in hide] * 2})
    idx = _index(tmp_path, tags)
    assert idx.refresh(lib) == 2
    reads = []
    real = idx._load_dir_tree

    def counting():
        reads.append(1)
        return real()

    idx._load_dir_tree = counting
    # Three folders found. The fixed cost of a probe is paid ONCE for the whole
    # batch however many names it carries (a hit then walks its own subtree),
    # which is what makes asking about a page of results affordable: 200 names
    # that find nothing cost exactly what one name costs.
    names = ["C", "D", "E"] + [f"Nobody {i}" for i in range(200)]
    assert idx.probe_folders(lib, names, candidates=lambda n: [n]) == 3
    assert len(reads) == 1 + 3
    assert sorted(x["title"] for x in idx.iter_albums()) == ["Alpha", "Beta", "RecC", "RecD", "RecE"]
    idx.close()


def test_a_found_folder_counts_even_when_its_albums_need_no_re_read(tmp_path, monkeypatch):
    """The answer is how many FOLDERS were found. Counting albums-needing-a-read
    instead reported 0 for a folder whose rows were written but whose tags were
    already current, and the caller read that as failure and never republished."""
    lib, _a, _b, tags = _two_artists(tmp_path)
    c = _hidden_c(tmp_path, tags)
    idx = _index(tmp_path, tags)
    _root_hides_c(monkeypatch, lib)
    idx.refresh(lib)
    assert idx.probe_folders(lib, ["C"], candidates=lambda n: [n]) == 1
    # Retire only the folder row, leaving the album row current, then ask again.
    with idx._lock:
        idx._conn.execute("DELETE FROM dirs WHERE path LIKE ?", (os.path.join(lib, "C") + "%",))
        idx._conn.commit()
    assert idx.probe_folders(lib, ["C"], candidates=lambda n: [n]) == 1
    assert _rows(idx, "SELECT COUNT(*) FROM albums WHERE folder_path = ?", c) == [(1,)]
    idx.close()


def test_a_cache_flagged_before_the_counts_existed_buys_one_re_list(tmp_path, monkeypatch):
    """A folder is flagged unreliable while it is being listed, and a warm scan
    reuses that listing forever, so a cache flagged by an earlier version could
    never say by HOW MUCH it was short. Opening it zeroes the flagged folder's
    stored mtime once, which buys exactly one re-list."""
    lib, _a, _b, tags = _two_artists(tmp_path)
    _hidden_c(tmp_path, tags)
    idx = _index(tmp_path, tags)
    _root_hides_c(monkeypatch, lib)
    idx.refresh(lib)
    assert idx.untrusted_listing_shape() == (4, 2)
    # Rewind to what an earlier version left: flagged, but with no counts and
    # no marker, and a root whose mtime says there is nothing to re-list.
    with idx._lock:
        idx._conn.execute("DELETE FROM meta WHERE key IN ('untrusted_listing_shape', 'listing_shape_measured')")
        idx._conn.commit()
    idx.close()

    again = _index(tmp_path, tags)
    assert again.untrusted_listing_shape() == (0, 0)
    assert _rows(again, "SELECT mtime FROM dirs WHERE path = ?", lib) == [(0.0,)]  # re-list bought
    again.refresh(lib)  # a WARM scan: only the flagged folder is listed again
    assert again.untrusted_listing_shape() == (4, 2)
    again.close()

    # ...and it is bought once, not on every launch.
    third = _index(tmp_path, tags)
    assert _rows(third, "SELECT mtime FROM dirs WHERE path = ?", lib)[0][0] != 0.0
    third.close()


# ---- the probe applies the walk's own skip rule -------------------------------------


def test_probe_never_indexes_a_folder_the_walk_skips(tmp_path, monkeypatch):
    """A probe is a lookup by name, and the names come from whatever the caller
    read off a mount, so they include the metadata folders a NAS writes.

    Indexing one is a lasting defect, not a cosmetic one: the walk never lists
    @eaDir, so the row it leaves is a child every later listing "loses", the
    parent is flagged untrusted on every scan from then on, and the folder
    itself shows up as an album the user owns, which makes the download gate
    refuse to re-fetch a deleted album."""
    lib, a, b, tags = _two_artists(tmp_path)
    for junk in ("@eaDir", "#recycle", ".hidden"):
        d = _mk(tmp_path, f"lib/{junk}/[2022] Junk", ["01.flac"])
        tags[d] = {"album": "Junk", "artist": junk, "date": "2022"}
    idx = _index(tmp_path, tags)
    _root_hides_c(monkeypatch, lib)
    idx.refresh(lib)
    assert idx.probe_folders(lib, ["@eaDir", "#recycle", ".hidden"], candidates=lambda n: [n]) == 0
    assert sorted(x["title"] for x in idx.iter_albums()) == ["Alpha", "Beta"]
    for junk in ("@eaDir", "#recycle", ".hidden"):
        assert _rows(idx, "SELECT COUNT(*) FROM dirs WHERE path LIKE ?", os.path.join(lib, junk) + "%") == [(0,)]


def test_a_cache_polluted_by_an_older_probe_is_swept_on_open(tmp_path, monkeypatch):
    """Rows an older build's probe already wrote would age out over two clean
    generations, but until then they keep flagging their parent, so a cache
    carrying them is swept once when it is opened."""
    lib, a, b, tags = _two_artists(tmp_path)
    junk = _mk(tmp_path, "lib/@eaDir/[2022] Junk", ["01.flac"])
    tags[junk] = {"album": "Junk", "artist": "@eaDir", "date": "2022"}
    idx = _index(tmp_path, tags)
    idx.refresh(lib)
    # Stand in for what the older probe left behind, and clear the marker so the
    # reopen below is the first one that knows to look. The generation is read
    # BEFORE the lock: _rows takes it too, and it is not reentrant.
    gen = _gen(idx)
    with idx._lock:
        idx._conn.execute(
            "INSERT INTO dirs (path, parent, mtime, listed, is_album, seen_gen) VALUES (?, ?, 0, 1, 1, ?)",
            (junk, os.path.join(lib, "@eaDir"), gen),
        )
        idx._conn.execute(
            "INSERT INTO albums (folder_path, album, artist, year) VALUES (?, 'Junk', '@eaDir', '2022')", (junk,)
        )
        idx._conn.execute("DELETE FROM meta WHERE key = 'skipped_dirs_pruned'")
        idx._conn.commit()
    idx.close()

    reopened = _index(tmp_path, tags)
    assert _rows(reopened, "SELECT COUNT(*) FROM dirs WHERE path = ?", junk) == [(0,)]
    assert sorted(x["title"] for x in reopened.iter_albums()) == ["Alpha", "Beta"]
    # The two real artists are untouched, and the sweep runs once.
    assert _rows(reopened, "SELECT COUNT(*) FROM dirs WHERE path LIKE ?", os.path.join(lib, "A") + "%")[0][0] >= 1


def test_a_root_living_under_a_hidden_folder_is_not_swept(tmp_path, monkeypatch):
    """The rule judges only what lies BELOW the root. A user whose library sits
    inside a dot folder still has a library."""
    lib = _mk(tmp_path, ".private/lib", [])
    a = _mk(tmp_path, ".private/lib/A/[2020] Alpha", ["01.flac"])
    tags = {a: {"album": "Alpha", "artist": "A", "date": "2020"}}
    idx = _index(tmp_path, tags)
    assert idx.refresh(lib) == 1
    with idx._lock:
        idx._conn.execute("DELETE FROM meta WHERE key = 'skipped_dirs_pruned'")
        idx._conn.commit()
    idx.close()
    reopened = _index(tmp_path, tags)
    assert [x["title"] for x in reopened.iter_albums()] == ["Alpha"]


# ---- the recovery bar only ever climbs ----


def test_probe_progress_never_goes_backwards(tmp_path, monkeypatch):
    """Recovering a whole share is minutes of reading, so the probe feeds the
    scan's own progress events. Both counters are running totals across every
    folder found so far, and both must be monotonic: a bar that jumps backwards
    reads as a hang, which is the very symptom this feed exists to cure.

    Several albums per artist on purpose. With one album each, "album folders
    seen" and "folders visited" coincide, and mixing the two counters is
    invisible.
    """
    lib, _a, _b, tags = _two_artists(tmp_path)
    for artist in ("C", "D", "E"):
        for n in range(3):
            folder = _mk(lib, f"{artist}/[202{n}] Rec{artist}{n}", ["1.flac"])
            tags[folder] = {"album": f"Rec{artist}{n}", "artist": artist, "date": f"202{n}", "title": "S"}
    hide = {"C", "D", "E"}
    _fake_listing(monkeypatch, {lib: lambda e: [x for x in e if x.name not in hide] * 2})
    idx = _index(tmp_path, tags)
    assert idx.refresh(lib) == 2

    events: list[dict] = []
    assert (
        idx.probe_folders(
            lib, ["C", "D", "E"], candidates=lambda n: [n], on_progress=events.append, progress_interval=0.0
        )
        == 3
    )
    walks = [e for e in events if e.get("phase") == "walk"]
    assert walks, events
    found = [w["found"] for w in walks]
    checked = [w["checked"] for w in walks]
    assert found == sorted(found), f"the album count fell: {found}"
    assert checked == sorted(checked), f"the folder count fell: {checked}"
    # Nine album folders across the three recovered artists, and the walk
    # visited at least those nine plus the three artist folders themselves.
    assert found[-1] == 9
    assert checked[-1] >= 12
    idx.close()


# ---- a spelling twin is decided by identity, not by the folded name ----


class _Entry:
    """A directory entry that reports one name while pointing somewhere else.

    This machine's filesystem folds both case and normalisation, so two folders
    whose names differ only that way cannot be created here at all: `Blur` and
    `blur` are one inode. A case-sensitive share holds them as two. Pointing a
    listed entry at a folder that really is separate reproduces the only thing
    the walk actually has to decide (same folded name, different folder) on a
    filesystem that cannot hold the literal case.
    """

    def __init__(self, name, path):
        self.name = name
        self.path = path

    def is_dir(self, follow_symlinks=True):
        return True


def test_a_folded_name_match_does_not_condemn_a_different_folder(tmp_path, monkeypatch):
    """On a case-sensitive share `Blur` and `blur` are two folders the user
    owns. The stored child is retired only when the listing's spelling is the
    SAME folder under another name, which is a question about inodes, not about
    names: condemning on the folded name alone deletes the rows for a folder
    that is still sitting on disk."""
    lib, _a, _b, tags = _two_artists(tmp_path)
    kept = _mk(tmp_path, "lib/Blur/[2020] Parklife", ["01.flac"])
    tags[kept] = {"album": "Parklife", "artist": "Blur", "date": "2020"}
    # A genuinely separate folder whose basename folds to the same key.
    other = _mk(tmp_path, "elsewhere/blur/[2021] Leisure", ["01.flac"])
    tags[other] = {"album": "Leisure", "artist": "blur", "date": "2021"}

    idx = _index(tmp_path, tags)
    assert idx.refresh(lib) == 3
    assert sorted(x["title"] for x in idx.iter_albums()) == ["Alpha", "Beta", "Parklife"]

    twin = os.path.join(tmp_path, "elsewhere", "blur")
    _fake_listing(
        monkeypatch,
        {lib: lambda e: [x for x in e if x.name != "Blur"] + [_Entry("blur", twin)]},
    )
    _bump(lib)
    idx.refresh(lib)

    titles = sorted(x["title"] for x in idx.iter_albums())
    assert "Parklife" in titles, f"the stored folder was condemned on a name match alone: {titles}"
    assert _rows(idx, "SELECT COUNT(*) FROM tracks WHERE folder_path = ?", kept) == [(1,)]
    idx.close()


def test_one_folder_under_two_spellings_still_retires_the_stored_row(tmp_path):
    """The other half of the same decision, and the reason the branch exists:
    where the filesystem folds, the two spellings ARE one folder, the listing
    picks the spelling, and the stored row must go or the artist is counted
    twice."""
    lib = _mk(tmp_path, "lib", [])
    real = _mk(tmp_path, "lib/Blur", [])
    assert li._is_one_folder(os.path.join(lib, "Blur"), real) is True
    # Two genuinely different folders are never one.
    assert li._is_one_folder(real, _mk(tmp_path, "lib/Oasis", [])) is False
    # A stored spelling that is gone retires rather than lingering forever.
    assert li._is_one_folder(os.path.join(lib, "Vanished"), real) is True
