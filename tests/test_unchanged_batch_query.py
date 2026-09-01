"""The warm scan's unchanged-verdict pass costs one query, not one per album.

THE COST THIS FENCES OFF
------------------------
``refresh`` decides which candidate folders need a tag re-read by asking
``_unchanged`` per candidate, and each ask was one sqlite round-trip with a
correlated subquery: 18k round-trips on a warm scan of a big library, all to
re-derive rows a single query returns. The verdict pass now loads every
album's row once (``_unchanged_rows``, a LEFT JOIN against a grouped track
count) and evaluates the same predicate in Python (``_unchanged_verdict``).

Pinned here: the per-candidate query shape never runs during a scan's verdict
pass; the batch rows produce the same verdict as the per-path query for
present and missing folders alike; and a genuinely changed folder is still
re-read while its unchanged siblings are not.
"""

from __future__ import annotations

import os

from waves.library_index import LibraryIndex


def _mk(base, rel, files):
    d = os.path.join(base, *rel.split("/"))
    os.makedirs(d, exist_ok=True)
    for name in files:
        open(os.path.join(d, name), "w").close()
    return d


def _reader(tagmap, counter):
    def read(path):
        counter.append(path)
        return tagmap.get(os.path.dirname(path))

    return read


def _library(tmp_path, n_albums):
    lib = _mk(tmp_path, "lib", [])
    tags = {}
    for i in range(n_albums):
        d = _mk(tmp_path, f"lib/Artist {i}/[2020] Album {i}", ["01.flac", "02.flac"])
        tags[d] = {"album": f"Album {i}", "artist": f"Artist {i}", "date": "2020"}
    return lib, tags


def test_warm_scan_verdicts_run_no_per_album_queries(tmp_path):
    lib, tags = _library(tmp_path, 12)
    reads: list[str] = []
    idx = LibraryIndex(str(tmp_path / "cache.sqlite3"), read_tags=_reader(tags, reads))
    assert idx.refresh(lib) == 12
    reads.clear()

    statements: list[str] = []
    idx._conn.set_trace_callback(statements.append)
    try:
        assert idx.refresh(lib) == 12
    finally:
        idx._conn.set_trace_callback(None)

    # The per-path shape is a SELECT against one folder_path; the prune's
    # DELETE ... WHERE folder_path NOT IN (...) is a different, legitimate
    # statement and stays out of the filter.
    per_album = [s for s in statements if s.lstrip().startswith("SELECT") and "FROM albums WHERE folder_path" in s]
    assert per_album == [], "the verdict pass fell back to one query per candidate"
    assert reads == []  # nothing changed, nothing re-read


def test_batch_rows_agree_with_the_per_path_query(tmp_path):
    lib, tags = _library(tmp_path, 4)
    idx = LibraryIndex(str(tmp_path / "cache.sqlite3"), read_tags=_reader(tags, []))
    idx.refresh(lib)

    known = idx._unchanged_rows()
    for path in known:
        st = os.stat(path)
        count = sum(1 for f in os.listdir(path) if f.endswith(".flac"))
        assert idx._unchanged(path, st.st_mtime, count) == idx._unchanged_verdict(known[path], st.st_mtime, count)
        # A wrong mtime flips both the same way.
        assert idx._unchanged(path, st.st_mtime + 5, count) == idx._unchanged_verdict(
            known[path], st.st_mtime + 5, count
        )
    # A folder the cache has never seen: per-path finds no row, batch has no key.
    ghost = os.path.join(lib, "Nobody", "[1999] Nothing")
    assert idx._unchanged(ghost, 1.0, 2) is False
    assert idx._unchanged_verdict(known.get(ghost), 1.0, 2) is False


def test_a_changed_folder_is_still_rescanned_alone(tmp_path):
    lib, tags = _library(tmp_path, 6)
    reads: list[str] = []
    idx = LibraryIndex(str(tmp_path / "cache.sqlite3"), read_tags=_reader(tags, reads))
    idx.refresh(lib)
    reads.clear()

    grown = sorted(tags)[0]
    open(os.path.join(grown, "03.flac"), "w").close()
    os.utime(grown, (os.stat(grown).st_atime, os.stat(grown).st_mtime + 10))

    idx.refresh(lib)
    touched = {os.path.dirname(p) for p in reads}
    assert touched == {grown}
