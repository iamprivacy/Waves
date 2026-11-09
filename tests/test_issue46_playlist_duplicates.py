"""Issue #46: My Tidal > Playlists listed the same playlist two or three times.

tidalapi's ``playlists_paginated`` fetched its pages in parallel sorted by
creation date. Playlists created in one batch tie on that key and the server
broke the tie differently per page, so a playlist landed on several pages
while others fell off. The sweep now reads the pages in turn, by cursor,
newest first, keeps each playlist once by id, and fills any shortfall with a
name-order pass.
"""

from types import SimpleNamespace

from _collection_server import CollectionServer, playlist_row

from waves.helper.tidal import user_media_lists


def _ids(rows) -> list[str]:
    return [str(r.id) for r in rows]


def _orders(server) -> set[str]:
    return {c["order"] for c in server.calls if c["folderId"] == "root" and c["includeOnly"] == "PLAYLIST"}


def test_a_playlist_repeated_across_pages_is_listed_once():
    # The tie-broken "1982" rides on every page.
    rows = ["1982"] + [f"a{i}" for i in range(49)] + ["1982"] + [f"b{i}" for i in range(49)] + ["1982", "c0"]
    server = CollectionServer(by_order={"DATE": rows}, count=100)
    ids = _ids(user_media_lists(server.session())["playlists"])
    assert ids.count("1982") == 1
    assert len(ids) == len(set(ids)) == 100


def test_the_default_order_stays_newest_first():
    # The v2 rows carry no parsable added date, so the list's own order is the
    # only newest-first order My Tidal has; a name-sorted list would silently
    # turn "Recently added" alphabetical.
    server = CollectionServer(by_order={"DATE": ["zebra", "apple", "mango"]}, count=3)
    assert _ids(user_media_lists(server.session())["playlists"]) == ["zebra", "apple", "mango"]
    pages = [c for c in server.calls if c["includeOnly"] == "PLAYLIST"]
    assert [(c["order"], c["orderDirection"]) for c in pages] == [("DATE", "DESC")]


def test_a_short_date_pass_is_filled_by_a_name_pass():
    server = CollectionServer(by_order={"DATE": ["p1", "p2"], "NAME": ["p3", "p2", "p1"]}, count=3)
    ids = _ids(user_media_lists(server.session())["playlists"])
    assert ids == ["p1", "p2", "p3"], "the newest-first rows keep their place, the fill lands at the end"
    assert _orders(server) == {"DATE", "NAME"}


def test_a_complete_date_pass_costs_no_second_pass():
    server = CollectionServer(by_order={"DATE": ["p1", "p2"]}, count=2)
    user_media_lists(server.session())
    assert _orders(server) == {"DATE"}


def test_root_folders_do_not_count_as_missing_playlists():
    # TIDAL's total counts root folders too; the playlist pages never return them.
    server = CollectionServer(folders={"root": [("f1", "Folder")]}, by_order={"DATE": ["p1"]}, count=2)
    rows = user_media_lists(server.session())["playlists"]
    assert _ids(rows) == ["f1", "p1"], "folders first, then each playlist once"
    assert _orders(server) == {"DATE"}


def test_an_unreadable_count_still_lists_each_playlist_once():
    server = CollectionServer(by_order={"DATE": ["p1", "p1", "p2"]})
    session = server.session()

    def no_count():
        raise RuntimeError("count endpoint changed shape")

    session.user.favorites = SimpleNamespace(get_playlists_count=no_count)
    assert _ids(user_media_lists(session)["playlists"]) == ["p1", "p2"]
    assert _orders(server) == {"DATE"}


def test_an_unreadable_root_row_is_skipped_not_fatal():
    server = CollectionServer(by_order={"DATE": [playlist_row("x", broken=True), "p1"]}, count=1)
    assert _ids(user_media_lists(server.session())["playlists"]) == ["p1"]
