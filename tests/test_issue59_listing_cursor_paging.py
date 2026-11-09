"""Issue #59: the Playlists page sat on "Loading library" for hours.

TIDAL's v2 playlist-folder listing ignores ``offset`` and pages by ``cursor``.
The folder walk paged by offset and stopped only on a short page, so a folder
of 50 or more playlists (or subfolders) returned the same full page forever.
The root listings had the same blind spot without the endless loop: past the
first 50 rows they silently came up short ("72 of 100" in the reporter's log).

_collection_server ignores offset and pages by cursor, as the live server does.
"""

from __future__ import annotations

from _collection_server import CollectionServer

from waves.helper import collection_paging
from waves.helper.folders import walk_playlist_tree
from waves.helper.tidal import user_media_lists


def _ids(rows) -> list[str]:
    return [str(r.id) for r in rows]


def test_a_folder_of_more_than_50_playlists_finishes_with_every_playlist():
    server = CollectionServer(folders={"root": [("f1", "Big")]}, playlists={"f1": [f"p{i}" for i in range(137)]})

    tree = walk_playlist_tree(server.session())

    assert not tree.partial
    assert _ids(tree.node_by_id("f1").playlists) == [f"p{i}" for i in range(137)]
    item_calls = [c for c in server.calls if c["folderId"] == "f1" and c["includeOnly"] == "PLAYLIST"]
    assert len(item_calls) == 3, "three pages of 50, then the listing ends"


def test_a_folder_of_exactly_50_playlists_ends_after_one_check():
    # A full page with no cursor gets one more ask (the offset fallback); the
    # server hands the same page back, which ends the pass.
    server = CollectionServer(folders={"root": [("f1", "Fifty")]}, playlists={"f1": [f"p{i}" for i in range(50)]})

    tree = walk_playlist_tree(server.session())

    assert len(tree.node_by_id("f1").playlists) == 50
    assert sum(1 for c in server.calls if c["folderId"] == "f1" and c["includeOnly"] == "PLAYLIST") == 2


def test_more_than_50_subfolders_are_all_walked():
    subs = [(f"s{i}", f"Sub {i}") for i in range(75)]
    server = CollectionServer(folders={"root": [("f1", "Top")], "f1": subs})

    tree = walk_playlist_tree(server.session())

    assert tree.node_by_id("f1").subfolder_count == 75
    assert len(tree.nodes) == 76


def test_root_lists_more_than_50_folders_and_playlists():
    server = CollectionServer(
        folders={"root": [(f"f{i}", f"F{i}") for i in range(60)]},
        playlists={"root": [f"p{i}" for i in range(140)]},
    )

    rows = user_media_lists(server.session())["playlists"]

    assert _ids(rows) == [f"f{i}" for i in range(60)] + [f"p{i}" for i in range(140)]


def test_a_cursor_on_the_last_page_ends_on_the_empty_page_after_it():
    # The root listing always carries a cursor key; the empty page ends it.
    server = CollectionServer(
        folders={"root": [("f1", "Top")]}, playlists={"f1": [f"p{i}" for i in range(60)]}, cursor_on_last_page=True
    )

    tree = walk_playlist_tree(server.session())

    assert len(tree.node_by_id("f1").playlists) == 60
    assert not tree.partial


def test_a_server_that_never_moves_cannot_loop_forever():
    # A cursor that never advances, on a full page, is the #59 hang in another
    # shape: the pass must stop on the repeat, not keep asking.
    server = CollectionServer(
        folders={"root": [("f1", "Stuck")]}, playlists={"f1": [f"p{i}" for i in range(50)]}, stuck_cursor=True
    )

    tree = walk_playlist_tree(server.session())

    assert len(tree.node_by_id("f1").playlists) == 50
    assert sum(1 for c in server.calls if c["folderId"] == "f1" and c["includeOnly"] == "PLAYLIST") <= 3


def test_offset_ignored_with_no_cursor_stops_after_one_repeat():
    # Should the cursor ever vanish from a full page, the pager falls back to
    # offset; a server ignoring that too repeats the page, which ends the pass.
    server = CollectionServer(folders={"root": [("f1", "Top")]}, playlists={"f1": [f"p{i}" for i in range(80)]})
    real = server.request

    def no_cursor(method, endpoint, base_url=None, params=None):
        response = real(method, endpoint, base_url=base_url, params=params)
        payload = response.json()
        payload.pop("cursor", None)
        response.json = lambda: payload
        return response

    session = server.session()
    session.request.request = no_cursor

    tree = walk_playlist_tree(session)

    assert len(tree.node_by_id("f1").playlists) == 50
    assert sum(1 for c in server.calls if c["folderId"] == "f1" and c["includeOnly"] == "PLAYLIST") == 2


def test_the_page_cap_marks_the_tree_partial(monkeypatch):
    monkeypatch.setattr(collection_paging, "MAX_PAGES", 2)
    server = CollectionServer(folders={"root": [("f1", "Huge")]}, playlists={"f1": [f"p{i}" for i in range(130)]})

    tree = walk_playlist_tree(server.session())

    assert len(tree.node_by_id("f1").playlists) == 100
    assert tree.partial, "a capped folder must not cache as complete"


def test_an_unreadable_root_folder_flags_the_sweep_short():
    server = CollectionServer(folders={"root": [("f1", "Good"), ("f2", "Bad")]}, playlists={"root": ["p1"]})
    session = server.session()
    parse = session.parse_folder

    def picky(item):
        if item["data"]["id"] == "f2":
            raise ValueError("row changed shape")
        return parse(item)

    session.parse_folder = picky

    lists = user_media_lists(session)

    assert _ids(lists["playlists"]) == ["f1", "p1"]
    assert lists["folders_short"], "a dropped root folder must not read as a complete listing"


def test_a_complete_root_listing_is_not_short():
    server = CollectionServer(folders={"root": [("f1", "Top")]}, playlists={"root": ["p1"]})
    assert user_media_lists(server.session())["folders_short"] is False
