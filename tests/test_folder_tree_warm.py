"""Nothing may silently give up because the folder tree has not been swept yet.

The tree is written in exactly one place, the sweep inside ``_media_lists``, so
it is None on every cold start and again after a sign-out. Two paths failed
silently in that window:

1. DRILL-IN. Page cache v3 restores the playlists first page (folder rows and
   all) on a warm launch, so folder tiles are clickable seconds before any
   sweep runs. ``openPlaylistFolder`` answered a click in that window by
   emitting an empty payload, and the drill-in ListView has no empty state, no
   spinner and no retry: the view stayed blank until the user crumbed out and
   back in. ``downloadFolder`` already handled the same not-ready state
   explicitly ("Folder not loaded yet"); the open path did not.

2. DOWNLOAD. ``_playlist_template`` resolved ``{folder_path}`` to "" with no
   tree, which is correct for a playlist in no folder and wrong for one in a
   folder. Paste your own playlist's share link on a cold session and the
   tracks land in <base>/Playlists/Road Songs/; open My Tidal (the sweep runs)
   and download it again and they land in <base>/Playlists/Country/Road Songs/.
   check_file_exists only ever compares the destination it was handed, so
   nothing is skipped: a full second copy is written.
"""

from __future__ import annotations

from threading import Lock
from types import SimpleNamespace

from tidaler.helper.folders import FolderNode, FolderTree
from tidaler.waves_ui.backend import WavesBridge


class _Emit:
    def __init__(self):
        self.calls: list = []

    def emit(self, *args):
        self.calls.append(args)


class _InlinePool:
    """Runs the warm worker on the calling thread. The GUI hop is a plain
    signal, so with an inline pool the whole warm-then-replay is synchronous."""

    @staticmethod
    def start(worker):
        worker.fn()


class _WarmStub:
    _warm_folder_tree = WavesBridge._warm_folder_tree
    _on_folder_tree_warmed = WavesBridge._on_folder_tree_warmed
    _current_folder_tree = WavesBridge._current_folder_tree
    openPlaylistFolder = WavesBridge.openPlaylistFolder
    downloadPlaylist = WavesBridge.downloadPlaylist
    _playlist_template = WavesBridge._playlist_template
    _needs_folder_tree = WavesBridge._needs_folder_tree

    def __init__(self, tree=None, logged_in=True):
        self._folder_tree = None
        self._swept = tree
        self._media_lists_lock = Lock()
        self._logged_in = logged_in
        self._tree_warm_waiting: list = []
        self._tree_warm_inflight = False
        self.threadpool = _InlinePool()
        self.sweeps = 0
        self.busy: list = []
        self.playlistFolderLoaded = _Emit()
        self._folderTreeWarmed = SimpleNamespace(emit=self._on_folder_tree_warmed)
        self.settings = SimpleNamespace(data=SimpleNamespace(format_playlist="Playlists/{folder_path}{playlist_name}"))

    def _media_lists(self, refresh=True, walk=True):
        self.sweeps += 1
        with self._media_lists_lock:
            self._folder_tree = self._swept
        return {}, self._swept

    def _set_busy(self, on):
        self.busy.append(on)

    def _folder_dict(self, node, tree):
        return {"kind": "folder", "id": node.id}

    def _playlist_dict(self, pl):
        return {"kind": "playlist", "id": pl.id}


def _tree():
    t = FolderTree()
    t.nodes = [
        FolderNode(folder=None, id="f1", name="Country", path="Country", parent_path="", parent_id="root"),
        FolderNode(
            folder=None, id="f2", name="Bluegrass", path="Country/Bluegrass", parent_path="Country", parent_id="f1"
        ),
    ]
    t.nodes[0].playlists = [SimpleNamespace(id="p1")]
    t.playlist_paths = {"p1": "Country"}
    return t


