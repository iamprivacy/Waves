"""Folder "download all" rollup: track-weighted aggregate under the folder id,
badge countdown (folderRemaining), and the pre-formatter {folder_path}
resolution for individual playlist downloads.

Same hermetic pattern as test_group_progress.py: the real unbound methods are
bound onto a minimal stub, no Qt app or network session.
"""

from __future__ import annotations

from threading import Lock
from types import SimpleNamespace

from tidaler.helper.folders import FolderNode, FolderTree
from tidaler.waves_ui.backend import WavesBridge


class _Signal:
    def __init__(self):
        self.emits: list = []

    def emit(self, *args):
        self.emits.append(args if len(args) != 1 else args[0])


class _OrderedSignal(_Signal):
    """Records into a shared log as well as its own, so the interleaving of two
    signals can be asserted."""

    def __init__(self, tag: str, log: list):
        super().__init__()
        self._tag = tag
        self._log = log

    def emit(self, *args):
        super().emit(*args)
        self._log.append(self._tag)


class _BumpStub:
    _bump_folder_group = WavesBridge._bump_folder_group

    def __init__(self, group: dict, extra: dict | None = None):
        self._folder_groups = {"fold1": group}
        self._folder_groups.update(extra or {})
        self._folder_lock = Lock()
        self._scan_gen = 0
        self.downloadProgress = _Signal()
        self.downloadState = _Signal()
        self.folderRemaining = _Signal()


def _group(keys=("p1", "p2"), weights=None, total=None):
    return {
        "keys": set(keys),
        "done": set(),
        "failed": set(),
        "prog": {},
        "weights": weights or dict.fromkeys(keys, 1),
        "total": total if total is not None else len(keys),
    }


def test_progress_is_track_weighted():
    stub = _BumpStub(_group(weights={"p1": 30, "p2": 10}))
    stub._bump_folder_group("p1", 50.0, None)
    # 50% of a 30-track playlist = 37.5% of the 40-track folder
    assert stub.downloadProgress.emits == [("fold1", 37.5)]
    assert stub.downloadState.emits == [("fold1", "running")]
    assert stub.folderRemaining.emits == []  # progress ticks don't touch the badge


def test_done_ticks_the_badge_down():
    stub = _BumpStub(_group())
    stub._bump_folder_group("p1", None, "done")
    assert stub.folderRemaining.emits == [("fold1", 1, 2)]
    assert ("fold1", "running") in stub.downloadState.emits


def test_all_done_finishes_and_drops_the_group():
    stub = _BumpStub(_group())
    stub._bump_folder_group("p1", None, "done")
    stub._bump_folder_group("p2", None, "done")
    assert stub.folderRemaining.emits[-1] == ("fold1", 0, 2)
    assert stub.downloadState.emits[-1] == ("fold1", "done")
    assert stub.downloadProgress.emits[-1] == ("fold1", 100.0)
    assert stub._folder_groups == {}


def test_any_failed_member_fails_the_folder():
    stub = _BumpStub(_group())
    stub._bump_folder_group("p1", None, "done")
    stub._bump_folder_group("p2", None, "failed")
    assert stub.downloadState.emits[-1] == ("fold1", "failed")
    assert stub.folderRemaining.emits[-1] == ("fold1", 0, 2)
    assert stub._folder_groups == {}


def test_non_member_is_a_noop():
    stub = _BumpStub(_group())
    stub._bump_folder_group("elsewhere", 40.0, None)
    assert stub.downloadProgress.emits == []
    assert stub.downloadState.emits == []


def _tree_with(paths: dict[str, str]) -> FolderTree:
    tree = FolderTree()
    tree.playlist_paths = dict(paths)
    return tree


class _TemplateStub:
    _playlist_template = WavesBridge._playlist_template

    def __init__(self, tree):
        self._tree = tree
        self.settings = SimpleNamespace(
            data=SimpleNamespace(
                format_playlist="Playlists/{folder_path}{playlist_name}/{list_pos}. {artist_name} - {track_title}"
            )
        )

    def _current_folder_tree(self):
        return self._tree


def test_playlist_template_mirrors_the_folder():
    stub = _TemplateStub(_tree_with({"p1": "Country/Bluegrass"}))
    out = stub._playlist_template("p1")
    assert out.startswith("Playlists/Country/Bluegrass/{playlist_name}/")
    assert "{folder_path}" not in out


