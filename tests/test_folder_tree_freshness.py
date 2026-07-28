"""The cached folder tree must stay authoritative, and stay paired with its sweep.

TWO BUGS FENCED OFF HERE
-----------------------
1. A rate-limited sweep returns what it managed to walk (``tree.partial``).
   That partial tree used to be cached as authoritative, which made the
   unwalked folders (and every playlist inside them) vanish from My Tidal and
   resolved ``{folder_path}`` to "" for them, so their downloads landed outside
   their folder, silently, past skip_existing.

2. The playlists page interleaves folder rows with playlists BY INDEX
   (``full[i - len(folder_rows)]``). ``len(folder_rows)`` was re-read from the
   live tree on every page call while page 1 stayed cached, so any change in
   the root-folder count between two page fetches shifted the window and
   skipped a playlist with no error and no visible gap. The tree is now
   returned alongside the listing it was swept with, and the mixes tab (which
   has no use for the tree, and whose walk was the thing most likely to trip a
   rate limit) no longer re-walks it at all.
"""

from __future__ import annotations

from threading import Lock
from types import SimpleNamespace
from unittest.mock import MagicMock

from tidaler.helper.folders import FolderNode, FolderTree
from tidaler.waves_ui import backend
from tidaler.waves_ui.backend import WavesBridge


def _tree(names, partial=False):
    t = FolderTree()
    t.nodes = [
        FolderNode(folder=None, id=f"f{i}", name=n, path=n, parent_path="", parent_id="root")
        for i, n in enumerate(names)
    ]
    t.partial = partial
    return t


def _bridge(monkeypatch, trees, sweeps=None):
    """Bridge whose folder walk hands back `trees` one per call."""
    b = WavesBridge.__new__(WavesBridge)
    b._media_lists_cache = None
    b._media_lists_lock = Lock()
    b._folder_tree = None
    b.tidal = MagicMock()
    calls = {"sweep": 0, "walk": 0}

    def fake_sweep(session):
        calls["sweep"] += 1
        return sweeps or {"playlists": [], "mixes": []}

    def fake_walk(session, root_folders=None):
        calls["walk"] += 1
        return trees.pop(0)

    monkeypatch.setattr(backend, "user_media_lists", fake_sweep)
    monkeypatch.setattr(backend, "walk_playlist_tree", fake_walk)
    return b, calls


def test_a_partial_sweep_does_not_replace_a_complete_tree(monkeypatch):
    good = _tree(["Country", "Jazz", "Rock"])
    cut = _tree(["Country"], partial=True)
    b, _calls = _bridge(monkeypatch, [good, cut])

    _lists, tree = b._media_lists(refresh=True)
    assert tree is good

    # Age the cache so the next first-page load re-sweeps, and let that sweep
    # get rate limited half way through.
    ts, data, t = b._media_lists_cache
    b._media_lists_cache = (ts - WavesBridge._MEDIA_LISTS_TTL - 1, data, t)
    _lists, tree = b._media_lists(refresh=True)
    assert tree is good, "a rate-limited walk must not drop folders from the cached tree"
    assert b._folder_tree is good


def test_a_partial_tree_is_kept_when_there_is_nothing_better(monkeypatch):
    cut = _tree(["Country"], partial=True)
    b, _calls = _bridge(monkeypatch, [cut])
    _lists, tree = b._media_lists(refresh=True)
    assert tree is cut  # something beats nothing on a cold session


def test_a_complete_sweep_replaces_a_partial_one(monkeypatch):
    cut = _tree(["Country"], partial=True)
    good = _tree(["Country", "Jazz"])
    b, _calls = _bridge(monkeypatch, [cut, good])
    b._media_lists(refresh=True)
    ts, data, t = b._media_lists_cache
    b._media_lists_cache = (ts - WavesBridge._MEDIA_LISTS_TTL - 1, data, t)
    _lists, tree = b._media_lists(refresh=True)
    assert tree is good


def test_the_mixes_tab_does_not_walk_the_folder_tree(monkeypatch):
    """One request per folder for a tab that renders only mixes, and the result
    was assigned over the tree the playlists page depends on."""
    good = _tree(["Country", "Jazz"])
    b, calls = _bridge(monkeypatch, [good], sweeps={"playlists": [], "mixes": ["m1", "m2"]})
    b._lib_sort = {}
    b._sort_local_library = lambda full, spec: full
    b._mix_dict = lambda m: {"id": m}

    b._media_lists(refresh=True)  # playlists tab: walks
    assert calls["walk"] == 1

    ts, data, t = b._media_lists_cache
    b._media_lists_cache = (ts - WavesBridge._MEDIA_LISTS_TTL - 1, data, t)
    WavesBridge._library_page(b, "mixes", 0, 1)  # mixes tab past the TTL: re-sweeps
    assert calls["sweep"] == 2
    assert calls["walk"] == 1, "the mixes tab must not pay for (or clobber) the folder walk"
    assert b._folder_tree is good


def test_the_playlists_page_windows_against_its_own_sweeps_tree(monkeypatch):
    """The folder rows and the playlist slice must come from ONE sweep: a newer
    tree with a different root count shifts `full[i - len(folder_rows)]`."""
    tree = _tree(["Country"])
    playlists = [SimpleNamespace(id=f"p{i}", name=f"P{i}", num_tracks=1, num_videos=0) for i in range(4)]
    b, _calls = _bridge(monkeypatch, [tree], sweeps={"playlists": playlists, "mixes": []})
    b._lib_sort = {}
    b._sort_local_library = lambda full, spec: full
    b._folder_dict = lambda n, t: {"kind": "folder", "id": n.id}
    b._playlist_dict = lambda p: {"kind": "playlist", "id": p.id}

    page0, more0 = WavesBridge._library_page(b, "playlists", 0, 3)
    # A concurrent sweep from elsewhere swaps in a tree with a different root
    # count. The next scroll page must still window against the paired tree.
    b._folder_tree = _tree(["Country", "Jazz", "Rock"])
    page1, more1 = WavesBridge._library_page(b, "playlists", 3, 3)

    ids = [r["id"] for r in page0 + page1]
    assert ids == ["f0", "p0", "p1", "p2", "p3"], "the page window skipped or repeated a playlist"
    assert more0 is True and more1 is False
