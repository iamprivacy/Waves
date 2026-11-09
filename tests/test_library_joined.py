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
