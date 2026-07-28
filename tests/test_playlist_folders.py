"""Folder tree sweep + {folder_path} handling (tidaler/helper/folders.py).

The fakes mirror the tidalapi surface the sweep touches: paged
favorites.playlist_folders(parent_folder_id=...) and Folder.items(). The
parent-path bookkeeping is asserted explicitly because tidalapi's parsed
Folder objects lie about their parent (parse() never sets parent_folder_id).
"""

from types import SimpleNamespace

import pytest
from tidalapi.exceptions import TooManyRequests

from tidaler.helper import folders as f


class FakePlaylist(SimpleNamespace):
    pass


class FakeFolder:
    def __init__(self, fid, name, playlists=None, items_error=None):
        self.id = fid
        self.name = name
        self._playlists = playlists or []
        self._items_error = items_error

    def items(self, offset=0, limit=50):
        if self._items_error is not None:
            raise self._items_error
        return self._playlists[offset : offset + limit]


class FakeSession:
    """children: dict parent_id -> list[FakeFolder]."""

    def __init__(self, children, raise_429_for=None):
        self._children = children
        self._raise_429_for = raise_429_for or set()
        self.user = SimpleNamespace(favorites=SimpleNamespace(playlist_folders=self._playlist_folders))

    def _playlist_folders(self, limit=50, offset=0, parent_folder_id="root"):
        if parent_folder_id in self._raise_429_for:
            raise TooManyRequests()
        kids = self._children.get(parent_folder_id, [])
        return kids[offset : offset + limit]


def _pl(pid):
    return FakePlaylist(id=pid)


def test_walk_builds_paths_and_map():
    blue = FakeFolder("f2", "Bluegrass", playlists=[_pl("p2"), _pl("p3")])
    country = FakeFolder("f1", "Country", playlists=[_pl("p1")])
    session = FakeSession({"root": [country], "f1": [blue], "f2": []})

    tree = f.walk_playlist_tree(session)

    assert not tree.partial
    assert [n.path for n in tree.nodes] == ["Country", "Country/Bluegrass"]
    assert tree.node_by_id("f2").parent_path == "Country"
    assert tree.node_by_id("f2").parent_id == "f1"
    assert tree.node_by_id("f1").subfolder_count == 1
    assert tree.playlist_paths == {"p1": "Country", "p2": "Country/Bluegrass", "p3": "Country/Bluegrass"}
    assert tree.folder_path_of("p2") == "Country/Bluegrass"
    assert tree.folder_path_of("unknown") == ""
    assert [n.id for n in tree.children_of("f1")] == ["f2"]
    assert [p.id for p in tree.playlists_under("f1")] == ["p1", "p2", "p3"]
    assert [p.id for p in tree.playlists_under("f2")] == ["p2", "p3"]
    assert tree.playlists_under("missing") == []


def test_walk_pages_past_50():
    many = [_pl(f"p{i}") for i in range(120)]
    top = FakeFolder("f1", "Big", playlists=many)
    session = FakeSession({"root": [top], "f1": []})

    tree = f.walk_playlist_tree(session)

    assert len(tree.node_by_id("f1").playlists) == 120
    assert len(tree.playlist_paths) == 120


def test_walk_reuses_prefetched_root_folders():
    top = FakeFolder("f1", "Country", playlists=[_pl("p1")])
    session = FakeSession({"f1": []})

    tree = f.walk_playlist_tree(session, root_folders=[top])

    assert tree.folder_path_of("p1") == "Country"


def test_walk_continues_past_an_unparsable_items_page_but_flags_the_tree():
    """One malformed item raises out of a whole page, so the sweep skips it and
    carries on. It must still flag the tree: the skip abandons every REMAINING
    page of that folder too, so a folder of 60 whose second page fails would
    otherwise cache as a complete folder of 50 (tile count, drill-in list,
    DOWNLOAD ALL and a green rollup all agreeing on the wrong number)."""
    broken = FakeFolder("f1", "Broken", items_error=KeyError("promotedArtists"))
    ok = FakeFolder("f2", "Fine", playlists=[_pl("p1")])
    session = FakeSession({"root": [broken, ok], "f1": [], "f2": []})

    tree = f.walk_playlist_tree(session)

    assert tree.partial
    assert tree.node_by_id("f1").playlists == []
    assert tree.folder_path_of("p1") == "Fine", "the rest of the sweep still ran"