def test_playlist_template_root_and_cold_session_fall_back_clean():
    for tree in (_tree_with({}), None):
        out = _TemplateStub(tree)._playlist_template("p1")
        assert out == "Playlists/{playlist_name}/{list_pos}. {artist_name} - {track_title}"


class _DownloadFolderStub:
    downloadFolder = WavesBridge.downloadFolder
    _playlist_template = WavesBridge._playlist_template
    _ffmpeg_gate_holds = WavesBridge._ffmpeg_gate_holds

    def __init__(self, tree, gate="ok", ffmpeg="system"):
        self._tree = tree
        self.gate = gate
        self.ffmpeg = ffmpeg
        self._ffmpeg_gate_bypassed = False
        self.ffmpegMissingBlocked = _Signal()
        self._folder_groups = {}
        self._folder_lock = Lock()
        self._scan_gen = 0
        self._objs = {"playlist": {}}
        self._pending_lock = Lock()
        self._pending_downloads: list = []
        self._queue_emit_suspended = False
        # Shared, so the ORDER of two different signals is observable.
        self.order: list = []
        self.downloadProgress = _Signal()
        self.downloadState = _OrderedSignal("state", self.order)
        self.folderRemaining = _OrderedSignal("badge", self.order)
        self.statuses: list = []
        self.downloads: list = []
        self.settings = SimpleNamespace(
            data=SimpleNamespace(format_playlist="Playlists/{folder_path}{playlist_name}/x")
        )

    def _current_folder_tree(self):
        return self._tree

    def _download_gate(self):
        return self.gate

    def _ffmpeg_source_label(self):
        return self.ffmpeg

    _stash_pending_download = WavesBridge._stash_pending_download

    def _remember(self, bucket, key, obj):
        self._objs[bucket][key] = obj

    def _set_status(self, text):
        self.statuses.append(text)

    def _emit_queue(self):
        pass

    def _download(self, obj, type_media, name, template, collection, media_id):
        self.downloads.append((media_id, type_media, template, collection))


def _playlist(pid, name, tracks):
    return SimpleNamespace(id=pid, name=name, num_tracks=tracks, num_videos=0)


def _folder_tree() -> FolderTree:
    tree = FolderTree()
    country = FolderNode(folder=None, id="f1", name="Country", path="Country", parent_path="", parent_id="root")
    blue = FolderNode(
        folder=None, id="f2", name="Bluegrass", path="Country/Bluegrass", parent_path="Country", parent_id="f1"
    )
    country.playlists = [_playlist("p1", "Road Songs", 12)]
    blue.playlists = [_playlist("p2", "Pickin'", 30)]
    tree.nodes = [country, blue]
    tree.playlist_paths = {"p1": "Country", "p2": "Country/Bluegrass"}
    return tree


def test_download_folder_is_recursive_weighted_and_mirrored():
    stub = _DownloadFolderStub(_folder_tree())
    stub.downloadFolder("f1")

    grp = stub._folder_groups["f1"]
    assert grp["keys"] == {"p1", "p2"}
    assert grp["weights"] == {"p1": 12, "p2": 30}
    assert grp["total"] == 2
    assert stub.folderRemaining.emits == [("f1", 2, 2)]
    assert stub.downloadState.emits == [("f1", "running")]
    # BADGE FIRST, the order downloadPlaylistCategory documents. The badge is
    # hidden while the button is idle and the count map is never pruned between
    # runs, so announcing "running" first shows the PREVIOUS run's count (or its
    # finished checkmark) until the count lands, and the odometer then rolls
    # away from a number that was never true for this run.
    assert stub.order == ["badge", "state"]

    templates = {mid: tpl for mid, _t, tpl, _c in stub.downloads}
    assert templates["p1"] == "Playlists/Country/{playlist_name}/x"
    assert templates["p2"] == "Playlists/Country/Bluegrass/{playlist_name}/x"
    assert all(c is True for _m, _t, _tpl, c in stub.downloads)
    assert stub._queue_emit_suspended is False  # restored after the batch


