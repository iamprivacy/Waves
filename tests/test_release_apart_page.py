"""Releases set apart on a shared same-name profile (see waves.release_ties)
leave the artist page's shelves for their own section and never join the
artist's discography download."""

from __future__ import annotations

from types import SimpleNamespace

from tidalapi.album import Album

from waves.follows import FollowStore
from waves.release_ties import Ties
from waves.waves_ui import backend
from waves.waves_ui.backend import WavesBridge


def test_the_page_moves_apart_releases_to_their_own_section():
    payload = {"albums": [{"id": "1"}, {"id": "2"}], "eps": [{"id": "3"}, {"id": "4"}], "apart": []}
    backend._split_apart(payload, {"2", "4"})
    assert [d["id"] for d in payload["albums"]] == ["1"]
    assert [d["id"] for d in payload["eps"]] == ["3"]
    assert [d["id"] for d in payload["apart"]] == ["2", "4"]
    same = {"albums": [{"id": "1"}], "eps": [], "apart": []}
    backend._split_apart(same, set())
    assert same == {"albums": [{"id": "1"}], "eps": [], "apart": []}


def _release(aid):
    a = Album.__new__(Album)
    a.id = aid
    a.name = f"R{aid}"
    a.artist = SimpleNamespace(name="Band", id=1)
    a.artists = [a.artist]
    return a


class _Stub:
    _artist_releases = WavesBridge._artist_releases
    _releases_apart_for = WavesBridge._releases_apart_for

    def __init__(self, store):
        self._releases = store

    def _waves_pref_bool(self, key):
        return key == "disco_albums"


def _store(tmp_path):
    s = FollowStore(str(tmp_path / "releases.sqlite3"))
    s.follow("1", "Band", "")
    rows = [
        {"id": i, "title": "R" + i, "date": "2026-01-01", "rtype": "album", "sibling_ids": [i, i + "-hires"]}
        for i in ("mine", "theirs")
    ]
    s.record_check("1", rows)
    return s


def test_the_discography_leaves_out_apart_releases(tmp_path):
    s = _store(tmp_path)
    s.set_apart("1", Ties("1"), ["theirs"], "sig")
    artist = SimpleNamespace(id=1, get_albums=lambda: [_release("mine"), _release("theirs")])
    own, _guest, complete = _Stub(s)._artist_releases(artist)
    assert complete and [a.id for a in own] == ["mine"]


def test_apart_ids_reach_every_edition_and_only_on_a_shared_follow(tmp_path):
    s = _store(tmp_path)
    stub = _Stub(s)
    assert stub._releases_apart_for("1") == set()
    s.set_apart("1", Ties("1"), ["theirs"], "sig")
    assert stub._releases_apart_for("1") == {"theirs", "theirs-hires"}
    assert stub._releases_apart_for("2") == set()
    assert _Stub(None)._releases_apart_for("1") == set()