def test_a_folder_opened_before_the_sweep_warms_and_then_fills():
    stub = _WarmStub(_tree())
    stub.openPlaylistFolder("f1")

    assert stub.sweeps == 1, "the click must trigger the sweep it needs"
    ((fid, rows, path),) = stub.playlistFolderLoaded.calls
    assert fid == "f1" and path == "Country"
    assert [r["id"] for r in rows] == ["f2", "p1"], "subfolder first, then the playlists"
    assert stub.busy == [True, False], "the wait is visible, and it ends"


def test_only_one_sweep_is_started_for_a_burst_of_clicks():
    """The warm parks callers rather than firing a sweep each. Simulated by
    holding the pool until both calls are in."""
    stub = _WarmStub(_tree())
    started: list = []
    stub.threadpool = SimpleNamespace(start=started.append)

    stub.openPlaylistFolder("f1")
    stub.openPlaylistFolder("f2")
    assert len(started) == 1
    assert len(stub._tree_warm_waiting) == 2

    started[0].fn()  # the sweep lands, both parked opens replay
    assert [c[0] for c in stub.playlistFolderLoaded.calls] == ["f1", "f2"]


def test_a_folder_that_is_really_gone_still_reports_empty_once():
    """Warming must not loop: the replayed open finds a tree (just not this
    node) and falls through to the old not-found emit."""
    stub = _WarmStub(_tree())
    stub.openPlaylistFolder("nope")

    assert stub.sweeps == 1
    assert stub.playlistFolderLoaded.calls == [("nope", [], "")]


def test_signed_out_keeps_the_old_behaviour():
    stub = _WarmStub(_tree(), logged_in=False)
    stub.openPlaylistFolder("f1")

    assert stub.sweeps == 0, "no sweep is possible signed out"
    assert stub.playlistFolderLoaded.calls == [("f1", [], "")]


def test_a_download_before_the_sweep_waits_for_the_folder_path():
    stub = _WarmStub(_tree())
    assert stub._needs_folder_tree() is True

    templates: list = []
    stub._objs = {"playlist": {"p1": SimpleNamespace(id="p1", name="Road Songs")}}
    stub.downloadState = _Emit()
    stub._download = lambda *a, **k: templates.append(a[3])
    stub._refetch_for_download = lambda *a: None
    WavesBridge.downloadPlaylist(stub, "p1")

    assert stub.sweeps == 1
    assert templates == ["Playlists/Country/{playlist_name}"], "the playlist's real folder, not the root"


def test_a_failed_sweep_does_not_loop_and_clears_the_button():
    """A sweep that fails leaves the tree None; replaying the parked callbacks
    into that would just re-warm, forever (each re-tests the same None). The
    callbacks are dropped instead, the button a parked download lit is cleared,
    and the user's next click is the retry."""
    stub = _WarmStub(None)  # the sweep "succeeds" but produces no tree
    stub.downloadState = _Emit()
    stub.statuses: list = []
    stub._set_status = stub.statuses.append

    replays: list = []
    started = stub._warm_folder_tree(lambda: replays.append(1), "p1")

    assert started is True and stub.sweeps == 1, "one sweep, no re-warm loop"
    assert replays == [], "the callback must not replay into a missing tree"
    assert stub._tree_warm_waiting == []
    assert stub.downloadState.calls == [("p1", "")], "the lit download button returns to idle"
    assert stub.statuses == ["Could not load your playlist folders, try again"]


def test_a_template_without_the_placeholder_never_waits():
    """Most users never touch the template: they must not pay a sweep for a
    value their path does not use."""
    stub = _WarmStub(_tree())
    stub.settings.data.format_playlist = "Playlists/{playlist_name}/{track_title}"
    assert stub._needs_folder_tree() is False

    templates: list = []
    stub._objs = {"playlist": {"p1": SimpleNamespace(id="p1", name="Road Songs")}}
    stub.downloadState = _Emit()
    stub._download = lambda *a, **k: templates.append(a[3])
    stub._refetch_for_download = lambda *a: None
    WavesBridge.downloadPlaylist(stub, "p1")

    assert stub.sweeps == 0
    assert templates == ["Playlists/{playlist_name}/{track_title}"]