def test_download_folder_without_tree_or_playlists_is_a_noop():
    stub = _DownloadFolderStub(None)
    stub.downloadFolder("f1")
    assert stub.downloads == []
    assert stub._folder_groups == {}

    empty = FolderTree()
    empty.nodes = [FolderNode(folder=None, id="f9", name="Empty", path="Empty", parent_path="", parent_id="root")]
    stub = _DownloadFolderStub(empty)
    stub.downloadFolder("f9")
    assert stub.downloads == []
    assert stub._folder_groups == {}


def test_every_overlapping_group_gets_credited():
    """A playlist can sit in two LIVE rollups at once: playlists_under() is
    recursive, so a parent folder's key set contains its subfolder's, and a
    Browse "cat:" rollup shares no membership rule with the library folders at
    all. Crediting only the first match left the other group permanently one
    member short: never finished, never deleted, button stuck at "running"."""
    parent = _group(keys=("p1", "p2"))  # folder "Country" (recursive)
    child = _group(keys=("p2",))  # its subfolder "Bluegrass"
    stub = _BumpStub(parent, {"fold2": child})

    stub._bump_folder_group("p2", None, "done")
    # Both groups ticked. The child held only p2, so it finished and was dropped.
    assert ("fold1", 1, 2) in stub.folderRemaining.emits
    assert ("fold2", 0, 1) in stub.folderRemaining.emits
    assert ("fold2", "done") in stub.downloadState.emits
    assert "fold2" not in stub._folder_groups

    stub._bump_folder_group("p1", None, "done")
    assert ("fold1", "done") in stub.downloadState.emits
    assert stub._folder_groups == {}  # no group leaked


def test_a_blocked_download_folder_publishes_no_rollup_state():
    """With nowhere to save to, _download rejects every member, so no queue row
    ever exists to tick the group back down. Publishing the group first left the
    folder button unclickable at "running" with a frozen badge for the rest of
    the session (only stopAll clears a group, and STOP is hidden while the queue
    is empty). Pre-gate like downloadArtist: publish nothing."""
    stub = _DownloadFolderStub(_folder_tree(), gate="block")
    stub.downloadFolder("f1")
    assert stub._folder_groups == {}
    assert stub.downloads == []
    assert stub.downloadState.emits == []
    assert stub.folderRemaining.emits == []


def test_a_nudged_download_folder_stashes_the_whole_rollup():
    """The default-folder nudge holds the download for replay after the user
    decides. The whole folder is stashed as ONE retry, so the rollup is
    registered once, on replay, instead of stranding a live group behind a
    dialog the user can dismiss."""
    stub = _DownloadFolderStub(_folder_tree(), gate="nudge")
    stub.downloadFolder("f1")
    assert stub._folder_groups == {}
    assert stub.downloads == []
    assert [mid for mid, _fn in stub._pending_downloads] == ["f1"]

    # Replaying it once the folder is resolved queues the real thing.
    stub.gate = "ok"
    stub._pending_downloads[0][1]()
    assert sorted(mid for mid, _t, _tpl, _c in stub.downloads) == ["p1", "p2"]
    assert "f1" in stub._folder_groups


def test_a_missing_ffmpeg_stashes_the_whole_rollup_before_any_state():
    """The FFmpeg gate would reject every member inside _download, so no queue
    row would ever tick the group down: the pre-gate must hold the WHOLE
    rollup before any state (button, badge, group) is published."""
    stub = _DownloadFolderStub(_folder_tree(), ffmpeg="none")
    stub.downloadFolder("f1")
    assert stub._folder_groups == {}
    assert stub.downloads == []
    assert stub.downloadState.emits == [] and stub.folderRemaining.emits == []
    assert stub.ffmpegMissingBlocked.emits == [()]
    assert [mid for mid, _fn in stub._pending_downloads] == ["f1"]

    # "Continue anyway" replays the stash and the rollup registers normally.
    stub.ffmpeg = "system"
    stub._pending_downloads[0][1]()
    assert sorted(mid for mid, _t, _tpl, _c in stub.downloads) == ["p1", "p2"]
    assert "f1" in stub._folder_groups


def test_lib_count_excludes_folder_rows():
    rows = [{"kind": "folder"}, {"kind": "playlist"}, {"kind": "playlist"}]
    assert WavesBridge._lib_count("playlists", rows) == 2
    assert WavesBridge._lib_count("albums", rows) == 3