def test_walk_429_returns_partial_tree():
    top = FakeFolder("f1", "Country", playlists=[_pl("p1")])
    session = FakeSession({"root": [top]}, raise_429_for={"f1"})

    tree = f.walk_playlist_tree(session)

    assert tree.partial
    assert tree.folder_path_of("p1") == ""  # walk stopped before items
    assert [n.path for n in tree.nodes] == ["Country"]


def test_walk_429_inside_items_is_partial_not_skip():
    top = FakeFolder("f1", "Country", items_error=TooManyRequests())
    session = FakeSession({"root": [top], "f1": []})

    tree = f.walk_playlist_tree(session)

    assert tree.partial


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Country/Bluegrass", "Country/Bluegrass"),
        ("", ""),
        ("/Country", "Country"),
        ("Country//Bluegrass/", "Country/Bluegrass"),
        ("a/../b", "a/b"),
        ("./x", "x"),
        ("Coun:try?", "Country"),
        ("back\\slash", "back/slash"),
        ("CON/x", "CON_/x"),
        ("Best of {artist_name}", "Best of (artist_name)"),
        ("{album_title}/{track_title}", "(album_title)/(track_title)"),
    ],
)
def test_sanitize_folder_path(raw, expected):
    assert f.sanitize_folder_path(raw) == expected


def test_a_folder_named_like_a_template_token_is_not_expanded():
    """The folder path is spliced in BEFORE format_path_media, and that
    formatter matches placeholders in the already-substituted string. A folder
    named "Best of {artist_name}" would therefore be read as a placeholder and
    expanded, scattering one playlist across a directory per artist. Verified
    end to end: the formatted path keeps the folder's own name."""
    from types import SimpleNamespace

    from tidalapi import Track

    from tidaler.helper.path import format_path_media

    template = f.apply_folder_path("Playlists/{folder_path}fixed", "Best of {artist_name}")
    assert "{artist_name}" not in template

    # A real Track: the formatter's token lookups are isinstance-gated, so a
    # stand-in would resolve nothing and the test would pass on its own.
    track = Track.__new__(Track)
    track.artists = [SimpleNamespace(name="Willie Nelson")]
    track.name = "On the Road Again"

    out = format_path_media(template, track)
    assert out == "Playlists/Best of (artist_name)/fixed"
    assert "Willie Nelson" not in out, "the folder's name was read as a placeholder and expanded"


def test_apply_folder_path_substitutes_with_trailing_slash():
    template = "Playlists/{folder_path}{playlist_name}/{list_pos}. {artist_name} - {track_title}"
    out = f.apply_folder_path(template, "Country/Bluegrass")
    assert out == "Playlists/Country/Bluegrass/{playlist_name}/{list_pos}. {artist_name} - {track_title}"


def test_apply_folder_path_empty_collapses_later():
    template = "Playlists/{folder_path}{playlist_name}/x"
    out = f.apply_folder_path(template, "")
    assert out == "Playlists/{playlist_name}/x"
    # pathlib collapses the seam either way; make sure we did not leave the token
    assert "{folder_path}" not in out


def test_apply_folder_path_token_absent_is_noop():
    template = "Playlists/{playlist_name}/x"
    assert f.apply_folder_path(template, "Country") == template


def test_apply_folder_path_hostile_value_cannot_escape():
    template = "Playlists/{folder_path}{playlist_name}/x"
    out = f.apply_folder_path(template, "/../..//etc")
    assert out == "Playlists/etc/{playlist_name}/x"


def test_apply_folder_path_leading_empty_does_not_go_absolute():
    """The token reference samples "Country/Bluegrass/", which invites putting
    the token first with a separator after it. For a playlist in no folder that
    used to render "/{playlist_name}/x", and Path(base) / "/x" DROPS base: the
    download would land at the filesystem root instead of the download folder.
    """
    template = "{folder_path}/{playlist_name}/x"
    assert f.apply_folder_path(template, "") == "{playlist_name}/x"
    # A non-empty path leaves the doubled seam this template always produced,
    # which pathlib collapses. Only the LEADING separator has to go.
    assert f.apply_folder_path(template, "Country") == "Country//{playlist_name}/x"


def test_apply_folder_path_leaves_a_deliberately_absolute_template_alone():
    """Only the separator the substitution exposed is stripped. A template the
    user wrote absolute behaves exactly as it did before folders existed."""
    template = "/Volumes/Music/{folder_path}{playlist_name}"
    assert f.apply_folder_path(template, "") == "/Volumes/Music/{playlist_name}"
