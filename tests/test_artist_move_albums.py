"""The library albums a same-name move names on the artist page.

A move remembers the library albums its check matched (``titles`` in the
evidence); the artist page's panel reads them back, dressed with year and
cover, through ``artistMoveAlbumsLoaded``. A move recorded before titles were
kept falls back to every album the library holds under the name.
"""

from __future__ import annotations

import types

from waves import artist_import
from waves.waves_ui.bridge_releases import ReleasesMixin


class _Cache:
    def __init__(self, albums):
        self._albums = albums

    def artist_titles(self, key):
        if key != "adept":
            return {"albums": [], "tracks": []}
        return {"albums": [a["title"] for a in self._albums], "tracks": []}

    def artist_albums(self, keys):
        return {"adept": list(self._albums)} if "adept" in keys else {}

    def distinct_artists(self):
        return [{"key": "adept", "name": "Adept"}]


def _album(title, year):
    return {
        "folder": "",
        "title": title,
        "year": year,
        "tracks": 10,
        "codec": "flac",
        "bits": 16,
        "rate": 44100,
        "bitrate": 0,
        "mtime": 0.0,
    }


def _host(cache):
    got = []
    host = types.SimpleNamespace(
        _releases_library_cache=lambda: cache,
        artistMoveAlbumsLoaded=types.SimpleNamespace(emit=lambda oid, d: got.append((oid, d))),
        settings=None,
    )
    for name in ("_releases_anchor_library", "_releases_anchor_folds", "_releases_held", "_releases_folder_cover"):
        setattr(host, name, getattr(ReleasesMixin, name).__get__(host))
    return host, got


def _move(titles=None):
    ev = {"albums_n": 2}
    if titles is not None:
        ev["titles"] = titles
    return {"old_id": "1", "new_id": "2", "old": {"name": "Adept"}, "new": {"name": "Adept"}, "evidence": ev}


LIB = [_album("Death Dealers", "2013"), _album("Silence the World", "2011"), _album("Sleepless", "2016")]


def test_the_matched_albums_are_named():
    host, got = _host(_Cache(LIB))
    ReleasesMixin._releases_move_albums_run(host, "1", _move(["Silence The World", "Death Dealers"]))
    oid, d = got[-1]
    assert oid == "1"
    assert [h["title"] for h in d["held"]] == ["Death Dealers", "Silence the World"]
    assert d["total"] == 2


def test_the_count_keeps_the_albums_past_the_saved_titles_cap():
    host, got = _host(_Cache(LIB))
    mv = _move(["Death Dealers"])
    mv["evidence"]["albums_n"] = 55
    ReleasesMixin._releases_move_albums_run(host, "1", mv)
    d = got[-1][1]
    assert [h["title"] for h in d["held"]] == ["Death Dealers"] and d["total"] == 55


def test_saved_titles_the_library_no_longer_holds_never_claim_every_album():
    host, got = _host(_Cache(LIB))
    ReleasesMixin._releases_move_albums_run(host, "1", _move(["Gone Away", "Also Gone"]))
    d = got[-1][1]
    assert d["held"] == [] and d["total"] == 0


def test_an_album_gone_from_the_library_leaves_the_count():
    host, got = _host(_Cache(LIB))
    ReleasesMixin._releases_move_albums_run(host, "1", _move(["Death Dealers", "Gone Away"]))
    d = got[-1][1]
    assert [h["title"] for h in d["held"]] == ["Death Dealers"] and d["total"] == 1


def test_a_move_without_titles_names_every_library_album():
    host, got = _host(_Cache(LIB))
    ReleasesMixin._releases_move_albums_run(host, "1", _move())
    assert got[-1][1]["total"] == 3


def test_weigh_returns_every_distinctive_album_it_matched():
    lib = {"albums": ["Hollow", "Death Dealers", "Silence the World", "Sleepless"], "tracks": []}
    out = artist_import.weigh(
        "Adept",
        lib,
        [{"id": "2", "name": "Adept", "releases": ["Hollow", "Death Dealers", "Silence the World", "Sleepless"]}],
    )
    assert sorted(out[0]["albums_owned"]) == sorted(lib["albums"])
