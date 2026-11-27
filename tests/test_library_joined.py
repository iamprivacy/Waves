"""JoinedLibrary: two folders' scan caches answering as one library."""

from __future__ import annotations

import os

from test_mb_library import A_ID, B_ID, _read_album_tags, _tagged

from waves.library_index import JoinedLibrary, LibraryIndex


def _cache(tmp_path, name, albums):
    root = str(tmp_path / name)
    tags = {}
    for artist, title in albums:
        d = os.path.join(root, artist, title)
        os.makedirs(d, exist_ok=True)
        open(os.path.join(d, "1.flac"), "w").close()
        tags[d] = {"album": title, "artist": artist, "date": "2001"}
    idx = LibraryIndex(str(tmp_path / f"{name}.db"), read_tags=lambda p: tags.get(os.path.dirname(p)))
    idx.refresh(root)
    return idx


def test_artist_ids_must_agree_across_both_folders(tmp_path):
    # Each folder alone agrees on one id; together they disagree, so the
    # joined answer claims nothing rather than whichever folder came first.
    _tagged(tmp_path / "lib" / "Nirvana" / "Nevermind", [A_ID])
    _tagged(tmp_path / "dl" / "Nirvana" / "Bleach", [B_ID])
    lib = LibraryIndex(str(tmp_path / "lib.db"), read_tags=_read_album_tags)
    dl = LibraryIndex(str(tmp_path / "dl.db"), read_tags=_read_album_tags)
    lib.refresh(str(tmp_path / "lib"))
    dl.refresh(str(tmp_path / "dl"))
    assert lib.artist_tag_ids() == {"nirvana": A_ID} and dl.artist_tag_ids() == {"nirvana": B_ID}
    assert JoinedLibrary(lib, dl).artist_tag_ids() == {}
    # The same id in both folders settles.
    _tagged(tmp_path / "dl2" / "Nirvana" / "Bleach", [A_ID])
    dl2 = LibraryIndex(str(tmp_path / "dl2.db"), read_tags=_read_album_tags)
    dl2.refresh(str(tmp_path / "dl2"))
    assert JoinedLibrary(lib, dl2).artist_tag_ids() == {"nirvana": A_ID}


def test_one_closed_cache_leaves_the_other_answering(tmp_path):
    lib = _cache(tmp_path, "lib", [("Air", "Moon Safari")])
    dl = _cache(tmp_path, "dl", [("Low", "Things")])
    joined = JoinedLibrary(lib, dl)
    assert {r["name"] for r in joined.distinct_artists()} == {"Air", "Low"}
    dl.close()
    assert {r["name"] for r in joined.distinct_artists()} == {"Air"}
    assert joined.has_presence_rows()


def test_buckets_pool_one_album_held_in_both(tmp_path):
    lib = _cache(tmp_path, "lib", [("Air", "Moon Safari")])
    dl = _cache(tmp_path, "dl", [("Air", "Moon Safari")])
    joined = JoinedLibrary(lib, dl)
    key = joined.distinct_artists()[0]["key"]
    buckets = joined.artist_buckets(key)
    assert len(buckets) == 1 and len(buckets[0]) == 2
    assert len(list(joined.iter_albums())) == 2


def test_tidal_ids_must_agree_across_both_folders(tmp_path):
    import json

    lib = _cache(tmp_path, "lib", [("Adept", "Sleepless")])
    dl = _cache(tmp_path, "dl", [("Adept", "Death Dealers")])
    for idx, ident in ((lib, "34868882"), (dl, "3581093")):
        folder = idx._read("SELECT folder_path FROM albums")[0][0]
        with idx._lock:
            idx._conn.execute(
                "INSERT OR REPLACE INTO file_tags (folder_path, name, tags) VALUES (?, ?, ?)",
                (folder, "1.flac", json.dumps({"easy": {"waves_tidal_album_artist_id": [ident]}})),
            )
            idx._conn.commit()
    assert lib.artist_tidal_ids() == {"adept": "34868882"}
    assert JoinedLibrary(lib, dl).artist_tidal_ids() == {}


def test_one_folder_held_by_two_caches_is_one_copy(tmp_path):
    """The download index and the scan cache can both hold a folder for a
    moment (the scan met it before the handover). That is one copy on disk."""
    root = str(tmp_path / "lib")
    d = os.path.join(root, "Air", "Moon Safari")
    os.makedirs(d)
    open(os.path.join(d, "1.flac"), "w").close()
    tags = {"album": "Moon Safari", "artist": "Air", "date": "1998", "title": "Sexy Boy"}
    lib = LibraryIndex(str(tmp_path / "lib.db"), read_tags=lambda p: tags)
    lib.refresh(root)
    overlay = lib.landed_overlay(root)
    assert overlay.index_folders(root, [d]) == 1
    joined = JoinedLibrary(lib, overlay)
    key = joined.distinct_artists()[0]["key"]
    assert [len(b) for b in joined.artist_buckets(key)] == [1]
    assert len(joined.presence_facts(("moon safari", key))) == 1
    (tkey,) = lib._read("SELECT tkey_title, tkey_artist FROM tracks")
    assert tkey[0] and len(lib.track_facts(tuple(tkey))) == 1
    assert len(joined.track_facts(tuple(tkey))) == 1


def _held_twice(tmp_path, spell=lambda p: p):
    root = str(tmp_path / "lib")
    d = os.path.join(root, "Air", "Moon Safari")
    os.makedirs(d)
    for n in ("1.flac", "2.flac"):
        open(os.path.join(d, n), "w").close()
    tags = {"album": "Moon Safari", "artist": "Air", "date": "1998", "title": "Sexy Boy"}
    lib = LibraryIndex(str(tmp_path / "lib.db"), read_tags=lambda p: tags)
    lib.refresh(root)
    overlay = lib.landed_overlay(root)
    assert overlay.index_folders(spell(root), [spell(d)]) == 1
    return JoinedLibrary(overlay, lib)


def test_artist_totals_count_a_folder_both_caches_hold_once(tmp_path):
    joined = _held_twice(tmp_path)
    (row,) = joined.distinct_artists()
    assert (row["albums"], row["tracks"]) == (1, 2)
    assert len(list(joined.iter_albums())) == 1
    assert len(list(joined.iter_tracks())) == 2


def test_a_folder_spelled_two_ways_is_one_copy(tmp_path):
    """A share hands the folder back decomposed (NFD) while the scan stored it
    composed: still one folder."""
    import unicodedata

    joined = _held_twice(tmp_path / "Bj\u00f6rk", spell=lambda p: unicodedata.normalize("NFD", p))
    key = joined.distinct_artists()[0]["key"]
    assert len(joined.presence_facts(("moon safari", key))) == 1
    assert [len(b) for b in joined.artist_buckets(key)] == [1]
