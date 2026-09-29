"""Issue #46: My Tidal > Playlists listed the same playlist two or three times.

tidalapi's ``playlists_paginated`` fetched its pages in parallel sorted by
creation date. Playlists created in one batch tie on that key and the server
broke the tie differently per page, so a playlist landed on several pages
while others fell off. The sweep now reads the pages in turn, newest first,
keeps each playlist once by id, and fills any shortfall with a name-order pass.
"""

from types import SimpleNamespace

from tidalapi.types import OrderDirection, PlaylistOrder

from waves.helper.tidal import user_media_lists


def _pl(pid: str) -> SimpleNamespace:
    return SimpleNamespace(id=pid, num_tracks=1)


class _Favorites:
    """A root collection whose pages come from ``pages_by_order``."""

    def __init__(self, pages_by_order: dict, count: int, folders: list | None = None):
        self._pages = pages_by_order
        self._count = count
        self._folders = folders or []
        self.calls: list[tuple[str, str, int]] = []

    def playlists(self, limit, offset, order, order_direction):
        self.calls.append((order.value, order_direction.value, offset))
        pages = self._pages.get(order, [])
        index = offset // limit
        return list(pages[index]) if index < len(pages) else []

    def playlist_folders(self, limit, offset, parent_folder_id):
        return list(self._folders) if offset == 0 else []

    def get_playlists_count(self):
        return self._count


def _session(favorites) -> SimpleNamespace:
    return SimpleNamespace(
        user=SimpleNamespace(favorites=favorites),
        mixes=lambda: SimpleNamespace(categories=[]),
    )


def _ids(rows) -> list[str]:
    return [str(r.id) for r in rows]


def _orders(favorites) -> set[str]:
    return {order for order, _, _ in favorites.calls}


def test_a_playlist_repeated_across_pages_is_listed_once():
    # Three pages where the tie-broken "1982" rides on every page.
    page1 = [_pl("1982")] + [_pl(f"a{i}") for i in range(49)]
    page2 = [_pl("1982")] + [_pl(f"b{i}") for i in range(49)]
    page3 = [_pl("1982"), _pl("c0")]
    favorites = _Favorites({PlaylistOrder.DateCreated: [page1, page2, page3]}, count=100)
    ids = _ids(user_media_lists(_session(favorites))["playlists"])
    assert ids.count("1982") == 1
    assert len(ids) == len(set(ids)) == 100


def test_the_default_order_stays_newest_first():
    # The v2 rows carry no parsable added date, so the list's own order is the
    # only newest-first order My Tidal has; a name-sorted list would silently
    # turn "Recently added" alphabetical.
    newest_first = [_pl("zebra"), _pl("apple"), _pl("mango")]
    favorites = _Favorites({PlaylistOrder.DateCreated: [newest_first]}, count=3)
    assert _ids(user_media_lists(_session(favorites))["playlists"]) == ["zebra", "apple", "mango"]
    assert favorites.calls == [("DATE", "DESC", 0)]


def test_a_short_date_pass_is_filled_by_a_name_pass():
    date_pages = [[_pl("p1"), _pl("p2")]]
    name_pages = [[_pl("p3"), _pl("p2"), _pl("p1")]]
    favorites = _Favorites({PlaylistOrder.DateCreated: date_pages, PlaylistOrder.Name: name_pages}, count=3)
    ids = _ids(user_media_lists(_session(favorites))["playlists"])
    assert ids == ["p1", "p2", "p3"], "the newest-first rows keep their place, the fill lands at the end"
    assert _orders(favorites) == {"DATE", "NAME"}


def test_a_complete_date_pass_costs_no_second_pass():
    favorites = _Favorites({PlaylistOrder.DateCreated: [[_pl("p1"), _pl("p2")]]}, count=2)
    user_media_lists(_session(favorites))
    assert _orders(favorites) == {"DATE"}


def test_a_short_page_in_the_middle_does_not_end_the_pass():
    # The server dropped an unavailable playlist from the second window: 50,
    # 49, then 21 rows. The old paginated call asked for every offset up to the
    # count; a pass that stopped at the 49 would lose the last 21.
    pages = [
        [_pl(f"a{i}") for i in range(50)],
        [_pl(f"b{i}") for i in range(49)],
        [_pl(f"c{i}") for i in range(21)],
    ]
    favorites = _Favorites({PlaylistOrder.DateCreated: pages}, count=120)
    ids = _ids(user_media_lists(_session(favorites))["playlists"])
    assert len(ids) == len(set(ids)) == 120
    assert _orders(favorites) == {"DATE"}


def test_root_folders_do_not_count_as_missing_playlists():
    # TIDAL's total counts root folders too; the playlist pages never return them.
    folder = SimpleNamespace(id="f1", name="Folder")
    favorites = _Favorites({PlaylistOrder.DateCreated: [[_pl("p1")]]}, count=2, folders=[folder])
    rows = user_media_lists(_session(favorites))["playlists"]
    assert _ids(rows) == ["f1", "p1"], "folders first, then each playlist once"
    assert _orders(favorites) == {"DATE"}


def test_a_server_that_ignores_the_offset_cannot_loop_forever():
    full = [_pl(f"p{i}") for i in range(50)]

    class _Stuck(_Favorites):
        def playlists(self, limit, offset, order, order_direction):
            self.calls.append((order.value, order_direction.value, offset))
            assert len(self.calls) < 20, "paging never stopped"
            return list(full)

    favorites = _Stuck({}, count=120)
    ids = _ids(user_media_lists(_session(favorites))["playlists"])
    assert len(ids) == len(set(ids)) == 50


def test_an_unreadable_count_still_lists_each_playlist_once():
    class _NoCount(_Favorites):
        def get_playlists_count(self):
            raise RuntimeError("count endpoint changed shape")

    favorites = _NoCount({PlaylistOrder.DateCreated: [[_pl("p1"), _pl("p1"), _pl("p2")]]}, count=0)
    assert _ids(user_media_lists(_session(favorites))["playlists"]) == ["p1", "p2"]
    assert _orders(favorites) == {"DATE"}


def test_pages_are_asked_in_the_direction_the_tab_shows():
    favorites = _Favorites({PlaylistOrder.DateCreated: [[_pl("p1")]]}, count=1)
    user_media_lists(_session(favorites))
    assert favorites.calls[0][1] == OrderDirection.Descending.value
