"""The scan reads one album per artist folder first, then the rest
(library_index._artists_first), so the Releases library import can find a
big library's artists long before the scan ends. Only the order changes."""

from __future__ import annotations

import os

import waves.library_index as li
from waves.library_index import LibraryIndex, _artists_first


def _c(path):
    return (path, path + "/01.flac", 0.0, 1, (path + "/01.flac",))


def test_one_album_per_parent_folder_comes_first():
    got = _artists_first(
        [
            _c("/m/B/b2"),
            _c("/m/A/a3"),
            _c("/m/A/a1"),
            _c("/m/C/c1"),
            _c("/m/B/b1"),
            _c("/m/A/a2"),
        ]
    )
    assert [c[0] for c in got] == ["/m/A/a1", "/m/B/b1", "/m/C/c1", "/m/A/a2", "/m/A/a3", "/m/B/b2"]


def test_every_candidate_is_kept_exactly_once():
    cands = [_c(f"/m/{a}/{i}") for a in "QWERTY" for i in range(4)] + [_c("/m/flat")]
    got = _artists_first(cands)
    assert sorted(got) == sorted(cands)
    assert len(got) == len(cands)


def test_a_flat_layout_keeps_plain_path_order():
    cands = [_c("/m/z"), _c("/m/a"), _c("/m/k")]
    # One parent for all: the first album leads, the rest follow in order.
    assert [c[0] for c in _artists_first(cands)] == ["/m/a", "/m/k", "/m/z"]


def test_empty_is_empty():
    assert _artists_first([]) == []


def _mk(base, rel):
    d = os.path.join(base, *rel.split("/"))
    os.makedirs(d, exist_ok=True)
    open(os.path.join(d, "01.flac"), "w").close()
    return d


def _library(tmp_path):
    lib = str(tmp_path / "lib")
    tags = {}
    for artist, n in (("Caribou", 3), ("Four Tet", 2), ("Nils Frahm", 1)):
        for i in range(n):
            d = _mk(lib, f"{artist}/Album {i}")
            tags[d] = {"album": f"{artist} {i}", "artist": artist, "date": "2020"}
    return lib, tags


def _scan(tmp_path, name, lib, tags, order=None):
    idx = LibraryIndex(str(tmp_path / name), read_tags=lambda p: tags.get(os.path.dirname(p)))
    if order is not None:
        real = idx._read_and_upsert

        def spy(to_read, alive, emit):
            order.extend(c[0] for c in to_read)
            return real(to_read, alive, emit)

        idx._read_and_upsert = spy
    idx.refresh(lib)
    return idx


def test_a_cold_scan_reads_each_artist_before_any_second_album(tmp_path):
    lib, tags = _library(tmp_path)
    order: list[str] = []
    _scan(tmp_path, "a.sqlite3", lib, tags, order=order)
    assert len(order) == 6
    assert {os.path.basename(os.path.dirname(p)) for p in order[:3]} == {"Caribou", "Four Tet", "Nils Frahm"}


def test_the_reorder_leaves_the_indexed_rows_unchanged(tmp_path, monkeypatch):
    lib, tags = _library(tmp_path)
    reordered = _scan(tmp_path, "a.sqlite3", lib, tags)
    monkeypatch.setattr(li, "_artists_first", lambda cands: list(cands))
    plain = _scan(tmp_path, "b.sqlite3", lib, tags)

    def rows(idx):
        return sorted(tuple(sorted((k, str(v)) for k, v in a.items())) for a in idx.iter_albums())

    assert rows(reordered) == rows(plain)
    assert len(rows(plain)) == 6
